"""Daily MangaBaka refresh, owned by the plugin and shared across workers."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tarfile
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

ARCHIVE_URL = 'https://api.mangabaka.org/v1/database/series.full.sqlite.tar.gz'
CHECKSUM_URL = ARCHIVE_URL + '.sha1'
MAX_ARCHIVE_BYTES = 3 * 1024**3
MAX_SOURCE_BYTES = 20 * 1024**3
EXPECTED_COLUMNS = (
    'id', 'title', 'type', 'links', 'status', 'titles', 'artists', 'authors',
    'final_volume', 'content_rating', 'cover_raw_url', 'native_title',
    'romanized_title', 'secondary_titles_ko', 'secondary_titles_en',
    'secondary_titles_ja', 'source_manga_updates_response_series_id',
    'source_manga_updates_response_recommendations',
    'source_manga_updates_response_category_recommendations', 'relationships_v2',
    'relationships_other', 'relationships_sequel', 'relationships_spin_off',
    'relationships_main_story', 'relationships_alternative',
    'relationships_adaptation', 'relationships_prequel', 'relationships_side_story',
)
SUPPORTED_RELATION_TYPES = {
    'main', 'main_story', 'parent', 'parent_story', 'source', 'original',
    'prequel', 'sequel', 'spin_off', 'side_story', 'alternative', 'adaptation',
    'parody', 'contains', 'compilation', 'cameo', 'series', 'remake',
    'crossover', 'summary', 'reboot', 'expansion', 'character_focus',
    'uncollected', 'other',
}


def request(url):
    return urllib.request.Request(url, headers={
        'User-Agent': 'BookOasis-Rabbit-Series-Refresh/1.0',
        'Cache-Control': 'no-cache',
    })


def published_sha1():
    with urllib.request.urlopen(request(CHECKSUM_URL), timeout=30) as response:
        raw = response.read(256).decode('ascii', 'strict').strip()
    match = re.match(r'^([a-fA-F0-9]{40})(?:\s|$)', raw)
    if not match:
        raise ValueError('MangaBaka 검사값 형식이 올바르지 않습니다.')
    return match.group(1).lower()


def download_archive(destination, expected_sha1, guard=lambda: None):
    digest = hashlib.sha1()
    total = 0
    with urllib.request.urlopen(request(ARCHIVE_URL), timeout=60) as response:
        declared = response.headers.get('Content-Length')
        if declared and int(declared) > MAX_ARCHIVE_BYTES:
            raise ValueError('덤프 압축 파일이 허용 크기를 초과합니다.')
        if shutil.disk_usage(destination.parent).free < int(declared or 0) + 1024**3:
            raise ValueError('덤프 다운로드에 필요한 여유 공간이 부족합니다.')
        with destination.open('xb') as output:
            while True:
                guard()
                chunk = response.read(4 * 1024**2)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    raise ValueError('덤프 압축 파일이 허용 크기를 초과합니다.')
                output.write(chunk)
                digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
    if not total or (declared and total != int(declared)):
        raise ValueError('덤프 다운로드가 완료되지 않았습니다.')
    if digest.hexdigest() != expected_sha1:
        raise ValueError('MangaBaka 검사값과 다운로드 파일이 다릅니다.')
    return total


def extract_sqlite(archive, destination, guard=lambda: None):
    found = 0
    with tarfile.open(archive, mode='r|gz') as source:
        for member in source:
            if member.isdir():
                continue
            if not member.isfile() or Path(member.name).name != 'series.full.sqlite':
                raise ValueError('덤프 압축 파일에 예상하지 못한 항목이 있습니다.')
            found += 1
            if found != 1 or not 0 < member.size <= MAX_SOURCE_BYTES:
                raise ValueError('덤프 SQLite 파일 수나 크기가 올바르지 않습니다.')
            if shutil.disk_usage(destination.parent).free < member.size * 3 + 1024**3:
                raise ValueError('덤프 추출과 최적화에 필요한 여유 공간이 부족합니다.')
            input_file = source.extractfile(member)
            if input_file is None:
                raise ValueError('덤프 SQLite 파일을 읽지 못했습니다.')
            remaining = member.size
            with destination.open('xb') as output:
                while remaining:
                    guard()
                    chunk = input_file.read(min(4 * 1024**2, remaining))
                    if not chunk:
                        raise ValueError('덤프 SQLite 파일이 끝까지 추출되지 않았습니다.')
                    output.write(chunk)
                    remaining -= len(chunk)
                output.flush()
                os.fsync(output.fileno())
    if found != 1:
        raise ValueError('덤프에서 Series SQLite 파일을 찾지 못했습니다.')
    return destination.stat().st_size


def inspect_source(path):
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=60) as db:
        columns = {row[1] for row in db.execute('PRAGMA table_info(Series)')}
        missing = sorted(set(EXPECTED_COLUMNS) - columns)
        if missing:
            raise ValueError('원본 덤프에 필수 열이 없습니다: ' + ', '.join(missing))
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='Series_compact_new'").fetchone():
            raise ValueError('원본 덤프에 Series_compact_new가 있습니다.')
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('원본 덤프 무결성 검사에 실패했습니다.')
        relation_types = {row[0] or '' for row in db.execute('''
            SELECT DISTINCT lower(json_extract(j.value, '$.relation_type'))
            FROM Series AS s
            JOIN json_each(CASE WHEN json_valid(s.relationships_v2)
                                THEN s.relationships_v2 ELSE '[]' END) AS j
            WHERE j.type='object'
        ''')}
        unsupported = sorted(relation_types - SUPPORTED_RELATION_TYPES)
        if unsupported:
            raise ValueError('새 관계 유형을 확인해야 합니다: ' + ', '.join(unsupported))
        return db.execute('SELECT COUNT(*) FROM Series').fetchone()[0], columns


def optimize_and_verify(path, expected_rows, guard=lambda: None):
    script = Path(__file__).with_name('series_db_optimize.sql').read_text(encoding='utf-8')
    if '@@set' in script or '@unset' in script or '${' in script:
        raise ValueError('최적화 SQL에 DBeaver 전용 명령이 남아 있습니다.')
    with sqlite3.connect(path, timeout=120) as db:
        db.set_progress_handler(lambda: (guard(), 0)[1], 100000)
        db.execute('PRAGMA busy_timeout=120000')
        db.executescript(script)
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=120) as db:
        columns = tuple(row[1] for row in db.execute('PRAGMA table_info(Series)'))
        if columns != EXPECTED_COLUMNS:
            raise ValueError('최적화 결과가 28열 스키마와 다릅니다.')
        rows = db.execute('SELECT COUNT(*) FROM Series').fetchone()[0]
        if rows != expected_rows:
            raise ValueError('최적화 전후 작품 수가 다릅니다.')
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('최적화 결과 무결성 검사에 실패했습니다.')
        required = {
            ('table', 'series_title_fts'), ('table', 'series_creator_fts'),
            ('view', 'series_relationship_items'),
            ('view', 'series_recommendation_items'),
            ('trigger', 'series_fts_after_insert'),
            ('trigger', 'series_fts_after_delete'),
            ('trigger', 'series_fts_after_update'),
        }
        objects = set(db.execute('SELECT type,name FROM sqlite_master'))
        if required - objects:
            raise ValueError('최적화 결과에 검색 또는 관계 조회 구성이 없습니다.')
    return rows


KST = timezone(timedelta(hours=9))
_start_lock = threading.Lock()
_started = False


class Cancelled(Exception):
    pass


def enabled(provider):
    from .rabbit_plugins import _config_bool
    plugin_flag = provider.get_db_gateway('general').get_setting('PLUGIN_ENABLED_' + provider.id)
    if plugin_flag is not None and str(plugin_flag.get('value')) != '1':
        return False
    return _config_bool(provider.get_plugin_config('general', {}).get('series_auto_update_enabled'), True)


def read_state(directory):
    try:
        state = json.loads((directory / '.rabbit-series-refresh-state.json').read_text(encoding='utf-8'))
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(directory, state):
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory,
                                     prefix='.rabbit-series-state-', delete=False) as output:
        temporary = Path(output.name)
        json.dump(state, output, ensure_ascii=False)
        output.flush()
        os.fsync(output.fileno())
    try:
        os.replace(temporary, directory / '.rabbit-series-refresh-state.json')
    finally:
        temporary.unlink(missing_ok=True)


def slot_day(now):
    if (now.hour, now.minute) < (12, 30):
        now -= timedelta(days=1)
    return now.strftime('%Y-%m-%d')


def due(state, now):
    if now.timestamp() < float(state.get('next_attempt') or 0):
        return False
    last = state.get('checked_day')
    return not last or last < slot_day(now)


def status(provider):
    active = provider._series_db_path()
    state = read_state(active.parent)
    running = state.get('status') in ('checking', 'downloading', 'extracting', 'optimizing', 'activating')
    # A killed worker cannot leave the settings screen showing "running" forever.
    if running and time.time() - float(state.get('started_at') or 0) > 4 * 3600:
        state = {**state, 'status': 'failed', 'message': '이전 갱신이 중단되었습니다. 다음 확인 때 재시도합니다.'}
    return {'success': True, 'enabled': enabled(provider), 'active_path': str(active),
            'schedule': '매일 12:30 (한국 시간)', 'state': state}


def _activate(provider, active, target):
    gateway = provider.get_db_gateway('general')
    config = gateway.get_plugin_config(provider.id, {})
    if target != active:
        if not enabled(provider) or provider._series_db_path() != active:
            raise Cancelled('자동 갱신 설정 또는 DB 경로가 바뀌어 반영하지 않았습니다.')
        updated = dict(config, series_db_path=str(target))
        try:
            gateway.set_plugin_config(provider.id, updated)
        except Exception:
            if provider._series_db_path() != target:
                target.unlink(missing_ok=True)
            raise
        if provider._series_db_path() != target:
            raise RuntimeError('새 Series DB 설정 경로를 확인하지 못했습니다.')
    warning = ''
    try:
        if provider._series_index() is None:
            warning = '검색 보조 인덱스는 다음 조회 때 다시 만듭니다.'
    except Exception:
        warning = '검색 보조 인덱스는 다음 조회 때 다시 만듭니다.'
    # Only scheduler-style dated files are removed. A manually supplied source
    # with another name may also belong to another application.
    if target != active and re.fullmatch(r'series\.\d{4}\.\d{2}\.\d{2}\.sqlite', active.name):
        try:
            active.unlink(missing_ok=True)
        except OSError:
            warning += ' 이전 날짜 파일 삭제 권한을 확인해 주세요.'
    return warning.strip()


def recover_pending(provider, directory, state):
    pending = state.get('pending')
    if not isinstance(pending, dict):
        return
    name = str(pending.get('name') or '')
    previous = str(pending.get('previous') or '')
    if not re.fullmatch(r'series\.\d{4}\.\d{2}\.\d{2}\.sqlite', name):
        return
    target = directory / name
    if target.exists():
        info = target.stat()
        if (info.st_ino, info.st_size, info.st_mtime_ns) != tuple(pending.get('identity', ())):
            return  # The file was replaced outside this operation; do not remove it.
        active = provider._series_db_path()
        if active == target:
            if re.fullmatch(r'series\.\d{4}\.\d{2}\.\d{2}\.sqlite', previous) and previous != name:
                old = directory / previous
                if old.exists() and old.stat().st_ino == pending.get('previous_inode'):
                    try:
                        old.unlink()
                    except OSError:
                        pass  # The current path is already committed; never roll it back.
            state.update(source_sha1=pending['sha1'], rows=pending['rows'],
                         active_name=name, checked_day=pending['checked_day'],
                         updated_at=pending['created_at'], checked_at=time.time(), next_attempt=0,
                         status='updated', message='재시작 후 완료된 DB 교체 기록을 복구했습니다.')
        elif active.name == previous and target != active:
            target.unlink()
            state.update(status='failed', message='중단된 교체를 정리하고 현재 DB를 유지했습니다.', next_attempt=0)
        else:
            return
    state.pop('pending', None)
    write_state(directory, state)


def run_once(provider, flock, force=False, now=None):
    if not enabled(provider):
        return False
    active = provider._series_db_path()
    directory = active.parent
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.rabbit-series-refresh.lock').open('a+b') as lock:
        try:
            flock.flock(lock.fileno(), flock.LOCK_EX | flock.LOCK_NB)
        except BlockingIOError:
            return False
        try:
            state = read_state(directory)
            recover_pending(provider, directory, state)
            now = (now or datetime.now(KST)).astimezone(KST)
            if not force and not due(state, now):
                return False
            started = time.time()
            state.update(status='checking', started_at=started, next_attempt=started + 3600,
                         message='공식 덤프 검사값을 확인하고 있습니다.')
            write_state(directory, state)
            last_guard = [0.0]

            def guard():
                moment = time.monotonic()
                if moment - last_guard[0] >= 2:
                    last_guard[0] = moment
                    if not enabled(provider):
                        raise Cancelled('자동 갱신을 꺼서 현재 DB를 유지했습니다.')
                    if time.time() - started > 4 * 3600:
                        raise TimeoutError('덤프 갱신 제한 시간 4시간을 초과했습니다.')

            def phase(name, message):
                guard()
                state.update(status=name, message=message)
                write_state(directory, state)
                print('[RabbitPlugins-Series] ' + name + ': ' + message, flush=True)

            try:
                sha1 = published_sha1()
                guard()
                if sha1 == state.get('source_sha1') and active.is_file():
                    state.update(status='unchanged', message='새 덤프가 없어 현재 DB를 유지했습니다.',
                                 checked_day=slot_day(now), checked_at=time.time(), next_attempt=0)
                    write_state(directory, state)
                    return True
                stage = directory / '.rabbit-series-refresh-stage'
                if stage.is_symlink() or (stage.exists() and not stage.is_dir()):
                    raise ValueError('덤프 임시 경로가 올바르지 않습니다.')
                if stage.exists():
                    shutil.rmtree(stage)
                stage.mkdir(mode=0o700)
                try:
                    archive, source = stage / 'series.full.sqlite.tar.gz', stage / 'series.full.sqlite'
                    phase('downloading', 'MangaBaka Full SQLite 덤프를 다운로드하고 있습니다.')
                    archive_bytes = download_archive(archive, sha1, guard)
                    phase('extracting', '검사값을 확인했습니다. SQLite 파일을 추출하고 있습니다.')
                    source_bytes = extract_sqlite(archive, source, guard)
                    archive.unlink()
                    if shutil.disk_usage(directory).free < source_bytes * 2 + 1024**3:
                        raise ValueError('최적화와 VACUUM에 필요한 여유 공간이 부족합니다.')
                    phase('optimizing', '관계 유형과 DB를 검사하고 자동 최적화하고 있습니다.')
                    rows, _ = inspect_source(source)
                    optimize_and_verify(source, rows, guard)
                    # Recheck unconditionally before publishing, even if the
                    # last guard ran less than two seconds ago.
                    if not enabled(provider) or provider._series_db_path() != active:
                        raise Cancelled('설정이 변경되어 현재 DB를 유지했습니다.')
                    target = directory / ('series.' + datetime.now(KST).strftime('%Y.%m.%d') + '.sqlite')
                    if target != active and target.exists():
                        raise FileExistsError('같은 날짜의 파일이 이미 있어 덮어쓰지 않았습니다: ' + str(target))
                    source.chmod(stat.S_IMODE(active.stat().st_mode) if active.exists() else 0o600)
                    phase('activating', '새 날짜 파일로 교체하고 플러그인 경로를 변경하고 있습니다.')
                    info = source.stat()
                    state['pending'] = {'name': target.name, 'previous': active.name,
                                        'previous_inode': active.stat().st_ino if active.exists() else None,
                                        'identity': [info.st_ino, info.st_size, info.st_mtime_ns],
                                        'sha1': sha1, 'rows': rows, 'checked_day': slot_day(now),
                                        'created_at': time.time()}
                    write_state(directory, state)
                    os.replace(source, target)
                    try:
                        warning = _activate(provider, active, target)
                    except Exception:
                        if target != active and provider._series_db_path() != target:
                            target.unlink(missing_ok=True)
                        raise
                    state.pop('pending', None)
                    state.update(status='updated', source_sha1=sha1, rows=rows,
                                 source_bytes=source_bytes, archive_bytes=archive_bytes,
                                 updated_at=time.time(), checked_at=time.time(), active_name=target.name,
                                 checked_day=slot_day(now), next_attempt=0,
                                 message='다운로드·최적화·날짜별 교체를 완료했습니다.' + (' ' + warning if warning else ''))
                    write_state(directory, state)
                    return True
                finally:
                    shutil.rmtree(stage, ignore_errors=True)
            except Exception as error:
                cancelled = isinstance(error, Cancelled) or not enabled(provider)
                state.update(status='cancelled' if cancelled else 'failed',
                             message=str(error)[:1000], checked_at=time.time(), next_attempt=time.time() + 3600)
                write_state(directory, state)
                print('[RabbitPlugins-Series] ' + state['status'] + ': ' + state['message'], flush=True)
                return False
        finally:
            flock.flock(lock.fileno(), flock.LOCK_UN)


def start(provider, flock):
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    from flask import Flask, current_app
    try:
        app = current_app._get_current_object()
    except RuntimeError:
        # The core boot hook may run outside app.app_context(). A lightweight
        # context supplies the real installation root without importing core
        # again (which would start scanners and other services).
        app = Flask('rabbit_series_refresh', root_path=str(Path(__file__).resolve().parents[3]))

    def worker():
        while True:
            try:
                with app.app_context():
                    run_once(provider, flock)
            except Exception as error:
                print(f'[RabbitPlugins-Series] 갱신 확인 실패: {type(error).__name__}: {error}', flush=True)
            time.sleep(60)
    threading.Thread(target=worker, name='rabbit-series-refresh', daemon=True).start()
