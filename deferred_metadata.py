"""Opt-in, durable retries and cross-provider hold confirmation."""
import hashlib
import json
import os
import tempfile
import threading
import time
from datetime import datetime

_start_lock = threading.Lock()
_started = False


def delay_seconds(config):
    try:
        minutes = int(config.get('metadata_deferred_minutes') or 0)
    except (ValueError, TypeError, OverflowError):
        return 0
    return min(10080, max(0, minutes)) * 60


def distinct_sources(candidates, enabled):
    if not isinstance(candidates, list) or not 2 <= len(candidates) <= 12:
        return False
    sources = [item.get('source') for item in candidates if isinstance(item, dict)]
    return (len(sources) == len(candidates) == len(set(sources))
            and all(source in enabled for source in sources))


def describe_hold(hold, config, gateway, ridi_blocked=False):
    from . import rabbit_plugins as rp
    delay = delay_seconds(config)
    if not delay or not rp._config_bool(config.get('metadata_auto_enabled'), False):
        return '예약 처리 꺼짐'
    if not distinct_sources(hold.get('candidates'), rp.METADATA_SOURCES):
        return '자동 처리 제외: 같은 제공처의 복수 후보 또는 후보 정보 부족'
    if ridi_blocked and any(c.get('source') == 'ridi' for c in hold.get('candidates', [])):
        return '리디 요청 제한 해제 대기 중'
    try:
        created = datetime.fromisoformat(hold['checked_at']).timestamp()
        saved = gateway.get_setting('rabbit_plugins:deferred-at:' + str(hold['book_id'])) or {}
        due = max(created + delay, float(saved.get('value') or 0))
    except (ValueError, TypeError, KeyError):
        return '보류 시각 확인 필요'
    if due > time.time():
        return '예약 처리 예정: ' + datetime.fromtimestamp(due).astimezone().isoformat(timespec='seconds')
    return '대기 시간 경과 · 순차 처리 대기 중'


