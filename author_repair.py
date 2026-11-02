"""Review and restore only Writer values; never run a cover or library scan."""
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import uuid

STATE_KEY = 'rabbit_plugins:author-repair:state:v1'
ENTRY_PREFIX = 'rabbit_plugins:author-repair:entry:v1:'
JOURNAL_PREFIX = 'rabbit_plugins:author-repair:journal:v1:'
READ_TIMEOUT = 20


def _json(value, default=None):
    if isinstance(value, dict) and 'value' in value:
        value = value['value']
    try:
        result = json.loads(value or '{}')
        return result if isinstance(result, dict) else (default or {})
    except (ValueError, TypeError):
        return default or {}


def _save(gateway, key, value):
    gateway.set_setting(key, json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=str))


def _text(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def _same_author(left, right):
    return _text(left).casefold() == _text(right).casefold()


def _valid_author(value):
    return bool(value) and len(value) <= 500 and value.casefold() not in {
        '-', 'unknown', '미상', '작가 미상', '알 수 없음'}


def _credits(value):
    return {part.strip().casefold() for part in str(value or '').split(',') if part.strip()}


def _suspect(row):
    author = str(row.get('author') or '')
    # Language only selects books to compare. It is never evidence for a write.
    # A Korean editorial credit such as "챔프D 편집부" is not a romanized
    # author merely because its magazine name contains one Latin letter.
    if re.search(r'[A-Za-z]', author) and not re.search(r'[가-힣]', author):
        return True
    writers = _credits(author)
    other_roles = _credits(row.get('cover_artist')) | _credits(row.get('translator'))
    return len(writers) > 1 and (bool(writers & other_roles) or bool(re.search(r'[A-Za-z]', author)))


def _read_embedded(row):
    from tools.scanner.embedded_metadata import read_embedded_metadata
    result = []

    def read():
        try:
            result.append(read_embedded_metadata(row['file_path'], row.get('file_format')) or {})
        except Exception:
            result.append({})

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    thread.join(READ_TIMEOUT)
    if thread.is_alive():
        return {}, True
    return result[0] if result else {}, False


def _evidence(provider, row, config, remote_cache):
    from . import rabbit_plugins as rp
    embedded, timed_out = _read_embedded(row)
    author = _text(embedded.get('author'))
    source = 'EPUB 내장 메타데이터' if row.get('file_format') == 'epub' else 'ComicInfo.xml'
    # Some older archives already merged a romanized illustrator into Writer.
    # Only verified product credits may resolve this; an English pen name alone
    # never triggers an external spelling replacement.
    role_review = (not re.search(r'[가-힣]', author)
                   and len([v for v in author.split(',') if v.strip()]) > 1)
    if _valid_author(author) and _same_author(author, row.get('author')) and not role_review:
        return '', source, '', '내장 글작가와 일치하므로 유지합니다.', timed_out
    original_writers = _credits(author)
    current_writers = _credits(row.get('author'))
    original_other_roles = _credits(embedded.get('cover_artist')) | _credits(embedded.get('translator'))
    extra_credits = current_writers - original_writers
    verified_role_cleanup = (bool(original_writers) and original_writers < current_writers
                             and extra_credits <= original_other_roles)
    if _valid_author(author) and (re.search(r'[가-힣]', author) or verified_role_cleanup):
        return author, source, '', '내장 Writer 값과 현재 글작가가 다릅니다.', timed_out
    # An existing, verified Ridi product can recover credits missing from the
    # archive. All HTTP goes through the shared throttle and cooldown handler.
    url = rp._source_link(row.get('link'), 'ridi')
    if url and 'ridi' in rp._metadata_source_order(config):
        if remote_cache.get('_blocked'):
            return '', '', url, '리디 조회 제한으로 추가 비교를 보류했습니다.', timed_out
        if url not in remote_cache:
            remote_cache[url] = rp._remote_fetch_metadata(url, 'ridi')
        remote = remote_cache[url]
        if remote.get('_fetch_error'):
            remote_cache['_blocked'] = True
            return '', '', url, '리디 조회 실패로 기존 값을 유지합니다.', timed_out
        query, _ = rp._metadata_book_folder_identity(row.get('series_name'), row.get('content_kind'))
        title = remote.get('title')
        if rp._metadata_content_kind(row.get('content_kind')) == 'manga':
            title = re.sub(r'\s*[@＠]comic\s*$', '', str(title or ''), flags=re.I)
        if not rp._metadata_title_matches(query, title):
            return '', '', url, '리디 링크의 작품 제목이 일치하지 않아 유지합니다.', timed_out
        remote_author = _text(remote.get('author'))
        if _valid_author(remote_author) and not _same_author(remote_author, row.get('author')):
            return remote_author, '리디', url, '기존 리디 링크의 글작가를 확인했습니다.', timed_out
    if author:
        reason = ('내장 글작가와 일치하므로 유지합니다.' if _same_author(author, row.get('author'))
                  else '내장 Writer만으로 변경 근거를 확정하지 못해 유지합니다.')
        return '', source, '', reason, timed_out
    return '', '', '', '원본 글작가를 확인하지 못해 유지합니다.', timed_out


def _entry_key(run_id, book_id):
    return f'{ENTRY_PREFIX}{run_id}:{int(book_id)}'


def _entries(gateway, run_id):
    if not re.fullmatch(r'[0-9a-f]{32}', str(run_id or '')):
        return []
    rows = gateway.fetch_all('SELECT `key`,value FROM settings WHERE `key` LIKE ?',
                             (ENTRY_PREFIX + run_id + ':%',))
    return [entry for row in rows if _valid_entry(entry := _json(row.get('value')), run_id)]


def _valid_entry(entry, run_id):
    return (entry.get('run_id') == run_id and isinstance(entry.get('book_id'), int)
            and not isinstance(entry['book_id'], bool) and entry['book_id'] > 0
            and isinstance(entry.get('library_id'), int) and entry['library_id'] > 0
            and isinstance(entry.get('series_name'), str) and bool(entry['series_name'])
            and isinstance(entry.get('current_author'), str)
            and isinstance(entry.get('proposed_author'), str))


def _lock_path(db_type):
    return Path(tempfile.gettempdir()) / f'rabbit-author-repair-{os.getuid()}-{db_type}.lock'


def _items(entries, db_type):
    groups = {}
    for entry in entries:
        if not entry.get('book_id') or not entry.get('series_name'):
            continue
        key = (entry['library_id'], entry['series_name'])
        groups.setdefault(key, []).append(entry)
    result = []
    for (_library, _series), rows in groups.items():
        repairable = [row for row in rows if row.get('status') == 'repairable']
        applied = [row for row in rows if row.get('status') == 'applied']
        first = (repairable or rows)[0]
        result.append({
            'db_type': db_type, 'book_id': first['book_id'],
            'library_id': first['library_id'], 'series_name': first['series_name'],
            'library_name': first.get('library_name', ''),
            'book_count': len(repairable) if repairable else len(rows),
            'current_author': ' / '.join(dict.fromkeys(row['current_author'] for row in rows)),
            'proposed_author': ' / '.join(dict.fromkeys(row['proposed_author'] for row in rows if row.get('proposed_author'))),
            'source': ' / '.join(dict.fromkeys(row.get('source') or '' for row in rows if row.get('source'))),
            'source_url': first.get('source_url', ''),
            'reason': first.get('reason', ''),
            'status': 'repairable' if repairable else 'applied' if applied else first.get('status', 'unchanged'),
        })
    return sorted(result, key=lambda item: (item['status'] != 'repairable', item['library_name'], item['series_name']))


def status(provider, db_type, context=None):
    gateway = provider.get_db_gateway(db_type)
    state = _json(gateway.get_setting(STATE_KEY))
    if state.get('status') == 'running':
        import fcntl
        with _lock_path(db_type).open('a+b') as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                state['status'] = 'interrupted'
                state.setdefault('warnings', []).append('비교 작업이 중단되었습니다. 다시 찾기를 실행해 주세요.')
                _save(gateway, STATE_KEY, state)
    return {'success': True, 'status': 'idle', **state,
            'items': _items(_entries(gateway, state.get('run_id')), db_type)}


def scan(provider, db_type, context=None):
    import fcntl
    gateway = provider.get_db_gateway(db_type)
    lock = _lock_path(db_type).open('a+b')
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return status(provider, db_type)
    state = {'run_id': uuid.uuid4().hex, 'status': 'running', 'checked': 0,
             'total': 0, 'warnings': [], 'started_at': time.time()}
    try:
        _save(gateway, STATE_KEY, state)
    except Exception:
        lock.close()
        raise

    def worker():
        try:
            _scan(provider, db_type, state, context or {})
        except Exception as error:
            state['status'] = 'interrupted'
            state['warnings'].append(f'비교를 완료하지 못했습니다: {type(error).__name__}')
            _save(gateway, STATE_KEY, state)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()

    threading.Thread(target=worker, name='rabbit-author-repair', daemon=True).start()
    return {'success': True, **state, 'items': []}


def _scan(provider, db_type, state, context):
    gateway = provider.get_db_gateway(db_type)
    config = provider.get_plugin_config(db_type, {}) or {}
    query = ('SELECT b.id,b.library_id,b.series_name,b.author,b.cover_artist,b.translator,b.file_path,'
             'b.file_format,b.file_mtime,b.file_size,b.link,l.name AS library_name,l.content_kind '
             'FROM books b JOIN libraries l ON l.id=b.library_id '
             'WHERE COALESCE(b.is_deleted,0)=0 AND COALESCE(b.metadata_locked,0)=0 '
             "AND COALESCE(b.author,'')<>''")
    params = ()
    if context.get('library_id'):
        query += ' AND b.library_id=?'
        params = (int(context['library_id']),)
    if context.get('series_name'):
        query += ' AND HEX(b.series_name)=HEX(?)'
        params += (str(context['series_name']),)
    rows = [row for row in gateway.fetch_all(query + ' ORDER BY b.library_id,b.series_name,b.id', params)
            if _suspect(row)]
    state['total'] = len(rows)
    _save(gateway, STATE_KEY, state)
    remote_cache = {}
    timeouts = 0
    for row in rows:
        proposed, source, url, reason, timed_out = _evidence(provider, row, config, remote_cache)
        timeouts += int(timed_out)
        entry = {**row, 'book_id': row['id'], 'run_id': state['run_id'],
                 'current_author': str(row.get('author') or ''), 'proposed_author': proposed,
                 'source': source, 'source_url': url, 'reason': reason,
                 'status': 'repairable' if proposed else 'unchanged'}
        _save(gateway, _entry_key(state['run_id'], row['id']), entry)
        state['checked'] += 1
        _save(gateway, STATE_KEY, state)
        if timeouts >= 3:
            state['warnings'].append('파일 메타데이터 읽기가 반복 지연되어 나머지 비교를 중단했습니다.')
            break
    if remote_cache.get('_blocked'):
        state['warnings'].append('리디 조회 실패 항목은 변경하지 않았습니다. 제한 해제 후 다시 비교할 수 있습니다.')
    state['status'] = 'complete' if state['checked'] == state['total'] else 'interrupted'
    state['finished_at'] = time.time()
    _save(gateway, STATE_KEY, state)


def _commit(gateway, entry):
    before = entry['current_author']
    after = entry['proposed_author']
    journal = {**entry, 'status': 'prepared', 'prepared_at': time.time()}
    journal_key = f'{JOURNAL_PREFIX}{entry["run_id"]}:{entry["book_id"]}'
    previous = _json(gateway.get_setting(journal_key))
    if previous:
        if (previous.get('current_author') != before or previous.get('proposed_author') != after):
            raise ValueError('복구 기록의 원본 값이 다릅니다.')
        completed = gateway.fetch_one(
            'SELECT id FROM books WHERE id=? AND library_id=? '
            'AND HEX(file_path)=HEX(?) AND HEX(series_name)=HEX(?) '
            'AND HEX(author)=HEX(?) AND COALESCE(file_mtime,0)=? AND COALESCE(file_size,0)=? '
            'AND COALESCE(metadata_locked,0)=0 AND COALESCE(is_deleted,0)=0',
            (entry['book_id'], entry['library_id'], entry['file_path'], entry['series_name'],
             after, entry.get('file_mtime') or 0, entry.get('file_size') or 0))
        if completed:
            previous.update(status='applied', finished_at=time.time())
            _save(gateway, journal_key, previous)
            entry['status'] = 'applied'
            _save(gateway, _entry_key(entry['run_id'], entry['book_id']), entry)
            entry['already_applied'] = True
            return 0
        if previous.get('status') == 'applied':
            entry.update(status='conflict', reason='복구 후 변경된 값이므로 유지했습니다.')
            _save(gateway, _entry_key(entry['run_id'], entry['book_id']), entry)
            return 0
    # Persist the original first. A failed journal write prevents any DB edit.
    _save(gateway, journal_key, journal)
    affected = gateway.execute(
        'UPDATE books SET author=? WHERE id=? AND library_id=? '
        'AND HEX(file_path)=HEX(?) AND HEX(series_name)=HEX(?) '
        "AND HEX(COALESCE(author,''))=HEX(?) "
        'AND COALESCE(file_mtime,0)=? AND COALESCE(file_size,0)=? '
        'AND COALESCE(metadata_locked,0)=0 AND COALESCE(is_deleted,0)=0',
        (after, entry['book_id'], entry['library_id'], entry['file_path'],
         entry['series_name'], before, entry.get('file_mtime') or 0, entry.get('file_size') or 0))
    journal.update(status='applied' if affected else 'conflict', finished_at=time.time())
    entry['status'] = journal['status']
    if not affected:
        entry['reason'] = '비교 후 변경·이동·잠금이 발생해 유지했습니다.'
    try:
        _save(gateway, journal_key, journal)
        _save(gateway, _entry_key(entry['run_id'], entry['book_id']), entry)
    except Exception:
        # The prepared journal already holds the exact original and proposed
        # values. Still count the committed change and invalidate visible data.
        entry['warning'] = f'{entry["series_name"]}: 복구 결과 기록을 완료하지 못했습니다. 원본 기록은 보존했습니다.'
    return int(bool(affected))


def apply(provider, db_type, context):
    import fcntl
    with _lock_path(db_type).open('a+b') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'success': False, 'error': '작가 비교 또는 복구가 진행 중입니다.'}
        return _apply(provider, db_type, context or {})


