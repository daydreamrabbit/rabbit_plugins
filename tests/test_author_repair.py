"""Author repair uses verified evidence and changes only the approved writer."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from .. import author_repair as repair, rabbit_plugins as m


RUN = 'a' * 32


class RepairGateway:
    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE settings (`key` TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE libraries (id INTEGER PRIMARY KEY, name TEXT, content_kind TEXT);
            INSERT INTO libraries VALUES (1, '대상 라이브러리', 'manga');
            INSERT INTO libraries VALUES (2, '다른 라이브러리', 'manga');
            CREATE TABLE books (
                id INTEGER PRIMARY KEY, library_id INTEGER, series_name TEXT,
                author TEXT, cover_artist TEXT, file_path TEXT, file_format TEXT,
                file_mtime REAL, file_size INTEGER, link TEXT,
                metadata_locked INTEGER DEFAULT 0, is_deleted INTEGER DEFAULT 0,
                title TEXT DEFAULT '권 제목', isbn TEXT DEFAULT '9791234567890',
                translator TEXT DEFAULT '기존 번역가', publisher TEXT DEFAULT '기존 출판사',
                cover_image TEXT DEFAULT 'existing-cover.webp',
                summary TEXT DEFAULT '기존 작품 소개', genre TEXT DEFAULT '기존 장르',
                tags TEXT DEFAULT '기존 태그', release_date TEXT DEFAULT '2026-01-02',
                localized_series TEXT DEFAULT '原題', books_lv TEXT DEFAULT 'M',
                publication_status TEXT DEFAULT '1'
            );
        ''')
        for book_id, library_id in ((1, 1), (2, 1), (3, 2)):
            self.db.execute(
                'INSERT INTO books (id,library_id,series_name,author,cover_artist,'
                'file_path,file_format,file_mtime,file_size,link) VALUES (?,?,?,?,?,?,?,?,?,?)',
                (book_id, library_id, '추남 진성 파이터', 'HIROMATSU RYO', 'KOUNO KOJI',
                 f'/library{library_id}/{book_id}.cbz', 'cbz', 123.5, 3456,
                 'https://ridibooks.com/books/123'),
            )

    def fetch_all(self, query, params=()):
        return [dict(row) for row in self.db.execute(query, params)]

    def fetch_one(self, query, params=()):
        return next(iter(self.fetch_all(query, params)), None)

    def execute(self, query, params=()):
        return self.db.execute(query, params).rowcount

    def get_setting(self, key, default=None):
        return self.fetch_one('SELECT value FROM settings WHERE `key`=?', (key,)) or default

    def set_setting(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, value))


class AuthorRepairTests(unittest.TestCase):
    def setUp(self):
        self.gateway = RepairGateway()
        self.addCleanup(self.gateway.db.close)
        lock_dir = tempfile.TemporaryDirectory()
        self.addCleanup(lock_dir.cleanup)
        lock_patch = patch.object(repair, '_lock_path',
                                  side_effect=lambda db: Path(lock_dir.name) / f'{db}.lock')
        lock_patch.start()
        self.addCleanup(lock_patch.stop)
        self.provider = SimpleNamespace(
            get_db_gateway=lambda _db: self.gateway,
            get_plugin_config=lambda *_: {'metadata_sources': 'series_db,ridi,naver'},
        )
        self.network = patch.object(m, '_remote_fetch_metadata',
                                    side_effect=AssertionError('Unexpected network request'))
        self.remote = self.network.start()
        self.addCleanup(self.network.stop)
        self.invalidate_patch = patch('services.series_service.SeriesService.invalidate_all_books_cache')
        self.invalidate = self.invalidate_patch.start()
        self.addCleanup(self.invalidate_patch.stop)

    def scan(self, embedded=None, *, library_id=1, run_id=RUN):
        state = {'run_id': run_id, 'status': 'running', 'checked': 0,
                 'total': 0, 'warnings': []}
        with patch.object(repair, '_read_embedded', return_value=(embedded or {}, False)):
            repair._scan(self.provider, 'general', state, {'library_id': library_id})
        return state

    def entries(self, run_id=RUN):
        return repair._entries(self.gateway, run_id)

    def book(self, book_id=1):
        return self.gateway.fetch_one('SELECT * FROM books WHERE id=?', (book_id,))

    def journal(self, book_id=1):
        key = f'{repair.JOURNAL_PREFIX}{RUN}:{book_id}'
        row = self.gateway.get_setting(key)
        return json.loads(row['value']) if row else None

    def apply(self, ids=None):
        return repair.apply(self.provider, 'general', {'book_ids': [1] if ids is None else ids})

    def test_comparison_does_not_modify_books(self):
        before = self.gateway.fetch_all('SELECT * FROM books ORDER BY id')
        self.scan({'author': '히로마츠 료'})
        self.assertEqual(self.gateway.fetch_all('SELECT * FROM books ORDER BY id'), before)
        self.assertEqual([entry['status'] for entry in self.entries()], ['repairable'] * 2)
        self.remote.assert_not_called()

    def test_apply_changes_only_author_and_persists_before_after_journal(self):
        before = {row['id']: row for row in self.gateway.fetch_all('SELECT * FROM books')}
        self.scan({'author': '히로마츠 료'})
        result = self.apply()
        self.assertTrue(result['success'])
        self.assertEqual(result['updated_books'], 2)
        self.assertEqual(result['updated_series'], 1)
        for row in self.gateway.fetch_all('SELECT * FROM books'):
            expected = dict(before[row['id']])
            if row['library_id'] == 1:
                expected['author'] = '히로마츠 료'
            self.assertEqual(row, expected)
        log = self.journal()
        self.assertEqual(log['status'], 'applied')
        self.assertEqual(log['current_author'], 'HIROMATSU RYO')
        self.assertEqual(log['proposed_author'], '히로마츠 료')
        self.assertEqual(log['file_path'], before[1]['file_path'])
        self.assertTrue(log['source'])
        self.invalidate.assert_called_once_with('general')

    def test_cas_preserves_concurrent_changes_to_every_identity_guard(self):
        changes = [
            ('author', 'hiromatsu ryo'),  # Exact comparison includes case.
            ('file_path', '/LIBRARY1/1.cbz'),
            ('series_name', '다른 작품'),
            ('library_id', 2),
            ('file_mtime', 999),
            ('file_size', 999),
            ('metadata_locked', 1),
            ('is_deleted', 1),
        ]
        for column, value in changes:
            with self.subTest(column=column):
                gateway = RepairGateway()
                self.addCleanup(gateway.db.close)
                row = gateway.fetch_one(
                    'SELECT b.*, l.name AS library_name, l.content_kind FROM books b '
                    'JOIN libraries l ON l.id=b.library_id WHERE b.id=1')
                entry = {**row, 'book_id': 1, 'run_id': RUN,
                         'current_author': row['author'], 'proposed_author': '히로마츠 료',
                         'status': 'repairable', 'source': 'ComicInfo.xml'}
                gateway.execute(f'UPDATE books SET {column}=? WHERE id=1', (value,))
                before = gateway.fetch_one('SELECT * FROM books WHERE id=1')
                self.assertEqual(repair._commit(gateway, entry), 0)
                self.assertEqual(gateway.fetch_one('SELECT * FROM books WHERE id=1'), before)
                saved = gateway.get_setting(f'{repair.JOURNAL_PREFIX}{RUN}:1')
                self.assertEqual(json.loads(saved['value'])['status'], 'conflict')

    def test_legitimate_english_writer_is_not_rewritten_without_evidence(self):
        self.provider.get_plugin_config = lambda *_: {'metadata_sources': 'series_db,naver'}
        self.scan()
        self.assertTrue(all(entry['status'] == 'unchanged' for entry in self.entries()))
        self.assertEqual(self.book()['author'], 'HIROMATSU RYO')
        self.remote.assert_not_called()

    def test_matching_embedded_writer_prevents_external_spelling_replacement(self):
        self.remote.side_effect = None
        self.remote.return_value = {'title': '추남 진성 파이터', 'author': '히로마츠 료'}
        self.scan({'author': 'HIROMATSU RYO'})
        self.assertTrue(all(entry['status'] == 'unchanged' for entry in self.entries()))
        self.remote.assert_not_called()

    def test_english_pen_name_is_preserved_while_verified_translator_is_removed(self):
        self.gateway.execute(
            'UPDATE books SET author=?, cover_artist=?, translator=?, file_format=? WHERE library_id=1',
            ('kiki, 조민경', '킨타', '조민경', 'epub'))
        self.assertTrue(repair._suspect({'author': 'kiki, 조민경',
                                       'cover_artist': '킨타', 'translator': '조민경'}))
        self.assertTrue(repair._suspect({'author': 'kiki, 조민경',
                                       'cover_artist': '', 'translator': ''}))
        self.assertFalse(repair._suspect({'author': '챔프D 편집부',
                                        'cover_artist': '', 'translator': ''}))
        before = self.book()
        self.scan({'author': 'kiki', 'translator': '조민경', 'cover_artist': '킨타'})
        self.assertEqual({entry['proposed_author'] for entry in self.entries()}, {'kiki'})
        self.assertEqual({entry['source'] for entry in self.entries()}, {'EPUB 내장 메타데이터'})
        self.assertEqual(self.apply()['updated_books'], 2)
        expected = dict(before, author='kiki')
        self.assertEqual(self.book(), expected)
        self.remote.assert_not_called()

    def test_unknown_additional_writer_is_not_removed_from_english_pen_name(self):
        self.provider.get_plugin_config = lambda *_: {'metadata_sources': 'series_db'}
        self.gateway.execute(
            'UPDATE books SET author=?, cover_artist=?, translator=?, file_format=? WHERE library_id=1',
            ('kiki, 조민경, 공동집필자', '킨타', '조민경', 'epub'))
        before = self.gateway.fetch_all('SELECT * FROM books ORDER BY id')
        self.scan({'author': 'kiki', 'translator': '조민경', 'cover_artist': '킨타'})
        self.assertTrue(all(entry['status'] == 'unchanged' for entry in self.entries()))
        self.assertEqual(self.apply()['updated_books'], 0)
        self.assertEqual(self.gateway.fetch_all('SELECT * FROM books ORDER BY id'), before)
        self.remote.assert_not_called()

    def test_verified_ridi_repairs_mixed_english_writer_and_artist_from_archive(self):
        title = '쌀쌀맞은 사토 양이 나에게만 상냥하다'
        contaminated = 'Kaya Tetsuyama, Kazami Sawatari'
        self.gateway.execute('UPDATE books SET series_name=?, author=? WHERE library_id=1',
                             (title, contaminated))
        self.remote.side_effect = None
        self.remote.return_value = {
            'title': title + ' @comic', 'author': '사와타리 카자미',
            'cover_artist': '테츠야마 카야',
        }
        self.scan({'author': contaminated})
        self.assertEqual([entry['proposed_author'] for entry in self.entries()],
                         ['사와타리 카자미'] * 2)
        self.assertEqual({entry['source'] for entry in self.entries()}, {'리디'})
        self.remote.assert_called_once_with('https://ridibooks.com/books/123', 'ridi')
        self.assertEqual(self.apply()['updated_books'], 2)
        self.assertEqual(self.book()['author'], '사와타리 카자미')
        self.assertEqual(self.book()['cover_artist'], 'KOUNO KOJI')

    def test_comic_suffix_does_not_match_a_novel_library(self):
        title = '쌀쌀맞은 사토 양이 나에게만 상냥하다'
        contaminated = 'Kaya Tetsuyama, Kazami Sawatari'
        self.gateway.execute('UPDATE libraries SET content_kind=? WHERE id=1', ('novel',))
        self.gateway.execute('UPDATE books SET series_name=?, author=? WHERE library_id=1',
                             (title, contaminated))
        self.remote.side_effect = None
        self.remote.return_value = {'title': title + ' @comic', 'author': '사와타리 카자미'}
        self.scan({'author': contaminated})
        self.assertTrue(all(entry['status'] == 'unchanged' for entry in self.entries()))
        self.assertEqual(self.book()['author'], contaminated)

    def test_unavailable_ridi_keeps_current_authors_and_blocks_extra_probes(self):
        self.remote.side_effect = None
        self.remote.return_value = {'_fetch_error': 'HTTPError'}
        state = self.scan()
        self.assertTrue(all(entry['status'] == 'unchanged' for entry in self.entries()))
        self.assertTrue(state['warnings'])
        self.remote.assert_called_once_with('https://ridibooks.com/books/123', 'ridi')
        self.assertEqual(self.book()['author'], 'HIROMATSU RYO')

    def test_mismatched_ridi_work_cannot_supply_author(self):
        self.remote.side_effect = None
        self.remote.return_value = {'title': '다른 작품', 'author': '다른 작가'}
        self.scan()
        self.assertTrue(all(entry['status'] == 'unchanged' for entry in self.entries()))
        self.assertEqual(self.book()['author'], 'HIROMATSU RYO')

    def test_verified_linked_ridi_is_fetched_once_for_shared_product(self):
        self.remote.side_effect = None
        self.remote.return_value = {'title': '추남 진성 파이터', 'author': '히로마츠 료'}
        self.scan()
        self.assertEqual([entry['proposed_author'] for entry in self.entries()], ['히로마츠 료'] * 2)
        self.remote.assert_called_once_with('https://ridibooks.com/books/123', 'ridi')

    def test_journal_failure_prevents_author_write(self):
        self.scan({'author': '히로마츠 료'})
        entry = self.entries()[0]
        original = self.gateway.set_setting

        def fail_journal(key, value):
            if key.startswith(repair.JOURNAL_PREFIX):
                raise RuntimeError('journal storage unavailable')
            return original(key, value)

        before = self.book(entry['book_id'])
        with patch.object(self.gateway, 'set_setting', side_effect=fail_journal):
            with self.assertRaises(RuntimeError):
                repair._commit(self.gateway, entry)
        self.assertEqual(self.book(entry['book_id']), before)

    def test_postwrite_journal_failure_keeps_prepared_evidence_and_invalidates(self):
        self.scan({'author': '히로마츠 료'})
        original = self.gateway.set_setting

        def fail_completed_journal(key, value):
            if key.startswith(repair.JOURNAL_PREFIX) and json.loads(value)['status'] == 'applied':
                raise RuntimeError('completed journal storage unavailable')
            return original(key, value)

        with patch.object(self.gateway, 'set_setting', side_effect=fail_completed_journal):
            result = self.apply()
        self.assertEqual(result['updated_books'], 2)
        self.assertTrue(result['warnings'])
        self.assertEqual(self.book()['author'], '히로마츠 료')
        self.assertEqual(self.journal()['status'], 'prepared')
        self.assertEqual(self.journal()['current_author'], 'HIROMATSU RYO')
        self.assertEqual(self.journal()['proposed_author'], '히로마츠 료')
        self.invalidate.assert_called_once_with('general')

    def test_retry_recovers_prepared_journal_without_rewriting_authors(self):
        self.scan({'author': '히로마츠 료'})
        original = self.gateway.set_setting

        def fail_completed_journal(key, value):
            if key.startswith(repair.JOURNAL_PREFIX) and json.loads(value)['status'] == 'applied':
                raise RuntimeError('completed journal storage unavailable')
            return original(key, value)

        with patch.object(self.gateway, 'set_setting', side_effect=fail_completed_journal):
            self.assertEqual(self.apply()['updated_books'], 2)
        before = self.gateway.fetch_all('SELECT * FROM books ORDER BY id')
        self.invalidate.reset_mock()
        with patch.object(self.gateway, 'execute', wraps=self.gateway.execute) as execute:
            result = self.apply()
        self.assertEqual(result['updated_books'], 0)
        self.assertEqual(result['already_applied'], 2)
        self.assertFalse(result['warnings'])
        self.assertEqual(self.gateway.fetch_all('SELECT * FROM books ORDER BY id'), before)
        self.assertFalse(any(call.args[0].startswith('UPDATE books')
                             for call in execute.call_args_list))
        self.assertEqual(self.journal()['status'], 'applied')
        self.assertEqual(self.journal()['current_author'], 'HIROMATSU RYO')
        self.assertEqual(self.journal()['proposed_author'], '히로마츠 료')
        self.assertEqual({entry['status'] for entry in self.entries()}, {'applied'})
        self.invalidate.assert_not_called()

    def test_retry_keeps_user_edit_and_successful_journal_after_entry_save_failure(self):
        self.scan({'author': '히로마츠 료'})
        original = self.gateway.set_setting

        def fail_entry_save(key, value):
            if key.startswith(repair.ENTRY_PREFIX):
                raise RuntimeError('completed entry storage unavailable')
            return original(key, value)

        with patch.object(self.gateway, 'set_setting', side_effect=fail_entry_save):
            self.assertEqual(self.apply()['updated_books'], 2)
        first_journal = self.journal()
        self.assertEqual(first_journal['status'], 'applied')
        self.gateway.execute('UPDATE books SET author=? WHERE id=1', ('사용자 수정 작가',))
        before = self.gateway.fetch_all('SELECT * FROM books ORDER BY id')
        result = self.apply()
        self.assertEqual(result['updated_books'], 0)
        self.assertEqual(result['already_applied'], 1)
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(self.gateway.fetch_all('SELECT * FROM books ORDER BY id'), before)
        self.assertEqual(self.journal(), first_journal)
        statuses = {entry['book_id']: entry['status'] for entry in self.entries()}
        self.assertEqual(statuses, {1: 'conflict', 2: 'applied'})

    def test_invalid_ids_are_rejected_before_writing(self):
        self.scan({'author': '히로마츠 료'})
        before = self.gateway.fetch_all('SELECT * FROM books ORDER BY id')
        for ids in ([True], [False], [1.9], [0], [-1], ['bad'], [{}], [], '1'):
            with self.subTest(ids=ids):
                result = self.apply(ids)
                self.assertFalse(result['success'])
                self.assertEqual(self.gateway.fetch_all('SELECT * FROM books ORDER BY id'), before)
        self.invalidate.assert_not_called()

    def test_repair_scope_does_not_cross_libraries_with_same_series_title(self):
        self.scan({'author': '히로마츠 료'})
        self.assertEqual({entry['library_id'] for entry in self.entries()}, {1})
        self.apply()
        self.assertEqual(self.book(3)['author'], 'HIROMATSU RYO')
        self.assertIsNone(self.journal(3))

    def test_narrow_scope_compares_only_named_series_in_selected_library(self):
        self.gateway.execute('UPDATE books SET series_name=? WHERE id=2', ('다른 작품',))
        state = {'run_id': RUN, 'status': 'running', 'checked': 0, 'total': 0, 'warnings': []}
        with patch.object(repair, '_read_embedded', return_value=({'author': '히로마츠 료'}, False)) as read:
            repair._scan(self.provider, 'general', state,
                         {'library_id': 1, 'series_name': '추남 진성 파이터'})
        self.assertEqual(read.call_count, 1)
        self.assertEqual(state['total'], 1)
        self.assertEqual([entry['book_id'] for entry in self.entries()], [1])
        self.assertEqual(self.apply()['updated_books'], 1)
        self.assertEqual(self.book(2)['author'], 'HIROMATSU RYO')
        self.assertEqual(self.book(3)['author'], 'HIROMATSU RYO')

    def test_same_book_id_in_other_database_is_not_modified(self):
        adult_gateway = RepairGateway()
        self.addCleanup(adult_gateway.db.close)
        self.provider.get_db_gateway = lambda db: adult_gateway if db == 'adult' else self.gateway
        self.scan({'author': '히로마츠 료'})
        self.apply()
        self.assertEqual(adult_gateway.fetch_one('SELECT author FROM books WHERE id=1')['author'],
                         'HIROMATSU RYO')
        self.assertIsNone(adult_gateway.get_setting(repair.STATE_KEY))
        self.assertEqual(adult_gateway.fetch_all('SELECT * FROM settings'), [])

    def test_repeated_embedded_timeouts_stop_comparison_without_edits(self):
        self.gateway.execute(
            'INSERT INTO books (id,library_id,series_name,author,cover_artist,file_path,'
            'file_format,file_mtime,file_size,link) '
            'SELECT 4,library_id,series_name,author,cover_artist,?,file_format,file_mtime,file_size,link '
            'FROM books WHERE id=1', ('/library1/4.cbz',))
        self.gateway.execute(
            'INSERT INTO books (id,library_id,series_name,author,cover_artist,file_path,'
            'file_format,file_mtime,file_size,link) '
            'SELECT 5,library_id,series_name,author,cover_artist,?,file_format,file_mtime,file_size,link '
            'FROM books WHERE id=1', ('/library1/5.cbz',))
        self.provider.get_plugin_config = lambda *_: {'metadata_sources': 'series_db'}
        before = self.gateway.fetch_all('SELECT * FROM books ORDER BY id')
        state = {'run_id': RUN, 'status': 'running', 'checked': 0, 'total': 0, 'warnings': []}
        with patch.object(repair, '_read_embedded', return_value=({}, True)) as read:
            repair._scan(self.provider, 'general', state, {'library_id': 1})
        self.assertEqual(read.call_count, 3)
        self.assertEqual(state['checked'], 3)
        self.assertEqual(state['total'], 4)
        self.assertEqual(state['status'], 'interrupted')
        self.assertTrue(state['warnings'])
        self.assertEqual(self.gateway.fetch_all('SELECT * FROM books ORDER BY id'), before)

    def test_positive_numeric_string_ids_are_accepted(self):
        self.scan({'author': '히로마츠 료'})
        self.assertEqual(self.apply(['1'])['updated_books'], 2)

    def test_malformed_persisted_entries_are_not_applied(self):
        self.scan({'author': '히로마츠 료'})
        for field, value in (('book_id', True), ('library_id', 0),
                             ('run_id', 'b' * 32), ('proposed_author', ['unsafe'])):
            with self.subTest(field=field):
                entry = dict(self.entries()[0])
                entry[field] = value
                self.gateway.set_setting(repair._entry_key(RUN, 1), json.dumps(entry))
                self.assertTrue(all(row['book_id'] != 1 for row in self.entries()))
                # Restore a fresh run before checking a different malformed field.
                self.scan({'author': '히로마츠 료'})

    def test_old_run_entries_cannot_be_applied_by_book_id(self):
        self.scan({'author': '히로마츠 료'})
        self.provider.get_plugin_config = lambda *_: {'metadata_sources': 'series_db'}
        self.scan(run_id='b' * 32)
        result = self.apply()
        self.assertEqual(result['updated_books'], 0)
        self.assertEqual(self.book()['author'], 'HIROMATSU RYO')
        self.assertIsNone(self.journal())

    def test_repeated_apply_keeps_successful_audit_record(self):
        self.scan({'author': '히로마츠 료'})
        self.assertEqual(self.apply()['updated_books'], 2)
        first = self.journal()
        self.assertEqual(self.apply()['updated_books'], 0)
        self.assertEqual(self.journal(), first)

    def test_locked_and_deleted_books_are_not_compared(self):
        self.gateway.execute('UPDATE books SET metadata_locked=1 WHERE id=1')
        self.gateway.execute('UPDATE books SET is_deleted=1 WHERE id=2')
        state = self.scan({'author': '히로마츠 료'})
        self.assertEqual(state['total'], 0)
        self.assertEqual(self.entries(), [])

    def test_suspect_detects_korean_multiwriter_artist_overlap_only_for_review(self):
        self.assertFalse(repair._suspect({'author': '챔프D 편집부', 'cover_artist': ''}))
        self.assertTrue(repair._suspect({'author': '히로마츠 료, 코노 코지', 'cover_artist': '코노 코지'}))
        self.assertTrue(repair._suspect({'author': 'HIROMATSU RYO', 'cover_artist': ''}))
        self.assertFalse(repair._suspect({'author': '히로마츠 료', 'cover_artist': '히로마츠 료'}))
        self.assertFalse(repair._suspect({'author': '작가 가, 작가 나', 'cover_artist': '다른 그림작가'}))
        self.assertFalse(repair._suspect({'author': '', 'cover_artist': '그림작가'}))


if __name__ == '__main__':
    unittest.main()
