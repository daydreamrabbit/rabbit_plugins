"""Persistent per-series automatic collection history; manual searches bypass it."""
from contextlib import contextmanager
from contextvars import ContextVar
import os
import hashlib
import json
from pathlib import Path
import tempfile
import time

priority_failed = ContextVar('rabbit_metadata_priority_failed', default='')
fetch_failed = ContextVar('rabbit_metadata_fetch_failed', default=False)


def fingerprint(files, config, kind):
    raw = json.dumps([files, config, kind], sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


@contextmanager
def attempt(gateway, db_type, library_id, series, signature, flock, book_id=None, config=None):
    identity = hashlib.sha256(json.dumps([db_type, library_id, series.casefold()],
                                         ensure_ascii=False).encode()).hexdigest()
    key = 'rabbit_plugins:collection-history:v1:' + identity
    user = str(os.getuid()) if hasattr(os, 'getuid') else os.getlogin()
    path = Path(tempfile.gettempdir()) / ('rabbit-collection-' + user + '-' + identity + '.lock')
    with path.open('a+b') as lock:
        try:
            flock.flock(lock.fileno(), flock.LOCK_EX | flock.LOCK_NB)
        except BlockingIOError:
            yield None
            return
        priority_token = priority_failed.set('')
        token = fetch_failed.set(False)
        state = None
        try:
            saved = gateway.get_setting(key)
            if isinstance(saved, dict):
                saved = saved.get('value')
            try:
                saved = json.loads(saved or '{}')
            except (TypeError, ValueError):
                saved = {}
            if (isinstance(saved, dict) and saved.get('signature') == signature
                    and float(saved.get('retry_at') or 0) > time.time()):
                yield None
                return
            state = {'signature': signature, 'status': 'retry', 'book_id': book_id,
                     'library_id': library_id, 'series_name': series}
            yield state
        finally:
            try:
                if state is not None:
                    if fetch_failed.get():
                        state['status'] = 'retry'
                    state['checked_at'] = time.time()
                    # A valid response with absent fields is not a network
                    # failure. Revisit unchanged works at most once a day.
                    from .deferred_metadata import delay_seconds
                    delay = delay_seconds(config or {}) or 300
                    state['retry_at'] = state['checked_at'] + (delay if state['status'] == 'retry' else 86400)
                    gateway.set_setting(key, json.dumps(state))
            finally:
                priority_failed.reset(priority_token)
                fetch_failed.reset(token)
                flock.flock(lock.fileno(), flock.LOCK_UN)
