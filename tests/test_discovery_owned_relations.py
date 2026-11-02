import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .. import rabbit_plugins as m


class OwnedRelationTests(unittest.TestCase):
    def _relations(self, title, native, related_native, localized, relation_field,
                   source_kind='manga', related_kind='manga', target_kind='manga',
                   target_series_name=None, related_library_id=2, related_files=None,
                   source_native_titles=None, related_native_titles=None,
                   source_korean_titles=None, related_korean_titles=None,
                   target_localized=None, visible=None, check_rating=None, queries=None,
                   extra_index_rows=None, extra_source_rows=None, root_fields=None):
        source_native_titles = source_native_titles if source_native_titles is not None else [native]
        related_native_titles = related_native_titles if related_native_titles is not None else [related_native]
        source_korean_titles = source_korean_titles if source_korean_titles is not None else [title]
        related_korean_titles = related_korean_titles if related_korean_titles is not None else []
        with tempfile.TemporaryDirectory() as directory:
            index = Path(directory) / 'lookup.sqlite'
            with sqlite3.connect(index) as db:
                db.execute('CREATE TABLE series_lookup (id INTEGER, series_type TEXT, native_title TEXT, native_key TEXT, ko_titles TEXT, manga_updates_id TEXT, native_titles TEXT)')
                db.execute('CREATE TABLE series_title_lookup (series_id INTEGER, title_key TEXT)')
                db.execute('CREATE TABLE series_native_title_lookup (series_id INTEGER, title_key TEXT)')
                db.executemany('INSERT INTO series_lookup VALUES (?, ?, ?, ?, ?, ?, ?)', [
                    (1, source_kind, native, m._title_key(native), json.dumps(source_korean_titles), '',
                     json.dumps(source_native_titles)),
                    (2, related_kind, related_native, m._title_key(related_native), json.dumps(related_korean_titles), '',
                     json.dumps(related_native_titles)),
                ])
                if extra_index_rows:
                    db.executemany('INSERT INTO series_lookup VALUES (?, ?, ?, ?, ?, ?, ?)', extra_index_rows)
                db.executemany('INSERT INTO series_title_lookup VALUES (?, ?)', [
                    (series_id, m._series_title_key(value))
                    for series_id, titles in ((1, source_korean_titles), (2, related_korean_titles))
                    for value in titles
                ])
                db.executemany('INSERT INTO series_native_title_lookup VALUES (?, ?)', [
                    (series_id, m._title_key(value))
                    for series_id, titles in ((1, source_native_titles), (2, related_native_titles))
                    for value in titles
                ])

            provider = object.__new__(m.RabbitPluginsMetadataProvider)
            provider._series_index = lambda: index
            root = {field: '' for field in m.RELATION_FIELDS.values()}
            root.update({relation_field: '[2]',
                         'source_manga_updates_response_recommendations': '[]'})
            root.update(root_fields or {})
            source_rows = {1: root, **(extra_source_rows or {})}
            provider._source_series_rows = lambda ids: {key: source_rows[key] for key in ids if key in source_rows}
            provider._similar_data = lambda *args, **kwargs: {'items': []}
            provider._random_genre_tag_recommendations = lambda *args, **kwargs: []
            files = related_files or [{
                'id': 10, 'library_id': related_library_id, 'content_kind': related_kind,
                'series_name': title, 'series_alias': None, 'localized_series': localized,
                'cover_image': '', 'cover_updated_at': None, 'volume_index': None,
                'author': '', 'publisher': '', 'books_lv': '', 'genre': '', 'tags': '',
                'file_path': '', 'file_format': '', 'file_mtime': None, 'file_size': 0,
            }]

            class Gateway:
                def fetch_all(self, query, params=()):
                    if query.startswith('PRAGMA table_info('):
                        return [{'name': name} for name in ('localized_series', 'document_volume_index', 'content_kind')]
                    if queries is not None:
                        queries.append((query, params))
                    return files

            target = {'id': 20, 'series_name': target_series_name or title, 'series_alias': None,
                      'localized_series': localized if target_localized is None else target_localized,
                      'library_id': 13, 'content_kind': target_kind}
            with patch('services.book_service.get_cover_image_with_t', side_effect=lambda cover, updated: cover):
                return provider._discovery_data(
                    Gateway(), target, '', [], visible or (lambda _: True), check_rating, 'general', 10)

    def test_earlier_prequel_does_not_become_main_story(self):
        files = []
        for book_id, title, native in ((10, '직전작', '前作'), (11, '더 앞선 전작', '以前'),
                                       (12, '본편', '本編'), (13, '다른 본편', '別本編')):
            files.append({
                'id': book_id, 'library_id': 2, 'content_kind': 'manga',
                'series_name': title, 'series_alias': None, 'localized_series': native,
                'cover_image': '', 'cover_updated_at': None, 'volume_index': None,
                'author': '', 'publisher': '', 'books_lv': '', 'genre': '', 'tags': '',
                'file_path': '', 'file_format': '', 'file_mtime': None, 'file_size': 0,
            })
        for explicit_main in (False, True):
            with self.subTest(explicit_main=explicit_main):
                result = self._relations(
                    '현재 앤솔로지', '現在', '前作', '前作', 'relationships_prequel',
                    target_localized='現在', related_korean_titles=['직전작'], related_files=files,
                    root_fields={'relationships_main_story': '[4]'} if explicit_main else {},
                    extra_index_rows=[
                        (3, 'manga', '以前', m._title_key('以前'), json.dumps(['더 앞선 전작']), '', json.dumps(['以前'])),
                        (4, 'manga', '本編', m._title_key('本編'), json.dumps(['본편']), '', json.dumps(['本編'])),
                        (5, 'manga', '別本編', m._title_key('別本編'), json.dumps(['다른 본편']), '', json.dumps(['別本編'])),
                    ],
                    extra_source_rows={2: {'relationships_v2': json.dumps([
                        {'relation_type': 'prequel', 'to_series_id': 3},
                        {'relation_type': 'prequel', 'to_series_id': 3},
                        {'relation_type': 'prequel', 'to_series_id': 1},
                        {'relation_type': 'parent', 'to_series_id': 5 if explicit_main else 4},
                    ])}})
                self.assertEqual([card['series_name'] for card in result['relations']['main_story']], ['본편'])
                self.assertEqual([card['series_name'] for card in result['relations']['prequel']], ['직전작', '더 앞선 전작'])

    def test_reverse_relations_accept_explicit_native_alias_with_korean_title(self):
        title = '던전에서 만남을 추구하면 안 되는 걸까'
        canonical = 'ダンジョンに出会いを求めるのは間違っているのだろうか'
        alias = 'ダンジョンに出会いを求めるのは間違っているだろうか'
        for field, kind, suffix in (
            ('relationships_prequel', 'prequel', 'Ⅱ'),
            ('relationships_main_story', 'main_story', '공식 앤솔로지'),
        ):
            with self.subTest(relation=kind):
                source_title = title + ' ' + suffix
                source_native = canonical + ' ' + suffix
                result = self._relations(
                    title, source_native, canonical, alias, field,
                    target_series_name=source_title, target_localized=source_native,
                    source_korean_titles=[source_title], related_korean_titles=[title],
                    related_native_titles=[canonical, alias])
                card = result['relations'][kind][0]
                self.assertEqual(card['series_name'], title)
                self.assertEqual(card['localized_series'], alias)

    def test_source_without_korean_title_matches_only_explicit_native_alias(self):
        canonical, alias, related = '原作の作品', '原作作品', '続編の作品'
        result = self._relations(
            '후속작', canonical, related, related, 'relationships_sequel',
            target_series_name='로컬 원작', target_localized=alias,
            source_korean_titles=[], source_native_titles=[canonical, alias],
            related_korean_titles=['후속작'])
        self.assertEqual(result['relations']['sequel'][0]['series_name'], '후속작')

    def test_related_without_korean_title_is_queried_by_explicit_native_alias(self):
        canonical, alias = '本編の外伝', '本編外伝'
        queries = []
        result = self._relations(
            '로컬 외전', '本編', canonical, alias, 'relationships_side_story',
            target_series_name='원작', target_localized='本編',
            source_korean_titles=['원작'], related_korean_titles=[],
            related_native_titles=[canonical, alias], queries=queries)
        self.assertEqual(result['relations']['side_story'][0]['series_name'], '로컬 외전')
        self.assertTrue(queries)
        self.assertTrue(any(alias in params and 'b.localized_series' in query
                            for query, params in queries))

    def test_native_alias_matching_rejects_similar_titles_and_preserves_permissions(self):
        canonical, alias = '本編の外伝', '本編外伝'
        cases = (
            ('unregistered spelling', '本編外傳', None, None),
            ('longer title', alias + ' 別冊', None, None),
            ('partial title', '本編', None, None),
            ('hidden book', alias, lambda _: False, None),
            ('rating denied', alias, None, lambda *_: False),
        )
        for name, localized, visible, check_rating in cases:
            with self.subTest(case=name):
                result = self._relations(
                    '외전', '原作', canonical, localized, 'relationships_side_story',
                    target_series_name='원작', target_localized='原作',
                    source_korean_titles=['원작'], related_korean_titles=['외전'],
                    related_native_titles=[canonical, alias], visible=visible,
                    check_rating=check_rating)
                self.assertEqual(result['relations'], {})

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

    def test_adaptation_uses_lowest_available_volume_cover_not_first_inserted_cover(self):
        title = '가끔씩 툭하고 러시아어로 부끄러워하는 옆자리의 아랴 양'
        native = '時々ボソッとロシア語でデレる隣のアーリャさん'
        related_files = []
        for book_id, volume in ((40, 4), (41, 5), (52, 1), (53, 2)):
            related_files.append({
                'id': book_id, 'library_id': 19, 'content_kind': 'manga',
                'series_name': title, 'series_alias': None, 'localized_series': native,
                'cover_image': f'cover-{volume}.webp', 'cover_updated_at': None,
                'volume_index': None, 'author': '', 'publisher': '', 'books_lv': '',
                'genre': '', 'tags': '', 'file_path': f'{title} {volume:02d}권.cbz',
                'file_format': 'cbz', 'file_mtime': None, 'file_size': 0,
            })

        result = self._relations(
            title, native, native, native, 'relationships_adaptation',
            source_kind='novel', related_kind='manga', target_kind='novel',
            target_series_name=title + ' [SUN SUN SUN]', related_library_id=19,
            related_files=related_files)
        item = result['relations']['adaptation'][0]
        self.assertEqual(item['cover'], 'cover-1.webp')
        self.assertEqual(item['book_id'], 52)
