import sqlite3
import unittest
from .. import rabbit_plugins as m


class CoverCommitRaceTests(unittest.TestCase):
    def test_download_cannot_replace_concurrent_cover_or_moved_book(self):
        for winner in ('internal.webp', 'moved', 'locked', 'unchanged'):
            with self.subTest(winner=winner), sqlite3.connect(':memory:') as db:
                db.execute('CREATE TABLE books(id INTEGER, file_path TEXT, cover_image TEXT, metadata_locked INTEGER, is_deleted INTEGER, cover_updated_at TEXT)')
                cover = 'internal.webp' if winner == 'internal.webp' else ''
                db.execute('INSERT INTO books VALUES(1,?,?,?,0,NULL)', ('/new' if winner == 'moved' else '/old', cover, int(winner == 'locked')))
                m._commit_external_cover(db, {'id':1,'file_path':'/old','cover_image':''}, 'external.webp', False)
                self.assertEqual(db.execute('SELECT cover_image FROM books').fetchone()[0], 'external.webp' if winner == 'unchanged' else cover)

    def test_explicit_overwrite_still_replaces_cover(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE books(id INTEGER, file_path TEXT, cover_image TEXT, metadata_locked INTEGER, is_deleted INTEGER, cover_updated_at TEXT)')
            db.execute("INSERT INTO books VALUES(1,'/old','internal.webp',0,0,NULL)")
            m._commit_external_cover(db, {'id':1,'file_path':'/old'}, 'external.webp', True)
            self.assertEqual(db.execute('SELECT cover_image FROM books').fetchone()[0], 'external.webp')
