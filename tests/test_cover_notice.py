import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from PIL import Image
from .. import rabbit_plugins as m

class NoticeTests(unittest.TestCase):
 def test_known_notice_and_reencoded_notice_rejected(self):
  p=Path(m.__file__).parent/'assets/ridi-age-placeholder.webp'
  self.assertTrue(m._cover_matches_age_placeholder(str(p),0,0))
  with tempfile.TemporaryDirectory() as d:
   dest=Path(d)/'notice.jpg'
   with Image.open(p) as im:im.resize((600,900)).save(dest,quality=85)
   self.assertTrue(m._cover_matches_age_placeholder(str(dest),0,0))
   Image.new('RGB',(300,450),'gray').save(dest)
   self.assertFalse(m._cover_matches_age_placeholder(str(dest),1,0))
 def test_notice_cannot_replace_existing_cover(self):
  g=Mock()
  with patch.object(m,'_stored_cover_is_notice',return_value=True):
   self.assertEqual(m._commit_external_cover(g,{'id':1},'notice.webp',True),0)
   m._remember_external_cover(g,1,'notice.webp',1,'manga','ridi')
  g.execute.assert_not_called();g.set_setting.assert_not_called()
