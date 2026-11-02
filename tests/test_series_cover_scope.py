import unittest
from .. import rabbit_plugins as m

class SeriesCoverScopeTests(unittest.TestCase):
    def test_series_db_cover_only_for_webtoon(self):
        p=object.__new__(m.RabbitPluginsMetadataProvider)
        for kind in ('manga','novel','book','hentai','unspecified','manhwa'):
            result=p._merge_metadata_candidates([
                {'source':'series_db','metadata':{'cover':'https://local/main','localized_series':'original'}},
                {'source':'ridi','metadata':{'cover':'https://ridi/main','cover_by_volume':{'1':'https://ridi/1'}}}],kind)
            with self.subTest(kind=kind):
                self.assertEqual(result['cover_source'],'series_db' if kind=='manhwa' else 'ridi')
                self.assertEqual(result['metadata']['localized_series'],'original')
    def test_series_db_alone_has_no_nonwebtoon_cover(self):
        p=object.__new__(m.RabbitPluginsMetadataProvider)
        r=p._merge_metadata_candidates([{'source':'series_db','cover':'https://local/main','cover_by_volume':{'1':'https://local/1'}}],'manga')
        self.assertFalse(r['metadata'].get('cover'))
        self.assertFalse(r.get('cover_by_volume'))
    def test_unlabelled_multiple_files_require_exact_covers(self):
        self.assertTrue(m._requires_volume_specific_cover([{'title':'a'},{'title':'b'}]))
    def test_single_second_volume_cannot_take_representative_cover(self):
        self.assertTrue(m._requires_volume_specific_cover([{'title':'작품 02권'}]))
    def test_stored_volume_resolves_cover_without_guessing_title_numbers(self):
        self.assertEqual(m._row_mapped_cover({'volume_index':2,'title':'book'}, {'2':'https://ridi/2'},{}),'https://ridi/2')
        self.assertEqual(m._row_mapped_cover({'title':'작품 0-365'}, {'1':'https://ridi/1'},{}),'')

    def test_representative_repeated_in_volume_map_is_rejected(self):
        self.assertEqual(m._row_mapped_cover({'title':'작품 02권'}, {'1':'https://img/same','2':'https://img/same'},{}),'')
