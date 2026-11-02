import unittest
from ..rabbit_plugins import RabbitPluginsMetadataProvider as Provider, _series_db_title_matches

class SeriesIdentityTests(unittest.TestCase):
    def test_qualifiers_are_identity(self):
        for title in ('킹덤 (K-drama)', '킹덤 [K-drama]', '킹덤 (외전)'):
            self.assertFalse(_series_db_title_matches('킹덤', title))
        self.assertTrue(_series_db_title_matches('킹덤 (K-drama)', '킹덤 (K-drama)'))
        self.assertTrue(_series_db_title_matches('“킹덤”', '"킹덤"'))

    def test_auto_and_manual_enrichment_exclude_different_work(self):
        candidates = [dict(id='series_db:'+str(i), source='series_db', title=t,
                           _match_titles=[t], metadata={}) for i,t in
                      ((1797,'킹덤'),(111227,'킹덤 (K-drama)'))]
        provider = object.__new__(Provider)
        exact = provider._series_db_exact_candidates('킹덤','manga',candidates=candidates)
        selected = provider._select_auto_candidates(candidates,'킹덤','manga','', ['series_db'])
        self.assertEqual([x['id'] for x in exact], ['series_db:1797'])
        self.assertEqual([x['id'] for x in selected], ['series_db:1797'])

    def test_series_db_ignores_author_hint(self):
        candidate=dict(id='series_db:84213',source='series_db',title='공녀 전하의 가정교사',
                       _match_author='Riku Nanano',metadata={})
        provider=object.__new__(Provider)
        selected=provider._select_auto_candidates([candidate],'공녀 전하의 가정교사','novel','novel',
                                                  ['series_db'],author_hint='나나노 리쿠')
        self.assertEqual(selected,[candidate])
