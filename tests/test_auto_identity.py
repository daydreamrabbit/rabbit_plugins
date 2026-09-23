import unittest
from unittest.mock import patch
from .. import rabbit_plugins as m


class CaptureGateway:
 def __init__(self):
  self.query='';self.params=()
 def fetch_all(self,query,params=()):
  self.query=query;self.params=params;return []


class ApplyGateway:
 def __init__(self): self.updates=[]
 def fetch_one(self,query,params=()):
  if 'FROM libraries' in query:return {'content_kind':'manga'}
  if 'FROM books b WHERE b.id' in query:
   return {'id':1,'series_name':'작품','library_id':4,'file_path':'/books/작품.cbz',
    'cover_image':'','metadata_locked':0,'author':'내부 작가','isbn':'내부 ISBN',
    'publisher':'내부 출판사','summary':'내부 소개','link':'','genre':'내부 장르',
    'tags':'내부 태그','release_date':'','title_alias':'','localized_series':'','cover_artist':''}
  return {}
 def fetch_all(self,query,params=()):
  if query.startswith('SELECT id, title, title_alias'):
   return [{'id':1,'title':'작품 1권','title_alias':'','file_path':'/books/작품 1권.cbz',
    'cover_image':'','metadata_locked':0,'link':'','volume_index':1}]
  return []
 def execute(self,query,params=()): self.updates.append((query,params));return 1
 def get_setting(self,*_): return None
 def set_setting(self,*_): pass


