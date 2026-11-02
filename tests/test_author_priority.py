"""Provider credits must respect existing metadata and Series.db's identity role."""
import sqlite3
import unittest
from unittest.mock import patch

from .. import collection_history, rabbit_plugins as m


class CreditGateway:
    """Exercise the production SQL against a small, real library database."""

    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE settings (`key` TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE libraries (id INTEGER PRIMARY KEY, content_kind TEXT);
            INSERT INTO libraries VALUES (3, 'manga');
            CREATE TABLE books (
                id INTEGER PRIMARY KEY, library_id INTEGER, series_name TEXT,
                title TEXT, title_alias TEXT DEFAULT '', file_path TEXT,
                cover_image TEXT DEFAULT '', metadata_locked INTEGER DEFAULT 0,
                is_deleted INTEGER DEFAULT 0, author TEXT DEFAULT '',
                cover_artist TEXT DEFAULT '', translator TEXT DEFAULT '',
                isbn TEXT DEFAULT '', publisher TEXT DEFAULT '',
                summary TEXT DEFAULT '', link TEXT DEFAULT '',
                genre TEXT DEFAULT '', tags TEXT DEFAULT '',
                release_date TEXT DEFAULT '', localized_series TEXT DEFAULT '',
                document_volume_index REAL, document_volume_count REAL,
                cover_updated_at TEXT, publication_status TEXT, books_lv TEXT
            );
        ''')
        for book_id in (1, 2, 3):
            self.db.execute(
                'INSERT INTO books '
                '(id, library_id, series_name, title, file_path, '
                'author, cover_artist, document_volume_index) '
                'VALUES (?, 3, ?, ?, ?, ?, ?, ?)',
                (book_id, '추남 진성 파이터', f'{book_id:02}권',
                 f'/books/추남 진성 파이터/{book_id:02}권.cbz',
                 '히로마츠 료', 'KOUNO KOJI', book_id),
            )

    def fetch_all(self, query, params=()):
        return [dict(row) for row in self.db.execute(query, params)]

    def fetch_one(self, query, params=()):
        return next(iter(self.fetch_all(query, params)), None)

    def execute(self, query, params=()):
        return self.db.execute(query, params).rowcount

    def get_setting(self, key, default=None):
        return self.fetch_one('SELECT value FROM settings WHERE `key` = ?', (key,)) or default

    def set_setting(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)', (key, value))


class AuthorPriorityTests(unittest.TestCase):
    def setUp(self):
        self.gateway = CreditGateway()
        self.addCleanup(self.gateway.db.close)
        self.provider = object.__new__(m.RabbitPluginsMetadataProvider)
        token = collection_history.priority_failed.set('')
        self.addCleanup(collection_history.priority_failed.reset, token)

    def apply(self, metadata, *, overwrite=False, preserve_existing=False):
        config = {
            'metadata_sources': 'series_db,naver',
            'metadata_collect_cover': False,
            'metadata_overwrite': overwrite,
            'metadata_manual_overwrite': overwrite,
        }
        # A merged record avoids provider network access while retaining the
        # metadata application path used after automatic provider selection.
        ok, message = self.provider._apply_metadata(
            self.gateway, 2, {'source': 'merged', 'metadata': metadata}, config,
            fields=['author', 'cover_artist', 'summary'],
            manual=preserve_existing, preserve_existing=preserve_existing,
        )
        self.assertTrue(ok, message)
        return self.gateway.fetch_all(
            'SELECT id, author, cover_artist, summary FROM books ORDER BY id')

    def test_existing_writer_survives_lower_priority_credit(self):
        for incoming_artist in ('', 'KOUNO KOJI'):
            with self.subTest(incoming_artist=incoming_artist):
                self.gateway.execute('UPDATE books SET author = ?', ('히로마츠 료',))
                rows = self.apply({
                    'author': 'HIROMATSU RYO', 'cover_artist': incoming_artist,
                })
                self.assertEqual([row['author'] for row in rows], ['히로마츠 료'] * 3)

    def test_sibling_writers_survive_when_target_is_filled(self):
        self.gateway.execute('UPDATE books SET author = ? WHERE id = 2', ('',))
        rows = self.apply({'author': 'HIROMATSU RYO', 'cover_artist': 'KOUNO KOJI'})
        self.assertEqual([row['author'] for row in rows],
                         ['히로마츠 료', 'HIROMATSU RYO', '히로마츠 료'])

    def test_sibling_writers_survive_when_target_is_unchanged(self):
        self.gateway.execute('UPDATE books SET author = ? WHERE id = 2', ('HIROMATSU RYO',))
        rows = self.apply({'author': 'HIROMATSU RYO', 'cover_artist': 'KOUNO KOJI'})
        self.assertEqual([row['author'] for row in rows],
                         ['히로마츠 료', 'HIROMATSU RYO', '히로마츠 료'])

    def test_same_work_keeps_each_volumes_credits_even_with_overwrite_enabled(self):
        for target_summary in ('', '외부 소개'):
            with self.subTest(target_summary=target_summary):
                self.gateway.execute('UPDATE books SET summary = ?', ('기존 소개',))
                self.gateway.execute('UPDATE books SET summary = ? WHERE id = 2',
                                     (target_summary,))
                rows = self.apply({
                    'author': 'HIROMATSU RYO', 'cover_artist': '코노 코지',
                    'summary': '외부 소개',
                }, overwrite=True, preserve_existing=True)
                self.assertEqual([row['author'] for row in rows], ['히로마츠 료'] * 3)
                self.assertEqual([row['cover_artist'] for row in rows], ['KOUNO KOJI'] * 3)

    def test_explicit_overwrite_can_replace_existing_writer(self):
        rows = self.apply({'author': '새 작가', 'cover_artist': '새 그림작가'},
                          overwrite=True)
        self.assertEqual([row['author'] for row in rows], ['새 작가'] * 3)
        self.assertEqual([row['cover_artist'] for row in rows], ['새 그림작가'] * 3)

    def test_real_illustrator_cleanup_preserves_the_existing_writer(self):
        self.gateway.execute('UPDATE books SET author = ?', ('히로마츠 료, KOUNO KOJI',))
        rows = self.apply({'author': 'HIROMATSU RYO', 'cover_artist': 'KOUNO KOJI'})
        self.assertEqual([row['author'] for row in rows], ['히로마츠 료'] * 3)

    def test_existing_writer_artist_can_be_repaired_to_korean(self):
        self.gateway.execute('UPDATE books SET author = ?, cover_artist = ?',
                             ('Aki Akimoto', 'Aki Akimoto'))
        rows = self.apply({'author': '아키모토 아키', 'cover_artist': '아키모토 아키'})
        self.assertEqual([row['author'] for row in rows], ['아키모토 아키'] * 3)
        self.assertEqual([row['cover_artist'] for row in rows], ['아키모토 아키'] * 3)

    def test_cached_series_db_credit_fields_cannot_outrank_remote_provider(self):
        cached = {
            'source': 'series_db',
            'metadata': {
                'localized_series': 'ブサメンガチファイター',
                'link': 'https://mangabaka.org/123',
                'author': 'STALE WRITER', 'cover_artist': 'STALE ARTIST',
                'translator': 'STALE TRANSLATOR', 'summary': 'STALE SUMMARY',
            },
        }
        remote = {
            'source': 'ridi',
            'metadata': {
                'author': '히로마츠 료', 'cover_artist': '코노 코지',
                'translator': '번역가', 'summary': '리디 작품 소개',
                'link': 'https://ridibooks.com/books/123',
            },
        }
        # No candidate URLs means merge cannot perform remote detail requests.
        with patch.object(m, '_remote_fetch_metadata', side_effect=AssertionError('network')):
            merged = self.provider._merge_metadata_candidates([cached, remote], 'manga')['metadata']
        for field in ('author', 'cover_artist', 'translator', 'summary'):
            with self.subTest(field=field):
                self.assertEqual(merged[field], remote['metadata'][field])
        self.assertEqual(merged['localized_series'], cached['metadata']['localized_series'])
        self.assertIn('https://mangabaka.org/123', merged['link'])
        self.assertIn('https://ridibooks.com/books/123', merged['link'])


if __name__ == '__main__':
    unittest.main()
