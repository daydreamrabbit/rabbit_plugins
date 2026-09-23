import unittest
from unittest.mock import patch
from .. import rabbit_plugins as m
class AutoIdentityTests(unittest.TestCase):
 def rows(self):
  return [dict(source='ridi',title='작품',author='신이난',url='https://ridibooks.com/books/1'),dict(source='naver',title='작품',url='https://series.naver.com/novel/detail.series?productNo=1'),dict(source='munpia',title='작품',author='야참타임')]
 def select(self,rows,author=''):
  return m.RabbitPluginsMetadataProvider._select_auto_candidates(rows,'작품','novel','',['ridi','naver','munpia'],author_hint=author)
 def test_different_authors_hold_all_automatic_application(self):
  with patch.object(m,'_remote_fetch_metadata',return_value={'author':'신이난'}):
   self.assertEqual(self.select(self.rows()),[])
 def test_hint_keeps_only_confirmed_author(self):
  with patch.object(m,'_remote_fetch_metadata',return_value={'author':'신이난'}):
   self.assertEqual([r['source'] for r in self.select(self.rows(),'신이난')],['ridi','naver'])
 def test_unverified_detail_not_merged(self):
  with patch.object(m,'_remote_fetch_metadata',return_value={}):
   self.assertEqual([r['source'] for r in self.select(self.rows()[:2])],['ridi'])
 def test_detail_author_overrides_search_card_for_identity(self):
  rows=self.rows()[:2];rows[1]['author']='신이난'
  with patch.object(m,'_remote_fetch_metadata',side_effect=[{'author':'신이난'},{'author':'다른작가'}]):
   self.assertEqual(self.select(rows),[])
 def test_ridi_flags_and_published_date(self):
  with patch.object(m,'_inline_json_object',return_value={'age_limit':'19','is_adult_only':True,'is_series_complete':'1','pub_date':'20160530','reg_date':'20160603144838','volume':'1'}):
   r=m._ridi_publication_metadata('')
  self.assertEqual(r['books_lv'],'m');self.assertEqual(r['publication_status'],'2')
  self.assertEqual(r['release_date'],'2016-05-30')
  self.assertEqual(r['publication_start_date'],'2016-05-30')
  self.assertNotIn('publication_end_date',r)
 def test_absent_flags_not_assumed_everyone_or_ongoing(self):
  with patch.object(m,'_inline_json_object',return_value={}):self.assertEqual(m._ridi_publication_metadata(''),{})