class AutoIdentityTests(unittest.TestCase):
 def rows(self):
  return [dict(source='ridi',title='작품',author='신이난',url='https://ridibooks.com/books/1'),dict(source='naver',title='작품',url='https://series.naver.com/novel/detail.series?productNo=1'),dict(source='munpia',title='작품',author='야참타임')]
 def select(self,rows,author=''):
  return m.RabbitPluginsMetadataProvider._select_auto_candidates(rows,'작품','novel','',['ridi','naver','munpia'],author_hint=author)
 def test_different_authors_hold_all_automatic_application(self):
  with patch.object(m,'_remote_fetch_metadata',return_value={'author':'신이난'}):
   self.assertEqual(self.select(self.rows()),[])
 def test_author_conflict_exposes_candidates_for_manual_review(self):
  diagnostics=[]
  with patch.object(m,'_remote_fetch_metadata',return_value={'author':'신이난'}):
   selected=m.RabbitPluginsMetadataProvider._select_auto_candidates(
    self.rows(),'작품','novel','',['ridi','naver','munpia'],diagnostics=diagnostics)
  self.assertEqual(selected,[])
  self.assertEqual(diagnostics[0]['reason'],'author_conflict')
  self.assertEqual({row['author'] for row in diagnostics[0]['candidates']},
                   {'신이난','야참타임'})
 def test_full_detail_credits_override_short_search_credit(self):
  rows=[
   {'source':'ridi','title':'작품','_match_author':'S. 코스기 외 2명',
    'metadata':{'author':'S. 코스기, 카에데하라 코타, 헤이로'},
    '_metadata_fetched':True},
   {'source':'naver','title':'작품 [단행본] (총 1권/미완결)',
    '_match_titles':['작품'],'metadata':{'author':'카에데하라 코타'},
    '_metadata_fetched':True},
  ]
  selected=m.RabbitPluginsMetadataProvider._select_auto_candidates(
   rows,'작품','manga','single',['ridi','naver'])
  self.assertEqual([row['source'] for row in selected],['ridi','naver'])
 def test_shortened_detail_credit_keeps_full_search_credits(self):
  rows=[
   {'source':'ridi','title':'작품','url':'https://ridibooks.com/books/1',
    'author':'S. 코스기, 카에데하라 코타, 헤이로',
    'metadata':{'author':'S. 코스기, 카에데하라 코타, 헤이로'}},
   {'source':'naver','title':'작품 [단행본] (총 1권/미완결)',
    '_match_titles':['작품'],'metadata':{'author':'카에데하라 코타'},
    '_metadata_fetched':True},
  ]
  with patch.object(m,'_remote_fetch_metadata',return_value={'author':'S. 코스기 외 2명'}):
   selected=m.RabbitPluginsMetadataProvider._select_auto_candidates(
    rows,'작품','manga','single',['ridi','naver'])
  self.assertEqual([row['source'] for row in selected],['ridi','naver'])
  self.assertEqual(selected[0]['metadata']['author'],'S. 코스기, 카에데하라 코타, 헤이로')
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
 def test_overwrite_kind_targets_rows_even_when_metadata_is_complete(self):
  gateway=CaptureGateway();provider=object.__new__(m.RabbitPluginsMetadataProvider)
  provider.get_plugin_config=lambda *_:{
   'metadata_overwrite':True,
   'metadata_overwrite_kinds':'novel',
   'metadata_fields':'author,summary,genre,isbn',
  }
  provider.get_db_gateway=lambda *_:gateway
  result=provider._auto_collect('general',{'library_id':7})
  self.assertEqual(result['rows'],0)
  self.assertIn('l.content_kind',gateway.query)
  self.assertIn(' OR COALESCE(NULLIF(l.content_kind',gateway.query)
  self.assertEqual(gateway.params,(7,'novel'))
 def test_disabled_overwrite_keeps_missing_metadata_gate(self):
  gateway=CaptureGateway();provider=object.__new__(m.RabbitPluginsMetadataProvider)
  provider.get_plugin_config=lambda *_:{'metadata_overwrite':False}
  provider.get_db_gateway=lambda *_:gateway
  provider._auto_collect('general',{'library_id':3})
  self.assertNotIn('l.content_kind, ""), "unspecified") IN',gateway.query)
  self.assertEqual(gateway.params,(3,))
 def test_new_book_hook_defers_broad_overwrite_to_completion(self):
  gateway=CaptureGateway();provider=object.__new__(m.RabbitPluginsMetadataProvider)
  provider.get_plugin_config=lambda *_:{'metadata_overwrite':True,'metadata_overwrite_kinds':'novel'}
  provider.get_db_gateway=lambda *_:gateway
  provider._auto_collect('general',{'library_id':3,'_rabbit_allow_overwrite':False})
  self.assertNotIn('l.content_kind, ""), "unspecified") IN',gateway.query)
  self.assertEqual(gateway.params,(3,))
 def test_overwrite_replaces_internal_kavita_values(self):
  gateway=ApplyGateway();provider=object.__new__(m.RabbitPluginsMetadataProvider)
  config={'metadata_overwrite':True,'metadata_overwrite_kinds':'manga','metadata_collect_cover':False}
  item={'source':'munpia','metadata':{
   'author':'외부 작가','publisher':'외부 출판사','summary':'외부 소개',
   'genre':'외부 장르','tags':'외부 태그','isbn':'외부 ISBN'}}
  with patch.object(m,'_optional_column_sql',return_value='NULL'):
   ok,_=provider._apply_metadata(gateway,1,item,config,
    fields=['author','publisher','summary','genre','tags','isbn'])
  self.assertTrue(ok)
  update=next((params for query,params in gateway.updates if query.startswith('UPDATE books SET')),())
  self.assertIn('외부 작가',update);self.assertIn('외부 출판사',update)
  self.assertIn('외부 소개',update);self.assertIn('외부 장르',update)
  self.assertNotIn('내부 장르',update)
 def test_selected_provider_link_replaces_stale_edition(self):
  existing='https://ridibooks.com/books/old, https://series.naver.com/comic/detail.series?productNo=7'
  incoming='https://ridibooks.com/books/new'
  merged=m._replace_provider_links(existing,incoming)
  self.assertNotIn('/old',merged)
  self.assertIn('/new',merged)
  self.assertIn('series.naver.com',merged)
 def test_selected_product_genre_replaces_stale_variant(self):
  merged=m._merge_variant_genres('만화 e북, 19+','해외 순정, 만화 연재, 성인')
  self.assertEqual(merged,'만화 e북, 19+, 해외 순정, 성인')
 def test_join_terms_flattens_and_deduplicates_lists(self):
  self.assertEqual(m._join_terms(['현대배경, 회사','회사, 일상']),'현대배경, 회사, 일상')
