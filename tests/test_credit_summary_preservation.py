import unittest
from .. import rabbit_plugins as m
from .test_provider_search import isolate_provider_state
class CreditSummaryTests(unittest.TestCase):
 def setUp(self):
  isolate_provider_state(self)

 def test_confirmation_does_not_fill_missing_artist_from_lower_provider(self):
  p=object.__new__(m.RabbitPluginsMetadataProvider)
  r=p._merge_metadata_candidates([
   {'source':'ridi','metadata':{'author':'이와시타 케이코','tags':'캠퍼스','link':'https://ridibooks.com/books/1'}},
   {'source':'naver','metadata':{'author':'Keiko Iwashita','cover_artist':'Keiko Iwashita','tags':'음악','link':'https://series.naver.com/comic/detail.series?productNo=1'}}],'manga')
  self.assertEqual(r['metadata']['author'],'이와시타 케이코')
  self.assertFalse(r['metadata'].get('cover_artist'))
  self.assertIn('캠퍼스',r['metadata']['tags']);self.assertIn('음악',r['metadata']['tags'])
  self.assertIn('ridibooks.com',r['metadata']['link']);self.assertIn('series.naver.com',r['metadata']['link'])
 def test_roles_and_spellings_are_preserved_from_priority_provider(self):
  p=object.__new__(m.RabbitPluginsMetadataProvider)
  r=p._merge_metadata_candidates([
   {'source':'ridi','metadata':{'author':'글 작가','cover_artist':'그림 작가'}},
   {'source':'naver','metadata':{'author':'Other writer','cover_artist':'Other artist'}}],'manga')
  self.assertEqual(r['metadata']['author'],'글 작가')
  self.assertEqual(r['metadata']['cover_artist'],'그림 작가')
 def test_lower_provider_can_supply_complete_record_when_priority_has_no_credits(self):
  p=object.__new__(m.RabbitPluginsMetadataProvider)
  r=p._merge_metadata_candidates([{'source':'ridi','metadata':{'publisher':'출판사'}},
   {'source':'naver','metadata':{'author':'Writer','cover_artist':'Artist'}}],'manga')
  self.assertEqual((r['metadata']['author'],r['metadata']['cover_artist']),('Writer','Artist'))
 def test_existing_intro_image_survives_plain_provider_text(self):
  s=m._summary_preserve_images('<img src="//misc.ridibooks.com/desc.intro/123"/>old','new')
  self.assertIn('https://misc.ridibooks.com/desc.intro/123',s);self.assertTrue(s.endswith('new'))
 def test_new_intro_image_replaces_old_without_duplication(self):
  incoming='<img src="https://example.com/new"/>new'
  self.assertEqual(m._summary_preserve_images('<img src="https://example.com/old"/>',incoming),incoming)
 def test_provider_product_scoped_intro_only(self):
  s=m._ridi_summary_with_images('<img src="//misc.ridibooks.com/desc.intro/1234"/><img data-src="//misc.ridibooks.com/desc.intro/123" onerror="evil()"/>','text','123')
  self.assertNotIn('1234',s);self.assertNotIn('onerror',s);self.assertIn('/123',s)
 def test_unsafe_image_scheme_rejected(self):
  self.assertEqual(m._summary_preserve_images('<img src="javascript:evil()"/>','text'),'text')
 def test_richer_summary_can_fill_missing_image(self):
  self.assertTrue(m._summary_should_refresh('text','<img src="https://example.com/i"/>text'))
 def test_apply_preserves_each_books_own_intro_image(self):
  from unittest.mock import patch
  from .test_auto_identity import ApplyGateway
  class Gateway(ApplyGateway):
   def fetch_one(self,q,p=()):
    r=super().fetch_one(q,p)
    if 'FROM books' in q:
     bid=p[0] if p else 1
     base=super().fetch_one('FROM books b WHERE b.id',(bid,))
     return dict(base,id=bid,summary=f'<img src="https://example.com/{bid}"/>old')
    return r
   def fetch_all(self,q,p=()):
    if q.startswith('SELECT id, title, title_alias'):
     base=super().fetch_all(q,p)[0]
     return [dict(base,id=1),dict(base,id=2)]
    return []
  g=Gateway();p=object.__new__(m.RabbitPluginsMetadataProvider)
  with patch.object(m,'_optional_column_sql',return_value='NULL'):
   ok,_=p._apply_metadata(g,1,{'source':'merged','metadata':{'summary':'new'}},
        {'metadata_overwrite':True,'metadata_overwrite_kinds':'manga'},fields=['summary'])
  self.assertTrue(ok)
  updates=[params for q,params in g.updates if 'summary = ?' in q]
  self.assertEqual(len(updates),2)
  self.assertIn('https://example.com/1',updates[0][0])
  self.assertIn('https://example.com/2',updates[1][0])
  self.assertNotIn('https://example.com/1',updates[1][0])

 def test_image_enrichment_respects_existing_text_without_overwrite(self):
  from unittest.mock import patch
  from .test_auto_identity import ApplyGateway
  g=ApplyGateway();p=object.__new__(m.RabbitPluginsMetadataProvider)
  with patch.object(m,'_optional_column_sql',return_value='NULL'):
   ok,_=p._apply_metadata(g,1,{'source':'merged','metadata':{'summary':'<img src="https://example.com/new"/>provider text'}},
        {'metadata_overwrite':False},fields=['summary'])
  self.assertTrue(ok)
  text=next(params[0] for q,params in g.updates if 'summary = ?' in q)
  self.assertIn('내부 소개',text);self.assertNotIn('provider text',text)
  self.assertIn('<img',text)

 def test_failed_priority_provider_cannot_replace_internal_credits_with_naver(self):
  p=object.__new__(m.RabbitPluginsMetadataProvider)
  r=p._merge_metadata_candidates([
   {'source':'ridi','metadata':{'_fetch_error':'HTTPError'}},
   {'source':'naver','metadata':{'author':'Rintarou Ohshima','cover_artist':'Rintarou Ohshima'}}],'manga')
  self.assertNotIn('author',r['metadata'])
  self.assertNotIn('cover_artist',r['metadata'])

 def test_killer_ridi_credits_win_over_naver(self):
  p=object.__new__(m.RabbitPluginsMetadataProvider)
  r=p._merge_metadata_candidates([
   {'source':'ridi','metadata':{'author':'오시마 린타로'}},
   {'source':'naver','metadata':{'author':'Rintarou Ohshima'}}],'manga')
  self.assertEqual(r['metadata']['author'],'오시마 린타로')

 def test_isbn_is_not_copied_to_sibling_volumes(self):
  from unittest.mock import patch
  from .test_auto_identity import ApplyGateway
  class Gateway(ApplyGateway):
   def fetch_all(self,q,p=()):
    rows=super().fetch_all(q,p)
    return rows+[dict(rows[0],id=2)] if rows else rows
  g=Gateway();p=object.__new__(m.RabbitPluginsMetadataProvider)
  with patch.object(m,'_optional_column_sql',return_value='NULL'):
   ok,_=p._apply_metadata(g,1,{'source':'merged','metadata':{'isbn':'9791142306273'}},
       {'metadata_overwrite':True,'metadata_overwrite_kinds':'manga'},fields=['isbn'])
  self.assertTrue(ok)
  updates=[params for q,params in g.updates if 'isbn = ?' in q]
  self.assertEqual(len(updates),1)
