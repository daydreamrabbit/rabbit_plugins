import tempfile
import json
import unittest
import zipfile
from pathlib import Path
from .. import rabbit_plugins as m


class PreferenceGateway:
    def __init__(self, config=None, cover='internal.webp'):
        self.settings = {}
        self.config = config or {}
        self.cover = cover
        self.updates = []

    def get_setting(self, key, default=None):
        value = self.settings.get(key)
        return {'value': value} if value is not None else default

    def set_setting(self, key, value):
        self.settings[key] = value

    def get_plugin_config(self, _plugin_id, default=None):
        return self.config

    def fetch_all(self, query, params=()):
        if 'FROM books b LEFT JOIN libraries' in query:
            return [{'id': 7, 'cover_image': self.cover, 'library_id': 2,
                     'content_kind': 'manga'}]
        return []

    def execute(self, query, params=()):
        self.updates.append((query, params))
        self.cover = params[0]
        return 1


class PreferenceProvider:
    def __init__(self, gateway):
        self.gateway = gateway

    def get_db_gateway(self, _db_type):
        return self.gateway

    def get_plugin_config(self, _db_type, default=None):
        return self.gateway.config

class CoverPolicyTests(unittest.TestCase):
    def test_internal_images_never_replaced_even_with_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            for ext in ['epub', 'cbz', 'zip']:
                path=Path(folder)/('book.'+ext)
                with zipfile.ZipFile(path,'w') as z:
                    z.writestr('images/001.jpg',b'image')
                row={'file_path':str(path)}
                self.assertFalse(m._metadata_cover_eligible(row,False,False))
                self.assertFalse(m._metadata_cover_eligible(row,False,True))
                self.assertTrue(m._metadata_cover_eligible(row,True,False))

    def test_no_internal_image_can_use_external(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'book.epub'
            with zipfile.ZipFile(path,'w') as z:
                z.writestr('chapter.xhtml','<p>Text</p>')
            self.assertTrue(m._metadata_cover_eligible({'file_path':str(path)},False,False))
        self.assertTrue(m._metadata_cover_eligible({'file_path':'book.txt'},False,False))
        self.assertFalse(m._metadata_cover_eligible({'file_path':'unavailable.cbz'},False,True))

    def test_pdf_without_generated_cover_can_use_external(self):
        row = {'file_path': '/books/서버 관리자.pdf', 'cover_image': None}
        self.assertTrue(m._metadata_cover_eligible(row, False, False))
        row['cover_image'] = 'internal.webp'
        self.assertFalse(m._metadata_cover_eligible(row, False, False))
        self.assertFalse(m._metadata_cover_eligible(row, False, True))

    def test_webtoon_includes_existing_covers_but_respects_locks(self):
        row={'file_path':'book.cbz','cover_image':'old.webp'}
        self.assertTrue(m._metadata_cover_eligible(row,True,False))
        row['metadata_locked']=1
        self.assertFalse(m._metadata_cover_eligible(row,True,False))
        self.assertTrue(m._metadata_cover_eligible(row,True,True))

    def test_ridi_volume_cover_can_fill_remote_archive_without_opening_it(self):
        row = {'file_path': '/remote/book 01.cbz', 'cover_image': ''}
        self.assertFalse(m._metadata_cover_eligible(row, False, False))
        self.assertTrue(m._metadata_cover_eligible(
            row, False, False, source='ridi', per_volume=True))
        row['cover_image'] = '1/book_existing.webp'
        self.assertFalse(m._metadata_cover_eligible(
            row, False, False, source='ridi', per_volume=True))
        self.assertTrue(m._metadata_cover_eligible(
            row, False, True, source='ridi', per_volume=True))

    def test_ridi_series_page_extracts_each_volume_cover(self):
        source = '''
        <li class="js_series_book_list detail_scalable_thumbnail" data-id="a" data-volume="1">
          <img data-original-cover="https://img.ridicdn.net/cover/a/xxlarge#1" />
          <div class="js_book_title">테스트 1권</div>
          <ul><li>nested</li></ul>
        </li>
        <li data-volume="2" class="hidden js_series_book_list">
          <img data-src="https://img.ridicdn.net/cover/b/xxlarge?dpi=xxhdpi" />
          <div class="info js_book_title">테스트 2권</div>
        </li>
        '''
        by_volume, by_title = m._ridi_volume_cover_maps(source)
        self.assertEqual(by_volume, {
            '1': 'https://img.ridicdn.net/cover/a/xxlarge#1',
            '2': 'https://img.ridicdn.net/cover/b/xxlarge?dpi=xxhdpi',
        })
        self.assertEqual(by_title[m._title_key('테스트 2권')], by_volume['2'])

    def test_ridi_next_data_extracts_distinct_volume_covers(self):
        books = [
            {'title': f'오늘은 누구랑?! {number}권',
             'cover': {'xxlarge': f'https://img.ridicdn.net/cover/{number}/xxlarge#1'}}
            for number in (1, 2)
        ]
        cells = [{'cell__BookDetailHomeEpisodeBookList': {'books': books}}]
        page = {'props': {'pageProps': {'sectionProps': {
            'gridQuery': {'riGrid': {'grid': {'cells': cells}}}}}}}
        source = '<script id="__NEXT_DATA__">' + json.dumps(page) + '</script>'
        by_volume, _ = m._ridi_volume_cover_maps(source)
        self.assertEqual(by_volume, {
            '1': 'https://img.ridicdn.net/cover/1/xxlarge#1',
            '2': 'https://img.ridicdn.net/cover/2/xxlarge#1',
        })

    def test_series_title_cover_is_not_a_volume_map(self):
        rows = [{'title': '오늘은 누구랑?! 01권'}, {'title': '오늘은 누구랑?! 02권'}]
        self.assertFalse(m._use_ridi_volume_covers(
            'ridi', rows, {}, {m._title_key('오늘은 누구랑?!'): 'https://img/series'}))

    def test_genre_conversion_normalizes_saved_terms_during_merge(self):
        rules = m._metadata_value_map('19+ => 성인')
        self.assertEqual(m._merge_converted_genres(
            '만화 e북, 성인', '만화 e북, 19+, 해외 순정', rules),
            '만화 e북, 성인, 해외 순정')

    def test_volume_key_falls_back_to_filename(self):
        row = {
            'volume_index': None,
            'title': '테스트',
            'file_path': '/remote/테스트 12권 [1500x].cbz',
        }
        self.assertEqual(m._row_volume_key(row), '12')

    def test_episode_range_without_volume_word_is_not_a_volume(self):
        row = {'title': '환생자의 재벌테크 1-470 (완결)',
               'file_path': '/books/환생자의 재벌테크 1-470 (완결).txt'}
        self.assertEqual(m._row_explicit_volume_key(row), '')
        self.assertEqual(m._row_mapped_cover(
            row, {'470': 'https://wrong/episode-cover'}, {}), '')

    def test_unmatched_ridi_volume_has_no_series_cover_fallback(self):
        row = {'title': '테스트 3권', 'file_path': '/remote/테스트 3권.cbz'}
        self.assertEqual(m._row_mapped_cover(
            row,
            {'1': 'https://img/one', '2': 'https://img/two'},
            {},
        ), '')

    def test_single_txt_range_uses_representative_ridi_cover(self):
        rows = [{'file_path': '/books/작품 1-470 (완결).txt'}]
        self.assertFalse(m._use_ridi_volume_covers(
            'ridi', rows, {'1': 'https://img/one'}, {}))

    def test_single_explicit_volume_can_use_selected_ridi_cover(self):
        rows = [{'title': '작품 01권', 'file_path': '/remote/작품 01권.cbz'}]
        self.assertTrue(m._use_ridi_exact_single_cover(
            'ridi', rows, 'https://img/one'))
        self.assertFalse(m._use_ridi_exact_single_cover(
            'ridi', [{'title': '작품 1-470'}], 'https://img/one'))

    def test_multiple_volumes_keep_strict_ridi_cover_matching(self):
        rows = [
            {'file_path': '/books/작품 01권.epub'},
            {'file_path': '/books/작품 02권.epub'},
        ]
        self.assertTrue(m._use_ridi_volume_covers(
            'ridi', rows, {'1': 'https://img/one', '2': 'https://img/two'}, {}))

    def test_series_db_priority_changes_actual_url_not_just_source(self):
        p=object.__new__(m.RabbitPluginsMetadataProvider)
        rows=[{'source':'kakao_webtoon','title':'작품','cover':'https://external/cover'},
              {'source':'series_db','title':'작품','cover':'https://series/cover'}]
        result=p._merge_metadata_candidates(rows,'manhwa')
        self.assertEqual(result['cover_source'],'series_db')
        self.assertEqual(result['metadata']['cover'],'https://series/cover')
        rows[1]['cover']=''
        result=p._merge_metadata_candidates(rows,'manhwa')
        self.assertEqual(result['metadata']['cover'],'https://external/cover')

    def test_merged_ridi_candidate_keeps_volume_cover_map(self):
        provider = object.__new__(m.RabbitPluginsMetadataProvider)
        rows = [{
            'source': 'ridi',
            'title': '테스트',
            'cover': 'https://img/one',
            'cover_by_volume': {'1': 'https://img/one', '2': 'https://img/two'},
        }]
        result = provider._merge_metadata_candidates(rows, 'manga')
        self.assertEqual(result['cover_source'], 'ridi')
        self.assertEqual(result['cover_by_volume']['2'], 'https://img/two')
        self.assertEqual(
            result['cover_maps_by_source']['ridi']['cover_by_volume']['2'],
            'https://img/two')

    def test_naver_episode_count_does_not_pollute_ridi_volume_map(self):
        provider = object.__new__(m.RabbitPluginsMetadataProvider)
        result = provider._merge_metadata_candidates([
            {'source': 'ridi', 'title': '작품', 'cover': 'https://ridi/main',
             'cover_by_volume': {'1': 'https://ridi/one'}},
            {'source': 'naver', 'title': '작품 (총 470화/완결)',
             'cover': 'https://naver/main',
             'cover_by_volume': {'470': 'https://naver/episode'}},
        ], 'novel')
        self.assertEqual(
            result['cover_maps_by_source']['ridi']['cover_by_volume'],
            {'1': 'https://ridi/one'})

    def test_delayed_scanner_cover_is_restored_when_overwrite_is_enabled(self):
        gateway = PreferenceGateway(config={
            'metadata_collect_cover': True,
            'metadata_cover_kinds': 'manga',
            'metadata_cover_overwrite': True,
            'metadata_cover_overwrite_kinds': 'manga',
        })
        m._remember_external_cover(gateway, 7, '2/external.webp', 2, 'manga')
        changed = m._reconcile_external_covers(PreferenceProvider(gateway), 'general')
        self.assertEqual(changed, 1)
        self.assertEqual(gateway.cover, '2/external.webp')
        self.assertEqual(gateway.updates[-1][1], ('2/external.webp', 7))

    def test_delayed_scanner_cover_is_left_when_overwrite_is_disabled(self):
        gateway = PreferenceGateway(config={
            'metadata_collect_cover': True,
            'metadata_cover_kinds': 'manga',
            'metadata_cover_overwrite': False,
        })
        m._remember_external_cover(gateway, 7, '2/external.webp', 2, 'manga')
        changed = m._reconcile_external_covers(PreferenceProvider(gateway), 'general')
        self.assertEqual(changed, 0)
        self.assertEqual(gateway.cover, 'internal.webp')
        self.assertEqual(gateway.updates, [])
