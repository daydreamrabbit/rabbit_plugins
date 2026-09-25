import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .. import rabbit_plugins as m


class OwnedRelationTests(unittest.TestCase):
    def _relations(self, title, native, related_native, localized, relation_field,
                   source_kind='manga', related_kind='manga', target_kind='manga',
                   target_series_name=None, related_library_id=2):
        with tempfile.TemporaryDirectory() as directory:
            index = Path(directory) / 'lookup.sqlite'
            with sqlite3.connect(index) as db:
                db.execute('CREATE TABLE series_lookup (id INTEGER, series_type TEXT, native_title TEXT, native_key TEXT, ko_titles TEXT, manga_updates_id TEXT)')
                db.execute('CREATE TABLE series_title_lookup (series_id INTEGER, title_key TEXT)')
                db.executemany('INSERT INTO series_lookup VALUES (?, ?, ?, ?, ?, ?)', [
                    (1, source_kind, native, m._title_key(native), f'["{title}"]', ''),
                    (2, related_kind, related_native, m._title_key(related_native), '[]', ''),
                ])
                db.execute('INSERT INTO series_title_lookup VALUES (?, ?)', (1, m._series_title_key(title)))

            provider = object.__new__(m.RabbitPluginsMetadataProvider)
            provider._series_index = lambda: index
            root = {field: '' for field in m.RELATION_FIELDS.values()}
            root.update({relation_field: '[2]',
                         'source_manga_updates_response_recommendations': '[]'})
            provider._source_series_rows = lambda ids: {1: root} if 1 in ids else {}
            provider._similar_data = lambda *args, **kwargs: {'items': []}
            provider._random_genre_tag_recommendations = lambda *args, **kwargs: []

            class Gateway:
                def fetch_all(self, *_):
                    return [{'id': 10, 'library_id': related_library_id, 'content_kind': related_kind,
                             'series_name': title, 'series_alias': None,
                             'localized_series': localized, 'cover_image': '', 'cover_updated_at': None,
                             'first_cover': '', 'first_cover_updated_at': None,
                             'author': '', 'publisher': '', 'books_lv': '', 'genre': '',
                             'tags': '', 'file_path': '', 'file_format': '',
                             'file_mtime': None, 'file_size': 0}]

            target = {'id': 20, 'series_name': target_series_name or title, 'series_alias': None,
                      'localized_series': localized, 'library_id': 13, 'content_kind': target_kind}
            return provider._discovery_data(
                Gateway(), target, '', [], lambda _: True, None, 'general', 10)

    def test_unowned_relation_without_korean_title_does_not_reuse_current_book(self):
        result = self._relations('나나', 'NANA', 'NANA Fanbook', '', 'relationships_spin_off')
        self.assertEqual(result['relations'], {})

    def test_alternative_with_same_native_title_does_not_link_current_series(self):
        result = self._relations('나의 아내는 조금 무섭다', '僕の奥さんはちょっと怖い',
                                 '僕の奥さんはちょっと怖い', '僕の奥さんはちょっと怖い',
                                 'relationships_alternative', related_library_id=13)
        self.assertEqual(result['relations'], {})

    def test_owned_manga_adaptation_with_same_title_as_novel_is_related(self):
        title = '10년 만에 재회한 건방진 꼬맹이는 청순 미소녀 여고생으로 성장해 있었다'
        native = '10年ぶりに再会したクソガキは清純美少女JKに成長していた'
        result = self._relations(
            title, native, native, native, 'relationships_adaptation',
            source_kind='novel', related_kind='manga', target_kind='novel',
            target_series_name=title + ' [칸자이 유키]')
        self.assertEqual(result['relations']['adaptation'][0]['series_name'], title)
