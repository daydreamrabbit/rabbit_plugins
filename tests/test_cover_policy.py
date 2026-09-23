import tempfile
import unittest
import zipfile
from pathlib import Path
from .. import rabbit_plugins as m

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

    def test_webtoon_includes_existing_covers_but_respects_locks(self):
        row={'file_path':'book.cbz','cover_image':'old.webp'}
        self.assertTrue(m._metadata_cover_eligible(row,True,False))
        row['metadata_locked']=1
        self.assertFalse(m._metadata_cover_eligible(row,True,False))
        self.assertTrue(m._metadata_cover_eligible(row,True,True))

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
