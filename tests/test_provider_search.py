"""Run with: python -m unittest rabbit_plugins.tests.test_provider_search"""
import unittest
import json
from types import SimpleNamespace
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

    def test_search_candidates_keep_their_own_covers(self):
        ridi_payload = {'props': {'books': [
            {'id': '896000382', 'book': {'title': {'main': '전설의 기사'},
                                      'categories': [{'name': '판타지 e북'}]}},
            {'id': '123000264', 'book': {'title': {'main': '전설의 기사 아크리안'},
                'series': {'thumbnail': {'large': '//img.ridicdn.net/cover/123000264/large'}},
                'categories': [{'name': '판타지 e북'}]}},
        ]}}
        ridi_html = '<script id="__NEXT_DATA__">' + json.dumps(ridi_payload, ensure_ascii=False) + '</script>'
        kyobo_html = '''<meta property="og:image" content="https://contents.kyobobook.co.kr/resources/fo/images/common/logo.png">
            <a href="https://product.kyobobook.co.kr/detail/S000001" class="prod_link">
              <img data-kbbfn-bid="9791199489561"></a>
            <a href="https://product.kyobobook.co.kr/detail/S000001">공작 하나</a>
            <a href="https://product.kyobobook.co.kr/detail/S000002" class="prod_link">
              <img data-src="https://example.com/two.jpg"></a>
            <a href="https://product.kyobobook.co.kr/detail/S000002">공작 둘</a>'''
        def fake_get(url, **kwargs):
            return SimpleNamespace(text=ridi_html if 'ridibooks.com' in url else kyobo_html,
                                   raise_for_status=lambda: None)
        with patch('requests.get', side_effect=fake_get):
            ridi = m._remote_search_page('ridi', '전설의 기사', 'novel', 'novel', allow_partial=True)
            kyobo = m._remote_search_page('kyobo', '공작', 'book', 'book', allow_partial=True)
        self.assertEqual(len(ridi), 2)
        self.assertEqual(ridi[0]['cover'], 'https://img.ridicdn.net/cover/896000382/xxlarge#1')
        self.assertEqual(ridi[1]['cover'], 'https://img.ridicdn.net/cover/123000264/large')
        self.assertEqual([row['cover'] for row in kyobo], [
            'https://contents.kyobobook.co.kr/sih/fit-in/300x0/pdt/9791199489561.jpg',
            'https://example.com/two.jpg',
        ])

    def test_yes24_official_search_and_metadata_fields(self):
        item = {
            'itemId': 12345678, 'title': '클린 코드', 'author': '로버트 C. 마틴 저',
            'publisher': '인사이트', 'goodsType': '국내도서',
            'goodsSortNm': '국내도서-IT 모바일',
            'isbn13': '9788966260959', 'publishDate': '20131224',
            'cover': 'https://image.yes24.com/goods/12345678/L',
            'link': 'https://www.yes24.com/product/goods/12345678',
            'adultYn': 'N', 'originalTitle': 'Clean Code',
            'contentDetail': {'bookIntroduction': '개발자를 위한 책',
                              'tableOfContents': '1장 목차'},
        }
        response = SimpleNamespace(
            status_code=200, json=lambda: {'success': True, 'data': {'items': [
                item, {**item, 'itemId': 99, 'title': '클린 코드와 다른 책'},
            ]}}, raise_for_status=lambda: None)
        with patch('requests.get', return_value=response) as get:
            candidates = self.provider()._search_metadata(
                '클린 코드', {'metadata_sources': 'yes24', 'yes24_api_key': 'private-key'},
                content_kind='book', manual=False)
        self.assertEqual(len(candidates), 1)
        row = candidates[0]
        self.assertEqual(row['source'], 'yes24')
        self.assertEqual(row['cover'], item['cover'])
        self.assertEqual(row['metadata']['localized_series'], 'Clean Code')
        self.assertEqual(row['metadata']['summary'], '개발자를 위한 책')
        self.assertNotIn('목차', row['metadata']['summary'])
        self.assertEqual(row['metadata']['isbn'], '9788966260959')
        self.assertEqual(row['metadata']['release_date'], '2013-12-24')
        self.assertEqual(row['metadata']['author'], '로버트 C. 마틴')
        self.assertEqual(row['metadata']['genre'], 'IT 모바일')
        self.assertNotIn('books_lv', row['metadata'])
        self.assertEqual(get.call_args.args[0], 'https://apis.yes24.com/v1/goods/itemList')
        self.assertEqual(get.call_args.kwargs['headers']['X-Api-Key'], 'private-key')
        self.assertEqual(get.call_args.kwargs['params']['category'], 'ALL')
        self.assertEqual(get.call_args.kwargs['params']['detail'], 'Y')
        with patch('requests.get', return_value=response):
            self.assertEqual(m._yes24_search('클린', 'novel', 'private-key', True), [])
        with patch('requests.get') as get:
            self.assertEqual(m._yes24_search('클린 코드', 'book', ''), [])
            get.assert_not_called()

    def test_yes24_filters_candidates_by_catalogue_kind(self):
        sections = [
            ('eBook-만화-판타지/SF', 'manga'),
            ('eBook-만화-웹툰', 'manhwa'),
            ('ebook-라이트노벨', 'novel'),
            ('eBook-BL-소설', 'novel'),
            ('eBook-BL만화-국내', 'manga'),
            ('eBook-컴퓨터와인터넷-IT 전문서', 'book'),
            ('eBook-잡지-성인', 'magazine'),
        ]
        items = [dict(itemId=index, title='같은 제목', author='작가',
                      goodsType='eBook', goodsSortNm=category)
                 for index, (category, _) in enumerate(sections, 1)]
        items.extend([
            dict(itemId=8, title='같은 제목', goodsType='도서',
                 goodsSortNm='국내도서-소설/시/희곡'),
            dict(itemId=9, title='같은 제목', goodsType='Blu-ray',
                 goodsSortNm='DVD/Blu-ray-애니메이션'),
        ])
        response = SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                                   json=lambda: {'success': True, 'data': {'items': items}})
        expected = {
            'manga': ['yes24:1', 'yes24:5'],
            'manhwa': ['yes24:2'],
            'novel': ['yes24:3', 'yes24:4', 'yes24:8'],
            'book': ['yes24:6'],
            '잡지': ['yes24:7'],
        }
        with patch('requests.get', return_value=response):
            for kind, ids in expected.items():
                found = m._yes24_search('같은 제목', kind, 'private-key')
                self.assertEqual([row['id'] for row in found], ids, kind)
        self.assertEqual(m._yes24_item_kind({'goodsType': 'eBook'}), '')

    def test_yes24_fantasy_and_martial_arts_are_novels(self):
        items = [
            {'itemId': 99121472, 'title': '환생자의 재벌테크',
             'author': '필로스 저', 'goodsType': 'eBook',
             'goodsSortNm': 'ebook-판타지'},
            {'itemId': 12345, 'title': '환생자의 재벌테크',
             'author': '필로스 저', 'goodsType': 'eBook',
             'goodsSortNm': 'ebook-무협'},
        ]
        response = SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                                   json=lambda: {'success': True, 'data': {'items': items}})
        with patch('requests.get', return_value=response):
            novels = m._yes24_search('환생자의 재벌테크', 'novel', 'private-key')
            books = m._yes24_search('환생자의 재벌테크', 'book', 'private-key')
        self.assertEqual([row['id'] for row in novels], ['yes24:99121472', 'yes24:12345'])
        self.assertEqual(novels[0]['author'], '필로스')
        self.assertEqual(books, [])

    def test_yes24_three_novel_category_families(self):
        for category in (
            'ebook-판타지/무협', 'ebook-퓨전', 'ebook-현대', 'ebook-게임',
            'ebook-스포츠', 'ebook-대체역사',
            'ebook-로맨스', 'ebook-현대물', 'ebook-역사/시대물',
            'ebook-TL/삽화소설', 'ebook-로맨틱판타지', 'ebook-로맨스-GL',
            'ebook-라이트노벨', 'ebook-시프트노벨', 'ebook-NT 노벨',
            'ebook-노블엔진', 'ebook-길찾기', 'ebook-제이노블',
            'ebook-라이트노벨-대여',
        ):
            with self.subTest(category=category):
                self.assertEqual(m._yes24_item_kind(
                    {'goodsType': 'eBook', 'goodsSortNm': category}), 'novel')
        for category in ('ebook-대여', 'ebook-GL'):
            self.assertEqual(m._yes24_item_kind(
                {'goodsType': 'eBook', 'goodsSortNm': category}), '')
        self.assertEqual(m._yes24_item_kind(
            {'goodsType': 'eBook', 'goodsSortNm': 'ebook-만화-스포츠'}), 'manga')
        for category, expected in (
            ('ebook-판타지', '웹소설, 판타지'),
            ('ebook-로맨스-현대물', '웹소설, 로맨스, 현대물'),
            ('ebook-BL-소설', '웹소설, BL, 소설'),
            ('ebook-웹소설', '웹소설'),
            ('ebook-라이트노벨', '라이트노벨'),
            ('eBook-컴퓨터와인터넷-IT 전문서', '컴퓨터와인터넷, IT 전문서'),
            ('ebook-만화-스포츠', '만화, 스포츠'),
        ):
            with self.subTest(genre_category=category):
                item = {'goodsType': 'eBook', 'goodsSortNm': category}
                self.assertEqual(m._yes24_genres(
                    item, m._yes24_item_kind(item), 'single'), expected)

    def test_yes24_episode_and_volume_editions(self):
        items = [
            {'itemId': 107847473, 'title': '하멜른의 영주', 'author': '재유 저',
             'goodsType': 'eBook', 'goodsSortNm': 'ebook-판타지'},
            {'itemId': 35838841, 'title': '[연재] 하멜른의 영주 142화 (완결)',
             'author': '재유 저', 'goodsType': 'eBook', 'goodsSortNm': 'ebook-판타지'},
            {'itemId': 111680764, 'title': '[대여] 하멜른의 영주 1권',
             'author': '재유 저', 'goodsType': 'eBook', 'goodsSortNm': 'ebook-판타지'},
        ]
        response = SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                                   json=lambda: {'success': True, 'data': {'items': items}})
        with patch('requests.get', return_value=response):
            both = m._yes24_search('하멜른의 영주', 'novel', 'key')
            serial = m._yes24_search('하멜른의 영주', 'novel', 'key', edition='series')
            volume = m._yes24_search('하멜른의 영주', 'novel', 'key', edition='single')
        self.assertEqual([row['variant_label'] for row in both],
                         ['소설 e북', '웹소설', '소설 e북'])
        self.assertEqual([row['metadata']['genre'] for row in both],
                         ['웹소설, 판타지', '웹소설, 판타지', '웹소설, 판타지'])
        self.assertEqual([row['id'] for row in serial], ['yes24:35838841'])
        self.assertEqual([row['id'] for row in volume],
                         ['yes24:107847473', 'yes24:111680764'])
        self.assertEqual(m._yes24_local_edition(['하멜른의 영주 01화.txt']), 'series')
        self.assertEqual(m._yes24_local_edition(['하멜른의 영주 01권.epub']), 'single')
        self.assertEqual(m._yes24_local_edition(['환생자의 재벌테크.txt']), 'single')
        self.assertEqual(m._yes24_local_edition(['01화.txt', '01권.epub']), '')
        with patch.object(m, '_yes24_search', return_value=both):
            found = self.provider()._search_metadata(
                '하멜른의 영주', {'metadata_sources': 'yes24', 'yes24_api_key': 'key'},
                content_kind='novel', book_type='novel', manual=True)
        self.assertEqual([row['id'] for row in found],
                         ['yes24:107847473', 'yes24:35838841'])
        self.assertEqual(found[1]['variant_label'], '웹소설')
        config = {'metadata_sources': 'yes24', 'yes24_api_key': 'key'}
        with patch('requests.get', return_value=response):
            auto_serial = self.provider()._search_metadata(
                '하멜른의 영주', config, content_kind='novel', book_type='novel',
                yes24_edition=m._yes24_local_edition(['하멜른의 영주 01화.txt']))
            auto_volume = self.provider()._search_metadata(
                '하멜른의 영주', config, content_kind='novel', book_type='novel',
                yes24_edition=m._yes24_local_edition(['하멜른의 영주 01권.epub']))
        self.assertEqual([row['id'] for row in auto_serial], ['yes24:35838841'])
        self.assertEqual([row['id'] for row in auto_volume], ['yes24:107847473'])

    def test_yes24_errors_do_not_expose_api_key(self):
        response = SimpleNamespace(status_code=401, raise_for_status=lambda: (_ for _ in ()).throw(
            RuntimeError('authorization failed')))
        with patch('requests.get', return_value=response), patch('builtins.print') as output:
            self.assertEqual(m._yes24_search('클린 코드', 'book', 'private-key'), [])
        self.assertNotIn('private-key', str(output.call_args_list))

    def test_yes24_uses_general_key_for_adult_search(self):
        provider = self.provider()
        provider.get_plugin_config = lambda db_type, default=None: (
            {'yes24_api_key': 'shared-key'} if db_type == 'general' else {})
        with patch.object(m, '_yes24_search', return_value=[]) as search:
            provider._search_metadata('작품', {'metadata_sources': 'yes24'},
                                      db_type='adult', content_kind='novel', manual=True)
        self.assertEqual(search.call_args.args[:3], ('작품', 'novel', 'shared-key'))

    def test_yes24_public_introduction_fills_missing_api_text(self):
        page = '''<div id="infoset_introduce" class="gd_infoSet">
          <h4>소개</h4><textarea class="txtContentText">
          첫 문단&lt;br/&gt;둘째 문단<br/>마지막 문단</textarea></div>
          <div id="infoset_tagList"><div id="tagArea" class="gd_tagArea">
            <span data-hashTagNm="통쾌함"><a>#통쾌함</a></span>
            <span data-hashTagNm="성장물"><a>#성장물</a></span>
          </div></div><div id="infoset_author">다른 설명</div>'''
        response = SimpleNamespace(text=page, url='https://www.yes24.com/product/goods/139753162',
                                   raise_for_status=lambda: None)
        with patch('requests.get', return_value=response) as get:
            details = m._yes24_public_metadata('139753162')
        summary = details['summary']
        self.assertEqual(summary, '첫 문단\n둘째 문단\n마지막 문단')
        self.assertEqual(details['tags'], '통쾌함, 성장물')
        self.assertEqual(get.call_args.args[0], 'https://www.yes24.com/product/goods/139753162')
        row = {'id': 'yes24:139753162', 'source': 'yes24', 'title': '1시간만에 서버 관리자 되기',
               'metadata': {'title': '1시간만에 서버 관리자 되기', 'author': '정복문'}}
        with patch.object(m, '_yes24_public_metadata', return_value=details) as fetch:
            merged = self.provider()._merge_metadata_candidates([row], 'book')
        self.assertEqual(merged['metadata']['summary'], summary)
        self.assertEqual(merged['metadata']['tags'], '통쾌함, 성장물')
        fetch.assert_called_once_with('139753162')
        self.assertEqual(m._yes24_credits('정복문 저')[0], '정복문')

    def test_type_gates_and_priority(self):
        calls = []
        def fake(source, query, matches, **kwargs):
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
        self.assertEqual(calls, ['naver_webtoon', 'kakaopage', 'kakao_webtoon'])

    def test_naver_sources_are_grouped_with_independent_activation(self):
        self.assertEqual(
            m._metadata_source_order({'metadata_sources': ['naver_webtoon', 'ridi', 'naver']}),
            ['naver', 'naver_webtoon', 'ridi'],
        )
        self.assertEqual(
            m._metadata_source_order({'metadata_sources': ['ridi', 'naver_webtoon']}),
            ['ridi', 'naver_webtoon'],
        )
        self.assertEqual(
            m._metadata_source_order({'metadata_sources': ['naver']}), ['naver'])
        self.assertEqual(
            m._metadata_source_order({'metadata_sources': ['kakao_webtoon', 'ridi', 'kakaopage']}),
            ['kakaopage', 'kakao_webtoon', 'ridi'],
        )
        self.assertEqual(
            m._metadata_source_order({'metadata_sources': ['ridi', 'kakao_webtoon']}),
            ['ridi', 'kakao_webtoon'],
        )

    def test_kakao_page_primary_is_enriched_by_matching_webtoon(self):
        page = {
            'id': 'kakaopage:1', 'source': 'kakaopage', 'source_label': '카카오페이지',
            'title': '같은 작품', 'author': '같은 작가', 'publisher': '출판사',
            'url': 'https://page.kakao.com/content/1', 'cover': 'page-cover',
            'publication_status': '0', 'genre': '판타지',
        }
        webtoon = {
            'id': 'kakao_webtoon:2', 'source': 'kakao_webtoon', 'source_label': '카카오웹툰',
            'title': '같은 작품', 'author': '같은 작가',
            'url': 'https://webtoon.kakao.com/content/work/2',
            'summary': '웹툰 소개', 'tags': '성장물', 'cover': 'webtoon-cover',
        }
        combined = self.provider()._combine_kakao_candidates([page, webtoon])
        self.assertEqual(len(combined), 1)
        row = combined[0]
        self.assertEqual(row['source_label'], '카카오페이지 + 카카오웹툰')
        self.assertEqual(row['metadata']['publisher'], '출판사')
        self.assertEqual(row['metadata']['cover'], 'page-cover')
        self.assertEqual(row['metadata']['summary'], '웹툰 소개')
        self.assertIn('page.kakao.com', row['metadata']['link'])
        self.assertIn('webtoon.kakao.com', row['metadata']['link'])

        mismatch = self.provider()._combine_kakao_candidates([
            page, {**webtoon, 'author': '다른 작가'}])
        self.assertEqual(len(mismatch), 2)

    def test_naver_series_variant_follows_filename_unit(self):
        self.assertEqual(
            m._metadata_book_type('manhwa', file_path='/books/잔불의 기사 01화.cbz'), 'series')
        self.assertEqual(
            m._metadata_book_type('manhwa', file_path='/books/잔불의 기사 01권.cbz'), 'single')
        self.assertIn(
            't=comic', m._remote_source_url('naver', '잔불의 기사', 'manhwa', 'series'))
        serialized = {'source': 'naver', 'title': '잔불의 기사 (총 246화/미완결)'}
        volume = {'source': 'naver', 'title': '잔불의 기사 [단행본] (총 3권/미완결)'}
        bundle = {'source': 'naver', 'title': '잔불의 기사 [3권 세트]'}
        self.assertTrue(m._metadata_source_variant_allowed(serialized, 'manhwa', 'series'))
        self.assertFalse(m._metadata_source_variant_allowed(volume, 'manhwa', 'series'))
        self.assertTrue(m._metadata_source_variant_allowed(volume, 'manhwa', 'single'))
        self.assertFalse(m._metadata_source_variant_allowed(serialized, 'manhwa', 'single'))
        self.assertFalse(m._metadata_source_variant_allowed(bundle, 'manhwa', 'single'))

    def test_novel_result_labels_never_fall_back_to_comic(self):
        self.assertEqual(m._metadata_candidate_variant_label({
            'source': 'ridi', 'variant_label': '만화 e북',
            'title': '택배 왔습니다', 'genre': '현대물, 판타지물, BL 소설 e북',
        }, 'novel', 'novel'), 'BL 소설 e북')
        self.assertEqual(m._metadata_candidate_variant_label({
            'source': 'naver', 'variant_label': '만화 e북',
            'title': '택배 왔습니다 [BL][단행본] (총 2권/완결)',
        }, 'novel', 'novel'), 'BL 소설 e북')
        self.assertEqual(m._metadata_candidate_variant_label({
            'source': 'naver', 'variant_label': '만화 연재',
            'title': '치명적 택배-택배 왔습니다만[BL] (총 45화/완결)',
        }, 'novel', 'novel'), 'BL 웹소설')
        self.assertEqual(m._metadata_candidate_variant_label({
            'source': 'munpia', 'variant_label': '만화 e북',
            'title': '택배 왔습니다', 'genre': '웹소설, 무협',
        }, 'novel', 'novel'), '웹소설')

    def test_naver_series_primary_is_enriched_by_webtoon(self):
        primary = {
            'id': 'naver:1', 'source': 'naver', 'source_label': '네이버시리즈',
            'title': '잔불의 기사 (총 246화/미완결)',
            'url': 'https://series.naver.com/comic/detail.series?productNo=6034771',
            'publisher': '네이버시리즈', 'cover': 'series-cover',
        }
        fallback = {
            'id': 'naver_webtoon:768536', 'source': 'naver_webtoon',
            'title': '잔불의 기사', 'author': '환댕', 'publisher': '네이버웹툰',
            'url': 'https://comic.naver.com/webtoon/list?titleId=768536',
            'publication_status': '1', 'publication_start_date': '2021-03-21',
            'total_chapters': 246, 'cover': 'webtoon-cover',
        }
        detail = {
            'title': '잔불의 기사', 'author': '환댕', 'publisher': '네이버시리즈',
            'genre': '소년', 'cover': 'series-cover', 'publication_status': '0',
            'link': primary['url'],
        }
        with patch.object(m, '_remote_fetch_metadata', return_value=detail):
            combined = self.provider()._combine_naver_candidates(
                [primary, fallback], 'series')
        self.assertEqual(len(combined), 1)
        row = combined[0]
        self.assertEqual(row['source_label'], '네이버시리즈 + 네이버웹툰')
        self.assertEqual(row['title'], '잔불의 기사 (총 246화/미완결)')
        self.assertEqual(row['metadata']['publisher'], '네이버시리즈')
        self.assertEqual(row['metadata']['cover'], 'series-cover')
        self.assertEqual(row['metadata']['publication_status'], '1')
        self.assertEqual(row['metadata']['publication_start_date'], '2021-03-21')
        self.assertIn('series.naver.com', row['metadata']['link'])
        self.assertIn('comic.naver.com', row['metadata']['link'])

        with patch.object(m, '_remote_fetch_metadata', return_value=detail):
            volume = self.provider()._combine_naver_candidates(
                [{**primary, 'title': '잔불의 기사 [단행본] (총 3권/미완결)'}, fallback],
                'single')
        self.assertNotIn('total_chapters', volume[0]['metadata'])

    def test_naver_webtoon_hiatus_metadata(self):
        search = {'searchWebtoonResult': {'searchViewList': [{
            'titleId': 768536, 'titleName': '잔불의 기사',
            'communityArtists': [{'name': '환댕'}], 'rest': True,
            'finished': False, 'articleTotalCount': 246,
            'lastArticleServiceDate': '26.07.05',
        }]}}
        detail = {
            'titleId': 768536, 'titleName': '잔불의 기사', 'rest': True,
            'finished': False, 'thumbnailUrl': 'https://example.com/cover.jpg',
            'age': {'type': 'RATE_15'},
            'curationTagList': [
                {'tagName': '판타지', 'curationType': 'GENRE_FANTASY'},
                {'tagName': '성장물', 'curationType': 'CUSTOM_TAG'},
            ],
        }
        chronology = {'totalCount': 246, 'articleList': [
            {'no': 1, 'serviceDateDescription': '21.03.21'},
        ]}

        def response(url, *_args, **_kwargs):
            if '/api/search/all?' in url:
                return search
            if '/api/article/list/info?' in url:
                return detail
            if '/api/article/list?' in url:
                return chronology
            raise AssertionError(url)

        adapter = api.SearchAdapter(lambda query, title: query == title)
        with patch.object(adapter, '_get_json', side_effect=response):
            row = adapter._search_naver_webtoon('잔불의 기사', {'MAX_RESULTS': 20})[0]
        self.assertEqual(row['publisher'], '네이버웹툰')
        self.assertEqual(row['publication_status'], '1')
        self.assertEqual(row['publication_start_date'], '2021-03-21')
        self.assertEqual(row['publication_end_date'], '')
        self.assertEqual(row['total_chapters'], 0)
        self.assertEqual(row['books_lv'], 'ma15+')
        self.assertEqual(row['genre'], '판타지')
        self.assertEqual(row['tags'], '성장물')

    def test_naver_webtoon_completed_chapters_and_end_date(self):
        search = {'searchWebtoonResult': {'searchViewList': [{
            'titleId': 1, 'titleName': '완결 작품', 'finished': True,
            'articleTotalCount': 44, 'lastArticleServiceDate': '24.05.06',
        }]}}
        detail = {'titleId': 1, 'titleName': '완결 작품', 'finished': True, 'rest': False}
        chronology = {'totalCount': 44, 'articleList': [
            {'no': 1, 'serviceDateDescription': '20.01.02'},
        ]}

        def response(url, *_args, **_kwargs):
            if '/api/search/all?' in url:
                return search
            if '/api/article/list/info?' in url:
                return detail
            if '/api/article/list?' in url:
                return chronology
            raise AssertionError(url)

        adapter = api.SearchAdapter(lambda query, title: query == title)
        with patch.object(adapter, '_get_json', side_effect=response):
            row = adapter._search_naver_webtoon('완결 작품', {'MAX_RESULTS': 20})[0]
        self.assertEqual(row['publication_status'], '2')
        self.assertEqual(row['publication_start_date'], '2020-01-02')
        self.assertEqual(row['publication_end_date'], '2024-05-06')
        self.assertEqual(row['total_chapters'], 44)

    def test_naver_genre_and_publisher(self):
        page = '<a href="?genreCode=201">로맨스</a><ul class="end_info"><li><span><a href="?genreCode=206">무협</a></span></li><li><span>출판사</span><a>제이플러스</a></li></ul><div class="end_dsc">'
        metadata = m._naver_role_metadata(page)
        self.assertEqual(metadata['genre'], '무협')
        self.assertEqual(metadata['publisher'], '제이플러스')
        metadata['tags'] = m._metadata_remove_terms('NOVEL, 무협, 먼치킨', 'NOVEL, COMIC, WEBTOON')
        self.assertEqual(m._metadata_clean(metadata)['tags'], '먼치킨')

    def test_naver_series_publisher_is_stable(self):
        from types import SimpleNamespace
        page = '''
          <meta property="og:title" content="작품">
          <ul class="end_info">
            <li><span>글</span><a>작가</a></li>
            <li><span>출판사</span><a>별도 임프린트</a></li>
          </ul><div class="end_dsc"></div>
        '''
        response = SimpleNamespace(text=page, raise_for_status=lambda: None)
        with patch('requests.get', return_value=response):
            metadata = m._remote_fetch_metadata(
                'https://series.naver.com/comic/detail.series?productNo=1', 'naver')
        self.assertEqual(metadata['publisher'], '네이버시리즈')

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
        self.assertEqual(row['genre'], '웹소설, 공포, 현대')
        self.assertEqual(row['tags'], '오컬트')
        self.assertEqual(row['publication_status'], '2')
        self.assertEqual(row['books_lv'], 'everyone')

    def test_munpia_detail_metadata(self):
        search = {'result': {'searchNovelTabDtos': [{
            'novelId': 366476, 'title': '금제술사의 대장간', 'author': '스테리엘',
            'mainGenre': '판타지', 'subGenre': '라이트노벨', 'entryCount': 37,
        }]}}
        detail = {'code': 'M000_00000', 'result': {'novelInfo': {
            'id': 366476, 'title': '금제술사의 대장간', 'authorName': '스테리엘',
            'genres': ['판타지', '라이트노벨'], 'tags': [
                {'title': '판타지'}, {'title': '천재'}, {'title': '이세계'},
            ],
            'finish': True, 'pause': False, 'chapterCount': 40,
            'createdAt': '2023-05-15T20:16:57',
            'updatedAt': '2026-09-21T23:53:56', 'isbn': 'G720:TEST',
        }}}

        def response(url, *_args, **_kwargs):
            return detail if '/pc/novel-detail/' in url else search

        adapter = api.SearchAdapter(lambda query, title: query == title)
        with patch.object(adapter, '_get_json', side_effect=response):
            row = adapter._search_munpia('금제술사의 대장간', {'MAX_RESULTS': 20})[0]
        self.assertEqual(row['genre'], '웹소설, 판타지, 라이트노벨')
        self.assertEqual(row['tags'], '판타지, 천재, 이세계')
        self.assertEqual(row['publication_status'], '2')
        self.assertEqual(row['publication_start_date'], '2023-05-15T20:16:57')
        self.assertEqual(row['publication_end_date'], '2026-09-21T23:53:56')
        self.assertEqual(row['total_chapters'], 40)
        self.assertEqual(row['isbn'], 'G720:TEST')

        detail['result']['novelInfo']['isbn'] = ''
        with patch.object(adapter, '_get_json', side_effect=response):
            row = adapter._search_munpia('금제술사의 대장간', {'MAX_RESULTS': 20})[0]
        self.assertEqual(row['isbn'], 'munpia:366476')

    def test_kakaopage_extended_metadata(self):
        search = {'result': {'list': [{
            'series_id': 57868498, 'title': '변경백 서자는 황제였다',
        }]}}
        overview = {'result': {'content': {
            'series_id': 57868498, 'title': '변경백 서자는 황제였다',
            'authors': '기준석', 'category': '웹소설', 'sub_category': '판타지',
            'thumbnail': 'cover-key', 'age_grade': 0, 'on_issue': 'N',
            'start_sale_dt': '2021-11-17T11:52:26+09:00',
            'last_slide_added_dt': '2026-01-10T17:50:15+09:00',
            'on_sale_count': 1123,
        }}}
        about = {'result': {
            'description': '작품 설명',
            'theme_keyword_list': [{'title': '회귀'}, {'title': '먼치킨'}],
            'detail': {'publisher_name': '판시아', 'category_list': ['웹소설', '판타지']},
        }}

        def response(url, *_args, **_kwargs):
            if '/search/series?' in url:
                return search
            if '/content/overview?' in url:
                return overview
            if '/content/about?' in url:
                return about
            raise AssertionError(url)

        adapter = api.SearchAdapter(lambda query, title: query == title)
        with patch.object(adapter, '_get_json', side_effect=response):
            row = adapter._search_kakaopage('변경백 서자는 황제였다', {'MAX_RESULTS': 20})[0]
        self.assertEqual(row['genre'], '웹소설, 판타지')
        self.assertEqual(row['tags'], '회귀, 먼치킨')
        self.assertEqual(row['publisher'], '판시아')
        self.assertEqual(row['publication_status'], '2')
        self.assertEqual(row['publication_start_date'], '2021-11-17T11:52:26+09:00')
        self.assertEqual(row['publication_end_date'], '2026-01-10T17:50:15+09:00')
        self.assertEqual(row['total_chapters'], 1123)

    def test_kakaopage_uses_webtoon_category_for_manhwa(self):
        categories = []

        def fake_search(_adapter, _query, cfg):
            categories.append(cfg.get('KAKAOPAGE_CATEGORY'))
            return []

        with patch.object(api.SearchAdapter, '_search_kakaopage', fake_search):
            api.search('kakaopage', '작품', lambda _query, _title: True,
                       content_kind='manhwa')
            api.search('kakaopage', '작품', lambda _query, _title: True,
                       content_kind='novel')
        self.assertEqual(categories, ['webtoon', 'novel'])

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
        def fake(source, query, matches, **kwargs):
            self.assertEqual(query, '작품')
            return [self.row(source, '다른 사람'), self.row(source, '작가')]
        with patch.object(m, 'search_additional_provider', fake):
            rows = self.provider()._search_metadata('작품 [작가]', {'metadata_sources': 'munpia'},
                                                    content_kind='novel', manual=True)
        self.assertEqual([r['author'] for r in rows], ['작가'])
        self.assertTrue(m._metadata_title_matches('작품', '작품의 다음 이야기', True))
        self.assertFalse(m._metadata_title_matches('작품', '작품의 다음 이야기'))

    def test_failure_isolated(self):
        def fake(source, query, matches, **kwargs):
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
            def get_setting(self, *args):
                return None
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