def _apply(provider, db_type, context):
    gateway = provider.get_db_gateway(db_type)
    state = _json(gateway.get_setting(STATE_KEY))
    if state.get('status') == 'running':
        return {'success': False, 'error': '작가 비교가 끝난 뒤 적용해 주세요.'}
    entries = _entries(gateway, state.get('run_id'))
    ids = context.get('book_ids')
    if not isinstance(ids, list) or not ids or len(ids) > 500:
        return {'success': False, 'error': '복구할 작품을 선택해 주세요.'}
    if any(isinstance(value, bool) or not (isinstance(value, int) and value > 0
           or isinstance(value, str) and re.fullmatch(r'[1-9][0-9]*', value)) for value in ids):
        return {'success': False, 'error': '도서 번호를 확인해 주세요.'}
    ids = {int(value) for value in ids}
    selected = {(entry['library_id'], entry['series_name']) for entry in entries
                if entry.get('book_id') in ids and entry.get('status') == 'repairable'}
    updated = 0
    updated_series = set()
    skipped = 0
    already_applied = 0
    warnings = []
    for entry in entries:
        identity = (entry['library_id'], entry['series_name'])
        if identity not in selected or entry.get('status') != 'repairable':
            continue
        if not _valid_author(entry.get('proposed_author')) or not entry.get('file_path'):
            skipped += 1
            continue
        try:
            changed = _commit(gateway, entry)
        except Exception as error:
            skipped += 1
            warnings.append(f'{entry["series_name"]}: 복구를 완료하지 못했습니다. ({type(error).__name__})')
            continue
        if entry.get('warning'):
            warnings.append(entry['warning'])
        if entry.get('already_applied'):
            already_applied += 1
            continue
        updated += changed
        skipped += not changed
        if changed:
            updated_series.add(identity)
    if updated:
        from services.series_service import SeriesService
        SeriesService.invalidate_all_books_cache(db_type)
    return {'success': True, 'updated_books': updated, 'updated_series': len(updated_series),
            'skipped': skipped, 'already_applied': already_applied, 'warnings': warnings,
            'message': f'글작가 {updated}권을 복구했습니다.' + (f' 이미 복구된 {already_applied}권의 기록을 확인했습니다.' if already_applied else ''),
            'items': _items(_entries(gateway, state.get('run_id')), db_type)}
