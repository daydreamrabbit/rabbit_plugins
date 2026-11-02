import json
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from .. import rabbit_plugins as m


def page(data):
    return '<script id="__NEXT_DATA__">'+json.dumps({'props': {'pageProps': data}})+'</script>'


class RidiPageFieldsTests(unittest.TestCase):
    def test_header_cover_requires_selected_product(self):
        def header(bid):
            return {'cell__BookDetailHomeHeader': {'information': {'bookId': bid},
                'thumbnail': {'cover': {'xxlarge': 'https://img.ridicdn.net/cover/'+bid+'/xxlarge'}}}}
        result=m._next_data_detail_metadata(page({'cells':[header('2'),header('1')]}),'1')
        self.assertEqual(result['cover'],'https://img.ridicdn.net/cover/1/xxlarge')

    def test_ecn_collected_as_uci(self):
        result=m._next_data_detail_metadata(page({'detail':{'ecn':'G720:T+A037-20260727102.M001'}}),'1')
        self.assertEqual(result['uci'],'G720:T+A037-20260727102.M001')
        self.assertFalse(m._next_data_detail_metadata(page({'ecn':'-'}),'1')['uci'])

    def test_bridge_preserves_existing_metadata(self):
        from .. import ridi_http
        response=SimpleNamespace(text=page({'adultBridge':{'descriptionLines':['로그인 후 이용해 주세요.']}}),raise_for_status=lambda:None)
        with patch.object(ridi_http,'get',return_value=response):
            result=m._remote_fetch_metadata('https://ridibooks.com/books/1','ridi')
        self.assertEqual(result['_fetch_error'],'adult_verification_required')
        self.assertNotIn('summary',result)

    def test_site_description_and_keywords_rejected(self):
        self.assertEqual(m._metadata_summary('웹툰, 웹소설, 전자책 모두 여기에! 이야기를 즐기는 나만의 공간, RIDI'),'')
        self.assertEqual(m._ridi_keyword_tags('리디,웹소설,webnovel,현대배경',{}),'현대배경')
        self.assertEqual(m._metadata_summary('웹소설을 쓰는 주인공의 이야기'),'웹소설을 쓰는 주인공의 이야기')

    def test_single_book_default_volume_does_not_require_volume_map(self):
        row={'title':'단편 (리디)#70','file_path':'/book/단편.cbz','volume_index':1.0}
        self.assertFalse(m._requires_volume_specific_cover([row]))
        self.assertTrue(m._requires_volume_specific_cover([row,row]))
        self.assertTrue(m._requires_volume_specific_cover([dict(row,volume_index=2)]))

    def test_legacy_series_maps_each_volume_not_representative_cover(self):
        source='var seriesBookListJson = '+json.dumps([
            {'id':'101','volume':'1','title':'작품 1'},
            {'id':'102','volume':'2','title':'작품 2'},
            {'id':'103','volume':'3','title':'작품 체험판','is_trial':True}])+';'
        volumes,_=m._ridi_volume_cover_maps(source)
        self.assertEqual(volumes,{'1':'https://img.ridicdn.net/cover/101/xxlarge#1',
                                 '2':'https://img.ridicdn.net/cover/102/xxlarge#1'})
