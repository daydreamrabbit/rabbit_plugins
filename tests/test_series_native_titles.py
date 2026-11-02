import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from flask import Flask
from .. import rabbit_plugins as m


class SeriesNativeTitleTests(unittest.TestCase):
    def test_only_explicit_native_aliases_are_identity_titles(self):
        entries = [
            {'title': '正式な原題', 'language': 'ja', 'traits': ['native'], 'is_primary': True},
            {'title': '日本語の略称', 'language': 'ja', 'traits': ['alternative']},
            {'title': '한국어 번역 제목', 'language': 'ko', 'traits': [], 'is_primary': True},
            {'title': '正式な原題', 'traits': ['native']},
            {'title': '잘못된 항목', 'traits': 'native'},
        ]
        self.assertEqual(m._native_titles('代表原題', json.dumps(entries)), ['代表原題', '正式な原題'])
        self.assertEqual(m._native_titles('代表原題', 'not json'), ['代表原題'])
        self.assertEqual(m._native_titles('', json.dumps(entries)), ['正式な原題'])

    def test_existing_current_index_rebuilds_once_with_native_alias_lookup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'series.sqlite'
            alias = 'ダンジョンに出会いを求めるのは間違っているだろうか'
            canonical = 'ダンジョンに出会いを求めるのは間違っているのだろうか'
            titles = json.dumps([{'title': alias, 'language': 'ja', 'traits': ['native']}])
            with sqlite3.connect(source) as db:
                db.execute('CREATE TABLE series (id INTEGER PRIMARY KEY, type TEXT, native_title TEXT, '
                           'titles TEXT, secondary_titles_ko TEXT, source_manga_updates_id TEXT)')
                db.execute('INSERT INTO series VALUES (1, ?, ?, ?, ?, ?)',
                           ('manga', canonical, titles, '[]', ''))
            original = source.read_bytes()
            stat = source.stat()
            cache = root / 'cache'
            cache.mkdir()
            index = cache / 'rabbit_plugins_series_index.sqlite'
            with sqlite3.connect(index) as db:
                db.execute('CREATE TABLE series_lookup (id INTEGER, series_type TEXT, native_title TEXT, '
                           'native_key TEXT, ko_titles TEXT, manga_updates_id TEXT)')
                db.execute('CREATE TABLE series_title_lookup (series_id INTEGER, title_key TEXT)')
                db.execute('CREATE TABLE index_meta (source_path TEXT, source_size INTEGER, source_mtime_ns INTEGER)')
                db.execute('INSERT INTO index_meta VALUES (?, ?, ?)', (str(source), stat.st_size, stat.st_mtime_ns))
            provider = object.__new__(m.RabbitPluginsMetadataProvider)
            provider._series_db_path = lambda: source
            app = Flask('native-alias-index', root_path=str(root))
            with app.app_context():
                self.assertEqual(provider._series_index(), index)
                built_mtime = index.stat().st_mtime_ns
                self.assertEqual(provider._series_index(), index)
                self.assertEqual(index.stat().st_mtime_ns, built_mtime)
            self.assertEqual(source.read_bytes(), original)
            with sqlite3.connect(index) as db:
                stored = db.execute('SELECT native_titles FROM series_lookup WHERE id=1').fetchone()[0]
                self.assertEqual(json.loads(stored), [canonical, alias])
                for title in (canonical, alias):
                    self.assertEqual(db.execute(
                        'SELECT series_id FROM series_native_title_lookup WHERE title_key=?',
                        (m._title_key(title),)).fetchall(), [(1,)])
                plan = db.execute('EXPLAIN QUERY PLAN SELECT series_id FROM series_native_title_lookup '
                                  'WHERE title_key=?', (m._title_key(alias),)).fetchall()
                self.assertTrue(any('idx_series_native_title_lookup_key' in str(row) for row in plan))


if __name__ == '__main__':
    unittest.main()
