"""Keep a webtoon's shared main cover when Lazy Scanner finishes concurrently."""
import hashlib
import sqlite3
import unittest
from unittest.mock import patch

from .. import rabbit_plugins as m


class Gateway:
    def __init__(self, cover, path='/book.cbz', locked=0, deleted=0, library=11):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE books(id INTEGER, library_id INTEGER, file_path TEXT,
                cover_image TEXT, metadata_locked INTEGER, is_deleted INTEGER,
                cover_updated_at TEXT);
            CREATE TABLE settings (`key` TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE libraries(id INTEGER, content_kind TEXT);
            INSERT INTO libraries VALUES(11, 'manhwa');
        ''')
        self.db.execute('INSERT INTO books VALUES(1,?,?,?,?,?,NULL)',
                        (library, path, cover, locked, deleted))

    def execute(self, query, params=()):
        return self.db.execute(query, params).rowcount

    def fetch_all(self, query, params=()):
        return [dict(row) for row in self.db.execute(query, params)]

    def fetch_one(self, query, params=()):
        return next(iter(self.fetch_all(query, params)), None)

    def get_setting(self, key, default=None):
        return self.fetch_one('SELECT value FROM settings WHERE `key`=?', (key,)) or default

    def set_setting(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, value))


class SharedCoverRaceTests(unittest.TestCase):
    row = {'id': 1, 'library_id': 11, 'file_path': '/book.cbz',
           'cover_image': 'before.webp', 'metadata_locked': 0}
    scanner_cover = '11/episode04__bo_cover_abc.webp'
    scanner_covers = (
        scanner_cover, '11/episode04__bo_embedded_abc.webp',
        '11/folder_8c8f8d7d615106908c8f8d7d61510690_18a0_4a.webp',
        'folder_8c8f8d7d615106908c8f8d7d61510690_18a0_4a.webp',
        f'11/book_{hashlib.md5(b"/book.cbz").hexdigest()}.webp',
        f'book_{hashlib.md5(b"/book.cbz").hexdigest()}.webp',
    )

    def gateway(self, cover=None, **kwargs):
        gateway = Gateway(self.scanner_cover if cover is None else cover, **kwargs)
        self.addCleanup(gateway.db.close)
        return gateway

    def apply(self, cover, **kwargs):
        gateway = self.gateway(cover, **kwargs)
        applied = m._commit_shared_webtoon_cover(gateway, self.row, 'shared.webp', True)
        return applied, gateway.fetch_one('SELECT * FROM books')['cover_image']

    def restore(self, gateway, config=None):
        provider = object.__new__(m.RabbitPluginsMetadataProvider)
        provider.get_db_gateway = lambda _: gateway
        provider.get_plugin_config = lambda *_: config or {
            'metadata_collect_cover': True, 'metadata_cover_overwrite': True}
        with patch.object(m, '_stored_cover_exists', return_value=True):
            return m._reconcile_external_covers(provider, 'general')

    def remember(self, gateway, **kwargs):
        m._remember_external_cover(gateway, 1, 'shared.webp', 11, 'manhwa',
                                   source='series_db', **kwargs)

    def test_late_scanner_cover_is_replaced(self):
        for cover in self.scanner_covers:
            with self.subTest(cover=cover):
                self.assertEqual(self.apply(cover), (1, 'shared.webp'))

    def test_repeated_scanner_write_cannot_invalidate_a_retry_snapshot(self):
        gateway = self.gateway()
        execute = gateway.execute
        writes = []

        def scanner_before_each_cover_write(query, params=()):
            if query.startswith('UPDATE books SET cover_image = ?'):
                writes.append(1)
                execute('UPDATE books SET cover_image = ?',
                        (f'11/episode04__bo_cover_{len(writes)}.webp',))
            return execute(query, params)

        gateway.execute = scanner_before_each_cover_write
        self.assertEqual(m._commit_shared_webtoon_cover(gateway, self.row, 'shared.webp', True), 1)
        self.assertEqual(gateway.fetch_one('SELECT cover_image FROM books')['cover_image'], 'shared.webp')
        self.assertEqual(len(writes), 1)

    def test_manual_cover_change_is_preserved(self):
        self.assertEqual(self.apply('manual.webp'), (0, 'manual.webp'))

    def test_scanner_marker_requires_literal_underscores(self):
        cover = '11/episode04XXboXcoverXmanual.webp'
        self.assertEqual(self.apply(cover), (0, cover))

    def test_legacy_cover_for_a_different_file_is_preserved(self):
        cover = f'11/book_{hashlib.md5(b"/other.cbz").hexdigest()}.webp'
        self.assertEqual(self.apply(cover), (0, cover))

    def test_lock_path_library_move_and_deletion_are_preserved(self):
        for changes in ({'locked': 1}, {'path': '/moved.cbz'}, {'library': 12}, {'deleted': 1}):
            with self.subTest(changes=changes):
                self.assertEqual(self.apply(self.scanner_cover, **changes), (0, self.scanner_cover))

    def test_overwrite_disabled_does_not_replace_scanner_cover(self):
        gateway = self.gateway()
        self.assertEqual(m._commit_shared_webtoon_cover(gateway, self.row, 'shared.webp', False), 0)
        self.assertEqual(gateway.fetch_one('SELECT cover_image FROM books')['cover_image'], self.scanner_cover)

    def test_timestamp_option_is_preserved(self):
        for timestamp in (False, True):
            with self.subTest(timestamp=timestamp):
                gateway = self.gateway()
                self.assertEqual(m._commit_shared_webtoon_cover(
                    gateway, self.row, 'shared.webp', True, timestamp=timestamp), 1)
                self.assertEqual(bool(gateway.fetch_one('SELECT cover_updated_at FROM books')['cover_updated_at']), timestamp)

    def test_already_selected_cover_with_zero_changed_rows_still_succeeds(self):
        gateway = self.gateway('shared.webp')
        gateway.execute = lambda *_: 0  # MariaDB reports changed, rather than matched, rows.
        self.assertEqual(m._commit_shared_webtoon_cover(
            gateway, self.row, 'shared.webp', True, timestamp=False), 1)

    def test_persisted_cover_survives_a_later_scanner_write(self):
        for cover in self.scanner_covers:
            with self.subTest(cover=cover):
                gateway = self.gateway()
                self.assertEqual(m._commit_shared_webtoon_cover(gateway, self.row, 'shared.webp', True), 1)
                self.remember(gateway, file_path=self.row['file_path'])
                gateway.execute('UPDATE books SET cover_image = ?', (cover,))
                self.assertEqual(self.restore(gateway), 1)
                self.assertEqual(gateway.fetch_one('SELECT cover_image FROM books')['cover_image'], 'shared.webp')
                self.assertEqual(self.restore(gateway), 0)

    def test_restore_preserves_manual_or_new_external_cover(self):
        for cover in ('manual.webp', '11/new__bo_external_abc.webp'):
            with self.subTest(cover=cover):
                gateway = self.gateway(cover)
                self.remember(gateway, file_path=self.row['file_path'])
                self.assertEqual(self.restore(gateway), 0)
                self.assertEqual(gateway.fetch_one('SELECT cover_image FROM books')['cover_image'], cover)

    def test_restore_preserves_lock_path_library_and_deleted_changes(self):
        for changes in ({'locked': 1}, {'path': '/moved.cbz'}, {'library': 12}, {'deleted': 1}):
            with self.subTest(changes=changes):
                gateway = self.gateway(**changes)
                self.remember(gateway, file_path=self.row['file_path'])
                self.assertEqual(self.restore(gateway), 0)

    def test_restore_respects_cover_collection_and_overwrite_settings(self):
        configs = (
            {'metadata_collect_cover': False, 'metadata_cover_overwrite': True},
            {'metadata_collect_cover': True, 'metadata_cover_overwrite': False},
            {'metadata_collect_cover': True, 'metadata_cover_overwrite': True,
             'metadata_cover_kinds': 'manga'},
        )
        for config in configs:
            with self.subTest(config=config):
                gateway = self.gateway()
                self.remember(gateway, file_path=self.row['file_path'])
                self.assertEqual(self.restore(gateway, config), 0)

    def test_legacy_preference_without_path_still_restores(self):
        gateway = self.gateway()
        self.remember(gateway)
        self.assertEqual(self.restore(gateway), 1)


if __name__ == '__main__':
    unittest.main()
