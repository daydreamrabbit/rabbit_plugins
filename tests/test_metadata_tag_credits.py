import unittest
from .. import rabbit_plugins as m

class CreditTagTests(unittest.TestCase):
    def test_union_keeps_keywords_but_not_any_provider_credit(self):
        p=object.__new__(m.RabbitPluginsMetadataProvider)
        merged=p._merge_metadata_candidates([
            {'source':'ridi','metadata':{'title':'작품','author':'카네시로 무네유키','cover_artist':'노무라 유스케','tags':'스포츠물'}},
            {'source':'naver','metadata':{'author':'Muneyuki Kaneshiro','tags':'카네시로 무네유키, 노무라 유스케, 성장'}},
        ],'manga')
        self.assertEqual(merged['metadata']['tags'],'스포츠물, 성장')
        self.assertEqual(merged['metadata']['author'],'카네시로 무네유키')

    def test_existing_keywords_preserved_during_credit_cleanup(self):
        row={'author':'카네시로 무네유키','cover_artist':'노무라 유스케'}
        self.assertEqual(m._metadata_content_tags(
            '스포츠물, 노무라 유스케, 카네시로 무네유키, 스포츠드라마, 성장',row),
            '스포츠물, 스포츠드라마, 성장')

    def test_naver_transform_removal_does_not_strip_other_parameters(self):
        self.assertEqual(m._metadata_cover_url('https://comicthumb-phinf.pstatic.net/a.jpg?type=m600x314&v=2'),
                         'https://comicthumb-phinf.pstatic.net/a.jpg?v=2')
        self.assertEqual(m._metadata_cover_url('https://example.org/a.jpg?type=m600x314'),
                         'https://example.org/a.jpg?type=m600x314')
