import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from .. import series_refresh as refresh, rabbit_plugins as rp


class Gateway:
    def __init__(self, config):
        self.config = dict(config)

    def get_plugin_config(self, *_args, **_kwargs):
        return dict(self.config)

    def get_setting(self, *_args):
        return None

    def set_plugin_config(self, _plugin, config):
        self.config = dict(config)


class Provider:
    id = 'rabbit_plugins'

    def __init__(self, active):
        self.gateway = Gateway({'series_db_path': str(active), 'series_auto_update_enabled': True,
                                'unrelated_setting': 'preserved'})
        self.prewarmed = 0

    def get_plugin_config(self, *_args):
        return self.gateway.get_plugin_config()

    def get_db_gateway(self, *_args):
        return self.gateway

    def _series_db_path(self):
        return Path(self.gateway.config['series_db_path'])

    def _series_index(self):
        self.prewarmed += 1
        return Path('index.sqlite')


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        yesterday = datetime.now(refresh.KST) - timedelta(days=1)
        self.old = self.root / ('series.' + yesterday.strftime('%Y.%m.%d') + '.sqlite')
        self.old.write_bytes(b'existing database')
        self.provider = Provider(self.old)
        fixture = self.root / 'fixture.sqlite'
        with sqlite3.connect(fixture) as db:
            columns = ','.join('"' + name + '" ' + ('INTEGER PRIMARY KEY' if name == 'id' else 'TEXT')
                               for name in refresh.EXPECTED_COLUMNS)
            db.execute('CREATE TABLE Series (' + columns + ')')
            db.execute('INSERT INTO Series (id,title,titles,authors,artists,relationships_v2) '
                       'VALUES (1,?,?,?,?,?)', ('작품', '[]', '작가', '그림',
                                              '[{"relation_type":"parent","to_series_id":2}]'))
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:gz') as archive:
            archive.add(fixture, arcname='series.full.sqlite')
        self.archive = output.getvalue()
        self.sha = hashlib.sha1(self.archive).hexdigest()
        self.target = self.root / ('series.' + datetime.now(refresh.KST).strftime('%Y.%m.%d') + '.sqlite')

    def tearDown(self):
        self.temporary.cleanup()

    def download(self, path, sha, guard):
        guard()
        self.assertEqual(sha, self.sha)
        path.write_bytes(self.archive)
        return len(self.archive)

    def run_refresh(self, **kwargs):
        with patch.object(refresh, 'published_sha1', return_value=self.sha), \
             patch.object(refresh, 'download_archive', side_effect=self.download):
            return refresh.run_once(self.provider, rp.fcntl, **kwargs)

    def test_real_sql_rotation_updates_config_and_removes_previous_date(self):
        self.assertTrue(self.run_refresh())
        self.assertFalse(self.old.exists())
        self.assertEqual(self.provider._series_db_path(), self.target)
        self.assertEqual(self.provider.gateway.config['unrelated_setting'], 'preserved')
        self.assertEqual(self.provider.prewarmed, 1)
        with sqlite3.connect(self.target.as_uri() + '?mode=ro', uri=True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM Series').fetchone()[0], 1)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertFalse((self.root / '.rabbit-series-refresh-stage').exists())
        self.assertEqual(refresh.read_state(self.root)['status'], 'updated')
        self.assertFalse(self.run_refresh(), 'successful checks run once per scheduled day')

    def test_bootstrap_without_existing_db_and_preserve_manual_source(self):
        self.old.unlink()
        self.assertTrue(self.run_refresh())
        self.assertTrue(self.target.is_file())

    def test_manual_source_is_not_deleted(self):
        manual = self.root / 'user-source.sqlite'
        self.old.rename(manual)
        self.provider.gateway.config['series_db_path'] = str(manual)
        self.assertTrue(self.run_refresh())
        self.assertTrue(manual.is_file())

    def test_disable_does_not_download(self):
        self.provider.gateway.config['series_auto_update_enabled'] = False
        with patch.object(refresh, 'published_sha1') as network:
            self.assertFalse(refresh.run_once(self.provider, rp.fcntl))
            network.assert_not_called()
        self.assertEqual(self.old.read_bytes(), b'existing database')

    def test_disable_during_optimization_preserves_current(self):
        original = refresh.optimize_and_verify
        def turn_off(*args):
            original(*args)
            self.provider.gateway.config['series_auto_update_enabled'] = False
        with patch.object(refresh, 'optimize_and_verify', side_effect=turn_off):
            self.assertFalse(self.run_refresh())
        self.assertEqual(self.old.read_bytes(), b'existing database')
        self.assertFalse(self.target.exists())
        self.assertEqual(refresh.read_state(self.root)['status'], 'cancelled')

    def test_failed_optimization_and_collision_preserve_existing(self):
        with patch.object(refresh, 'optimize_and_verify', side_effect=ValueError('bad schema')):
            self.assertFalse(self.run_refresh())
        self.assertEqual(self.old.read_bytes(), b'existing database')
        self.target.write_bytes(b'unrelated dated file')
        self.assertFalse(self.run_refresh(force=True))
        self.assertEqual(self.target.read_bytes(), b'unrelated dated file')
        self.assertEqual(self.provider._series_db_path(), self.old)

    def test_lock_prevents_overlap(self):
        with (self.root / '.rabbit-series-refresh.lock').open('a+b') as lock:
            rp.fcntl.flock(lock.fileno(), rp.fcntl.LOCK_EX | rp.fcntl.LOCK_NB)
            with patch.object(refresh, 'published_sha1') as network:
                self.assertFalse(refresh.run_once(self.provider, rp.fcntl, force=True))
                network.assert_not_called()

    def test_unchanged_checksum_never_downloads_or_changes_filename(self):
        refresh.write_state(self.root, {'source_sha1': self.sha})
        with patch.object(refresh, 'download_archive', side_effect=AssertionError('no download')):
            with patch.object(refresh, 'published_sha1', return_value=self.sha):
                self.assertTrue(refresh.run_once(self.provider, rp.fcntl))
        self.assertTrue(self.old.exists())
        self.assertFalse(self.target.exists())

    def test_new_dump_on_same_date_replaces_in_place(self):
        self.assertTrue(self.run_refresh())
        state = refresh.read_state(self.root)
        state['source_sha1'] = '0' * 40
        refresh.write_state(self.root, state)
        self.assertTrue(self.run_refresh(force=True))
        self.assertEqual(self.provider._series_db_path(), self.target)
        self.assertEqual(len(list(self.root.glob('series.*.sqlite'))), 1)
        self.assertNotIn('pending', refresh.read_state(self.root))

    def test_stale_saved_date_path_resolves_to_managed_current_file(self):
        self.old.unlink()
        self.target.write_bytes(b'current')
        refresh.write_state(self.root, {'active_name': self.target.name})
        provider = rp.RabbitPluginsMetadataProvider()
        with patch.object(provider, 'get_plugin_config', return_value={'series_db_path': str(self.old)}):
            self.assertEqual(provider._series_db_path(), self.target)

    def test_schedule_and_retry(self):
        morning = datetime(2026, 11, 4, 12, 29, tzinfo=refresh.KST)
        self.assertTrue(refresh.due({}, morning))
        self.assertFalse(refresh.due({'checked_day': '2026-11-03'}, morning))
        self.assertTrue(refresh.due({'checked_day': '2026-11-02'}, morning), 'missed runs catch up after restart')
        self.assertTrue(refresh.due({'checked_day': '2026-11-03'}, morning + timedelta(minutes=1)))
        self.assertFalse(refresh.due({'next_attempt': morning.timestamp() + 30}, morning))

    def test_config_write_failure_preserves_old_and_removes_candidate(self):
        with patch.object(self.provider.gateway, 'set_plugin_config', side_effect=RuntimeError('DB unavailable')):
            self.assertFalse(self.run_refresh())
        self.assertTrue(self.old.exists())
        self.assertFalse(self.target.exists())

    def test_unknown_relation_is_rejected(self):
        source = self.root / 'fixture.sqlite'
        with sqlite3.connect(source) as db:
            db.execute('UPDATE Series SET relationships_v2=?',
                       ('[{"relation_type":"future_kind","to_series_id":2}]',))
        with self.assertRaisesRegex(ValueError, '새 관계 유형'):
            refresh.inspect_source(source)

    def test_download_checksum_and_unsafe_archive(self):
        class Response(io.BytesIO):
            headers = {'Content-Length': str(len(self.archive))}
        with patch.object(refresh.urllib.request, 'urlopen', return_value=Response(self.archive)):
            refresh.download_archive(self.root / 'good.tar.gz', self.sha)
        with patch.object(refresh.urllib.request, 'urlopen', return_value=Response(self.archive)):
            with self.assertRaisesRegex(ValueError, '검사값'):
                refresh.download_archive(self.root / 'bad.tar.gz', '0' * 40)
        malicious = self.root / 'malicious.tar.gz'
        with tarfile.open(malicious, 'w:gz') as archive:
            link = tarfile.TarInfo('series.full.sqlite')
            link.type, link.linkname = tarfile.SYMTYPE, '/etc/passwd'
            archive.addfile(link)
        with self.assertRaisesRegex(ValueError, '예상하지 못한 항목'):
            refresh.extract_sqlite(malicious, self.root / 'unsafe.sqlite')
        self.assertFalse((self.root / 'unsafe.sqlite').exists())

    def test_restart_recovers_both_sides_of_config_commit(self):
        self.target.write_bytes(b'verified candidate')
        info = self.target.stat()
        pending = {'name': self.target.name, 'previous': self.old.name,
                   'previous_inode': self.old.stat().st_ino,
                   'identity': [info.st_ino, info.st_size, info.st_mtime_ns],
                   'sha1': self.sha, 'rows': 1, 'checked_day': '2026-11-03', 'created_at': 1}
        state = {'pending': dict(pending)}
        refresh.recover_pending(self.provider, self.root, state)
        self.assertFalse(self.target.exists(), 'unactivated candidate is removed after a crash')
        self.assertTrue(self.old.exists())
        self.target.write_bytes(b'verified candidate')
        info = self.target.stat()
        pending['identity'] = [info.st_ino, info.st_size, info.st_mtime_ns]
        state = {'pending': pending}
        self.provider.gateway.config['series_db_path'] = str(self.target)
        refresh.recover_pending(self.provider, self.root, state)
        self.assertFalse(self.old.exists(), 'committed dated file is retained and the old date is cleaned')
        self.assertTrue(self.target.exists())
        self.assertEqual(state['source_sha1'], self.sha)
        self.assertNotIn('pending', state)

    def test_status_action_requires_admin(self):
        from flask import Flask, session
        app = Flask('series_refresh_test')
        app.secret_key = 'test'
        provider = rp.RabbitPluginsMetadataProvider()
        with app.test_request_context('/'):
            self.assertFalse(provider.run_context_menu_action('general', 'series_refresh_status', {})['success'])
            session['role'] = 'admin'
            with patch.object(refresh, 'start') as start, \
                 patch.object(refresh, 'status', return_value={'success': True}):
                self.assertTrue(provider.run_context_menu_action('general', 'series_refresh_status', {})['success'])
                start.assert_called_once()

    def test_boot_hook_without_context_starts_once_with_installation_root(self):
        from flask import has_app_context, current_app
        def check_context(*_args):
            self.assertTrue(has_app_context())
            self.assertEqual(Path(current_app.root_path), Path(refresh.__file__).resolve().parents[3])
        with patch.object(refresh, '_started', False), \
             patch.object(refresh.threading, 'Thread') as thread, \
             patch.object(refresh, 'run_once', side_effect=check_context) as tick, \
             patch.object(refresh.time, 'sleep', side_effect=StopIteration):
            refresh.start(self.provider, rp.fcntl)
            refresh.start(self.provider, rp.fcntl)
            thread.assert_called_once()
            with self.assertRaises(StopIteration):
                thread.call_args.kwargs['target']()
            tick.assert_called_once()

    def test_hot_reload_starts_on_next_real_plugin_request(self):
        from flask import Flask
        app = Flask('series_hot_reload_test')
        provider = rp.RabbitPluginsMetadataProvider()
        app.add_url_rule('/probe', 'probe', lambda: provider.get_plugin_config('general'))
        with patch.object(provider, 'get_db_gateway', return_value=self.provider.gateway), \
             patch.object(refresh, 'start') as start:
            response = app.test_client().get('/probe')
            self.assertEqual(response.status_code, 200)
            start.assert_called_once()


if __name__ == '__main__':
    unittest.main()