def tick(provider, db_type, flock):
    from . import rabbit_plugins as rp, ridi_http, collection_history
    config = provider.get_plugin_config(db_type, {}) or {}
    delay = delay_seconds(config)
    if not delay or not rp._config_bool(config.get('metadata_auto_enabled'), False):
        return
    ridi_blocked = ridi_http.cooling_down()
    gateway = provider.get_db_gateway(db_type)
    now = time.time()
    # Holds are processed first. Persist the next attempt before network I/O so
    # a worker restart or failed provider does not create a retry storm.
    for hold in rp._metadata_hold_entries(gateway, db_type):
        if not distinct_sources(hold.get('candidates'), rp.METADATA_SOURCES):
            continue
        if ridi_blocked and any(c.get('source') == 'ridi' for c in hold.get('candidates', [])):
            continue
        try:
            created = datetime.fromisoformat(hold['checked_at']).timestamp()
        except (ValueError, TypeError, KeyError):
            continue
        key = 'rabbit_plugins:deferred-at:' + str(hold['book_id'])
        saved = gateway.get_setting(key) or {}
        try:
            next_at = float(saved.get('value') or 0)
        except (TypeError, ValueError):
            next_at = 0
        if now < max(created + delay, next_at):
            continue
        # Same per-series lock as scan-triggered collection.
        identity = hashlib.sha256(json.dumps([db_type, hold['library_id'], hold['series_name'].casefold()], ensure_ascii=False).encode()).hexdigest()
        path = os.path.join(tempfile.gettempdir(), f'rabbit-collection-{os.getuid()}-{identity}.lock')
        with open(path, 'a+b') as lock:
            try:
                flock.flock(lock.fileno(), flock.LOCK_EX | flock.LOCK_NB)
            except BlockingIOError:
                continue
            try:
                gateway.set_setting(key, str(now + delay))
                ft = collection_history.fetch_failed.set(False)
                pt = collection_history.priority_failed.set('')
                try:
                    result = provider._confirm_same_work(db_type, hold['book_id'], automatic=True)
                    if not result.get('success'):
                        hold_key = rp._metadata_hold_key(hold['library_id'], hold['series_name'])
                        current = rp._read_metadata_hold(gateway, hold['library_id'], hold['series_name'])
                        if current:
                            current['auto_last_message'] = result.get('error') or result.get('message') or '적용 실패'
                            gateway.set_setting(hold_key, json.dumps(current, ensure_ascii=False))
                    print(f'[RabbitPlugins-Metadata] 예약 동일 작품 처리 book={hold["book_id"]} result={result}')
                finally:
                    collection_history.fetch_failed.reset(ft)
                    collection_history.priority_failed.reset(pt)
            finally:
                flock.flock(lock.fileno(), flock.LOCK_UN)
        return
    if ridi_blocked and 'ridi' in rp._metadata_source_order(config):
        return
    rows = gateway.fetch_all("SELECT `key`, `value` FROM settings WHERE `key` LIKE ?", ('rabbit_plugins:collection-history:v1:%',))
    # Upgrade failure records written before scheduled retries stored book IDs.
    legacy = {}
    for row in rows:
        try:
            state = json.loads(row.get('value') or '{}')
            if state.get('status') == 'retry' and not state.get('book_id'):
                legacy[row['key']] = (row, state)
        except (TypeError, ValueError, AttributeError):
            continue
    if legacy:
        for book in gateway.fetch_all('SELECT MIN(id) AS id, library_id, series_name FROM books WHERE COALESCE(is_deleted, 0) = 0 GROUP BY library_id, series_name', ()):
            identity = hashlib.sha256(json.dumps([db_type, book['library_id'], str(book['series_name']).casefold()], ensure_ascii=False).encode()).hexdigest()
            key = 'rabbit_plugins:collection-history:v1:' + identity
            if key in legacy:
                row, state = legacy[key]
                state.update(book_id=book['id'], library_id=book['library_id'], series_name=book['series_name'])
                row['value'] = json.dumps(state)
                gateway.set_setting(key, row['value'])
    due = []
    for row in rows:
        try:
            state = json.loads(row.get('value') or '{}')
            if (state.get('status') == 'retry' and state.get('book_id')
                    and now >= max(float(state.get('retry_at') or 0), float(state.get('checked_at') or 0) + delay)):
                due.append((row, state))
        except (TypeError, ValueError, AttributeError):
            continue
    for row, state in sorted(due, key=lambda pair: float(pair[1].get('checked_at') or 0)):
        target = gateway.fetch_one('SELECT id FROM books WHERE id = ? AND library_id = ? AND series_name = ? AND COALESCE(is_deleted, 0) = 0 AND COALESCE(metadata_locked, 0) = 0',
                                   (state['book_id'], state['library_id'], state['series_name']))
        if not target:
            state['status'] = 'cancelled'
            gateway.set_setting(row['key'], json.dumps(state))
            continue
        # Mark scheduler pacing separately; collection history still owns its
        # normal retry gate and is updated by the actual collection attempt.
        provider._queue_auto_collect(db_type, {'book_ids': [state['book_id']], 'library_id': state['library_id']})
        latest = gateway.get_setting(row['key']) or {}
        if latest.get('value') == row.get('value'):
            state['retry_at'] = now + delay
            gateway.set_setting(row['key'], json.dumps(state))
        return


def start(provider, flock):
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    def worker():
        path = os.path.join(tempfile.gettempdir(), f'rabbit-deferred-{os.getuid()}.lock')
        while True:
            try:
                # Re-elect each tick so a surviving worker takes over after reload.
                with open(path, 'a+b') as lock:
                    try:
                        flock.flock(lock.fileno(), flock.LOCK_EX | flock.LOCK_NB)
                    except BlockingIOError:
                        continue
                    try:
                        for db_type in ('general', 'adult'):
                            tick(provider, db_type, flock)
                    finally:
                        flock.flock(lock.fileno(), flock.LOCK_UN)
            except Exception as error:
                print(f'[RabbitPlugins-Metadata] 예약 처리 실패: {type(error).__name__}: {error}')
            finally:
                time.sleep(30)
    threading.Thread(target=worker, name='rabbit-deferred-metadata', daemon=True).start()
