"""Run with: python -m unittest rabbit_plugins.tests.test_provider_search"""
import unittest
from unittest.mock import patch
from .. import rabbit_plugins as m
from .. import provider_search as api


class ProviderTests(unittest.TestCase):
    def provider(self):
        obj = object.__new__(m.RabbitPluginsMetadataProvider)
        obj.cache_get = lambda key: None
        obj.cache_set = lambda *a, **kw: None
        return obj

    def row(self, source, author='작가'):
        return dict(id=source, title='작품', source=source, author=author,
                    url='https://example.com/' + source, summary='소개', publisher=source)

    def test_type_gates_and_priority(self):
        calls = []
        def fake(source, query, matches):
            calls.append(source)
            return [self.row(source)]
        p = self.provider()
        with patch.object(m, 'search_additional_provider', fake):
            rows = p._search_metadata('작품', {'metadata_sources': 'munpia,kakao_webtoon,kakaopage,novelpia'},
                                      content_kind='novel', manual=True)
        self.assertEqual(calls, ['munpia', 'kakaopage', 'novelpia'])
        merged = p._merge_metadata_candidates(rows, 'novel')
        self.assertEqual(merged['metadata']['publisher'], 'munpia')
        calls.clear()
        with patch.object(m, 'search_additional_provider', fake):
            p._search_metadata('작품', {'metadata_sources': list(api.SOURCE_KINDS)},
                               content_kind='manhwa', manual=True)
        self.assertEqual(calls, ['kakao_webtoon'])

    def test_naver_genre_and_publisher(self):
        page = '<a href="?genreCode=201">로맨스</a><ul class="end_info"><li><span><a href="?genreCode=206">무협</a></span></li><li><span>출판사</span><a>제이플러스</a></li></ul><div class="end_dsc">'
        metadata = m._naver_role_metadata(page)
        self.assertEqual(metadata['genre'], '무협')
        self.assertEqual(metadata['publisher'], '제이플러스')
        metadata['tags'] = m._metadata_remove_terms('NOVEL, 무협, 먼치킨', 'NOVEL, COMIC, WEBTOON')
        self.assertEqual(m._metadata_clean(metadata)['tags'], '먼치킨')

    def test_extended_metadata_fields(self):
        item = m._metadata_clean({'publication_status': 0, 'books_lv': 'everyone',
            'publication_start_date': '2024-01-02T00:00:00', 'publication_end_date': '2024-02-30',
            'isbn': '9781234567897'})
        self.assertEqual(item['publication_status'], '0')
        self.assertEqual(item['publication_start_date'], '2024-01-02')
        self.assertNotIn('publication_end_date', item)
        self.assertIn('summary', m._metadata_field_selection({'metadata_fields': 'summary'}, automatic=True))
        self.assertNotIn('summary', m._metadata_field_selection({'metadata_fields': 'author'}, automatic=True))

    def test_series_dates_gateway_wrapper(self):
        class Gateway:
            def get_setting(self, *args):
                return {'value': '{"start":"2021-07-21","end":"2026-04-17"}'}
        self.assertEqual(m._read_series_dates(Gateway(), 'key'), {'start':'2021-07-21', 'end':'2026-04-17'})
        self.assertNotEqual(m._series_dates_key(1, '작품'), m._series_dates_key(2, '작품'))

    def test_novelpia_genres(self):
        from email.message import Message
        from types import SimpleNamespace
        import json
        response = SimpleNamespace(headers=Message())
        payload = {'status':200, 'list':[{'novel_no':1, 'novel_name':'작품', 'novel_genre_arr':['공포','현대','오컬트'], 'is_complete':1, 'novel_age':0, 'start_date':'2021-07-21', 'complete_date':'2026-04-17'}]}
        with patch.object(api.SearchAdapter, '_request', return_value=(json.dumps(payload).encode(),response)):
            row=api.search('novelpia','작품',lambda q,t:q==t)[0]
        self.assertEqual(row['genre'], '공포, 현대')
        self.assertEqual(row['tags'], '오컬트')
        self.assertEqual(row['publication_status'], '2')
        self.assertEqual(row['books_lv'], 'everyone')

    def test_episode_range_coverage(self):
        def file(name):
            return {'file_path':'/books/'+name+'.txt','file_format':'txt'}
        coverage=m._chapter_file_coverage([file('예수천국 불신지옥 0-365')],444)
        self.assertEqual(coverage, {'total':444,'present':365,'missing':79,'known':True})
        self.assertEqual(m._chapter_file_coverage([file('작품 0-365'),file('작품 300-444')],444)['missing'],0)
        self.assertEqual(m._chapter_file_coverage([file('작품 0-100'),file('작품 102-444')],444)['missing'],1)
        self.assertFalse(m._chapter_file_coverage([file('작품 0-365'),file('외전')],444)['known'])
        self.assertFalse(m._chapter_file_coverage([file('작품 444-365')],444)['known'])

    def test_auto_merge_preserves_genre_source(self):
        row = dict(self.row('novelpia'), genre='무협', tags='하렘')
        merged = self.provider()._merge_metadata_candidates([row], 'novel')
        self.assertEqual(merged['source'], 'merged')
        self.assertEqual(merged['genre_source'], 'novelpia')

    def test_manual_chapter_count_save_and_clear(self):
        from flask import Flask, session
        import json
        p = self.provider()
        class Gateway:
            data = {}
            def fetch_all(self, *args):
                return [{'id':1, 'library_id':17, 'file_path':'/book.txt'}]
            def get_setting(self, key, default=None):
                return {'value':self.data.get(key, '{}')}
            def set_setting(self, key, value):
                self.data[key] = value
        gateway = Gateway()
        p.get_db_gateway = lambda *args: gateway
        app = Flask('count-test'); app.secret_key = 'test-only'
        with app.test_request_context('/'), patch.object(m, '_optional_column_sql', return_value='NULL'), patch.object(m, '_comicinfo_metadata', return_value={'artist':'', 'count':0, 'volume':0}):
            session['role'] = 'admin'
            context = {'series_name':'작품', 'manual_chapter_count':'444'}
            self.assertTrue(p.run_context_menu_action('general','save_detail_metadata',context)['success'])
            key = m._series_dates_key(17,'작품')
            self.assertEqual(json.loads(gateway.data[key])['manual_chapter_count'],444)
            context['manual_chapter_count'] = ''
            self.assertTrue(p.run_context_menu_action('general','save_detail_metadata',context)['success'])
            self.assertNotIn('manual_chapter_count',json.loads(gateway.data[key]))
            context['manual_chapter_count'] = '1.5'
            self.assertFalse(p.run_context_menu_action('general','save_detail_metadata',context)['success'])

    def test_novelpia_discontinued(self):
        for value in [2,4,'2','4']:
            result=api.SearchAdapter._novelpia_status({'is_complete':0,'novel_live':value})
            self.assertEqual(result, {'publication_status':'1','publication_status_label':'연재중단'})
            self.assertEqual(m._metadata_clean(result)['publication_status_label'], '연재중단')
        self.assertEqual(api.SearchAdapter._novelpia_status({'is_complete':1,'novel_live':2}), {'publication_status':'2'})
        self.assertEqual(api.SearchAdapter._novelpia_status({'is_complete':0,'novel_live':0}), {'publication_status':'0'})

    def test_empty_selection(self):
        with patch.object(m, 'search_additional_provider') as search, patch.object(m, '_remote_search') as old:
            self.assertEqual(self.provider()._search_metadata('작품', {'metadata_sources': []}, manual=True), [])
            search.assert_not_called()
            old.assert_not_called()

    def test_author_and_partial(self):
        def fake(source, query, matches):
            self.assertEqual(query, '작품')
            return [self.row(source, '다른 사람'), self.row(source, '작가')]
        with patch.object(m, 'search_additional_provider', fake):
            rows = self.provider()._search_metadata('작품 [작가]', {'metadata_sources': 'munpia'},
                                                    content_kind='novel', manual=True)
        self.assertEqual([r['author'] for r in rows], ['작가'])
        self.assertTrue(m._metadata_title_matches('작품', '작품의 다음 이야기', True))
        self.assertFalse(m._metadata_title_matches('작품', '작품의 다음 이야기'))

    def test_failure_isolated(self):
        def fake(source, query, matches):
            if source == 'novelpia':
                raise TimeoutError('fixture timeout')
            return [self.row(source)]
        with patch.object(m, 'search_additional_provider', fake), patch.object(m, 'search_novelpia_author', return_value=([], [])):
            rows = self.provider()._search_metadata('작품', {'metadata_sources': 'novelpia,munpia'},
                                                    content_kind='novel', manual=True)
        self.assertEqual([r['source'] for r in rows], ['munpia'])

    def test_webtoon_duplicate_credits(self):
        adapter = api.SearchAdapter(lambda q,t: True)
        self.assertEqual(adapter._kakao_author_text([
            {'name':'윤태호','type':'WRITER'}, {'name':'윤태호','type':'ILLUSTRATOR'},
            {'name':'출판사','type':'PUBLISHER'}]), '윤태호')

    def test_novelpia_filter_before_limit_and_thumbnail(self):
        import json
        from email.message import Message
        from types import SimpleNamespace
        data = {'status': 200, 'list': [
            {'novel_no': 1, 'novel_name': '무관한 책'},
            {'novel_no': 2, 'novel_name': '작품', 'writer_nick': '작가',
             'cover_url': '//images.example.com/cover.jpg'}]}
        response = SimpleNamespace(headers=Message())
        with patch.object(api.SearchAdapter, '_request', return_value=(json.dumps(data).encode(), response)):
            rows = api.search('novelpia', '작품', lambda q,t:q == t, limit=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['author'], '작가')
        self.assertEqual(rows[0]['cover'], 'https://images.example.com/cover.jpg')

    def test_auto_pipeline_author(self):
        p = self.provider()
        class Gateway:
            def fetch_all(self, *args):
                return [dict(id=1, series_name='작품 [작가]', title='작품 1권', library_id=1, file_path='/작품 [작가]/1.txt')]
            def fetch_one(self, *args):
                return {'content_kind': 'novel'}
        p.get_db_gateway = lambda *a: Gateway()
        p.get_plugin_config = lambda *a: {'metadata_sources': 'munpia'}
        applied = []
        p._apply_metadata = lambda gateway, book, item, config, **kw: (applied.append(item) is None, 'fixture')
        with patch.object(m, '_optional_column_sql', return_value='NULL'), patch.object(
            m, 'search_additional_provider', return_value=[self.row('munpia', '다른 사람'), dict(self.row('munpia'), total_chapters=444, publication_status='2')]):
            stats = p._auto_collect('general', {'library_id': 1})
        self.assertEqual(stats['matched'], 1)
        self.assertEqual(applied[0]['metadata']['author'], '작가')
        self.assertEqual(applied[0]['metadata']['total_chapters'], 444)
        self.assertEqual(applied[0]['metadata']['publication_status'], '2')


if __name__ == '__main__':
    unittest.main()
