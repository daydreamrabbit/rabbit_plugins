import unittest
import json
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
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
 def test_quoted_retailer_titles_retry_and_verify_full_work(self):
  query='“너 따위가 마왕을 이길 수 있다고 생각하지 마”라며 용사 파티에서 추방되었으니 왕도에서 멋대로 살고 싶다'
  phrase='너 따위가 마왕을 이길 수 있다고 생각하지 마'
  calls=[]
  def finder(term,partial):
   calls.append((term,partial))
   if term != phrase:return []
   return [
    {'title':'"너 따위가 마왕을 이길 수 있다고 생각하지 마”라며 용사 파티에서 추방되었으니 왕도에서 멋대로 살고 싶다'},
    {'title':'″너 따위가 마왕을 이길 수 있다고 생각하지 마″라며 용사 파티에서 추방되었으니 왕도에서 멋대로 살고 싶다'},
    {'title':'너 따위가 마왕을 이길 수 있다고 생각하지 마: 다른 이야기'},
   ]
  result=m._quoted_title_search(query,finder)
  self.assertEqual(len(result),2)
  self.assertEqual(calls,[(query,False),(phrase,True)])
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
  with patch.object(m,'_inline_json_object',return_value={'age_limit':'19','is_adult_only':True,'is_series':'1','series_id':'123','is_series_complete':'1','pub_date':'20160530','reg_date':'20160603144838','volume':'1'}):
   r=m._ridi_publication_metadata('')
  self.assertEqual(r['books_lv'],'m');self.assertEqual(r['publication_status'],'2')
  self.assertEqual(r['release_date'],'2016-05-30')
  self.assertEqual(r['publication_start_date'],'2016-05-30')
  self.assertNotIn('publication_end_date',r)
 def test_ridi_single_book_is_not_ongoing(self):
  with patch.object(m,'_inline_json_object',return_value={
      'is_series':'0','series_id':'','is_series_complete':'0',
      'volume':'0','pub_date':'20171122'}):
   result=m._ridi_publication_metadata('')
  self.assertTrue(result['is_standalone'])
  self.assertNotIn('publication_status',result)
  self.assertEqual(result['release_date'],'2017-11-22')
  self.assertTrue(m._metadata_clean(result)['is_standalone'])
  self.assertTrue(m._local_standalone_marker('3일간의 행복 (E000002904686) (단편) (교보).epub'))
  self.assertFalse(m._local_standalone_marker('작품 01권.epub'))
  self.assertFalse(m._local_standalone_marker('단편소설 모음.epub'))
  single=[{'title':'3일간의 행복','file_path':'3일간의 행복 (E000002904686) (단편) (교보).epub'}]
  self.assertTrue(m._standalone_series(single, {}))  # YES24/Kyobo have no status flag.
  self.assertTrue(m._standalone_series([{'title':'작품','file_path':'작품.epub'}], {'is_standalone':True}))
  self.assertFalse(m._standalone_series([{'title':'작품 01권','file_path':'작품 01권.epub'}], {'is_standalone':True}))
  self.assertFalse(m._standalone_series(single*2, {'is_standalone':True}))
 def test_standalone_apply_clears_old_ongoing_status(self):
  class Gateway(ApplyGateway):
   def __init__(self):
    super().__init__();self.settings={}
   def fetch_one(self,query,params=()):
    if 'FROM libraries' in query:return {'content_kind':'novel'}
    if 'SELECT publication_status FROM books' in query:return {'publication_status':'0'}
    if 'FROM books b WHERE b.id' in query:
     return {'id':1,'series_name':'3일간의 행복','library_id':12,
      'file_path':'3일간의 행복 (단편).epub','cover_image':'','metadata_locked':0,
      'author':'','isbn':'','publisher':'','summary':'','link':'','genre':'',
      'tags':'','release_date':'','title_alias':'','localized_series':'','cover_artist':''}
    return {}
   def fetch_all(self,query,params=()):
    if query.startswith('SELECT id, title, title_alias'):
     return [{'id':1,'title':'3일간의 행복','title_alias':'',
      'file_path':'3일간의 행복 (단편).epub','cover_image':'',
      'metadata_locked':0,'link':'','volume_index':None}]
    return []
   def get_setting(self,key,*_):return {'value':self.settings.get(key,'{}')}
   def set_setting(self,key,value):self.settings[key]=value
  gateway=Gateway()
  provider=object.__new__(m.RabbitPluginsMetadataProvider)
  with patch.object(m,'_optional_column_sql',return_value='NULL'):
   applied,_=provider._apply_metadata(gateway,1,
    {'source':'yes24','metadata':{'title':'3일간의 행복','publication_status':'0'}},
    {'metadata_collect_cover':False},fields=['publication_status'])
  self.assertTrue(applied)
  self.assertTrue(any('SET publication_status = NULL' in sql for sql,_ in gateway.updates))
  self.assertTrue(json.loads(gateway.settings[m._series_dates_key(12,'3일간의 행복')])['standalone'])
 def test_ridi_novel_ebook_categories_and_webnovel_genre(self):
  self.assertFalse(m._metadata_title_matches(
   '전설의 기사 아크리안','전설의 기사',allow_partial=True))
  for category in ('판타지 e북','로맨스 e북','로판 e북','BL 소설 e북'):
   book={'categories':[{'name':category}]}
   self.assertTrue(m._ridi_book_type_allowed('novel','전설의 기사 아크리안',book,{}),category)
   self.assertEqual(m._ridi_variant_label('작품',book,{},'novel'),'소설 e북')
  self.assertFalse(m._ridi_book_type_allowed(
   'novel','작품',{'categories':[{'name':'만화 e북'}]},{}))
  def search_card(category):
   return {'id':'123000264','book':{'title':{'main':'전설의 기사 아크리안 1권'},
    'series':{'title':'전설의 기사 아크리안'},
    'categories':[{'name':category}]}}
  for category,expected in [('판타지 e북','판타지 e북'),
                            ('판타지 웹소설','웹소설, 판타지 웹소설')]:
   html='<script id="__NEXT_DATA__" type="application/json">'+json.dumps(
    {'props':{'books':[search_card(category)]}},ensure_ascii=False)+'</script>'
   response=SimpleNamespace(text=html,raise_for_status=lambda:None)
   with patch('requests.get',return_value=response):
    results=m._remote_search_page('ridi','전설의 기사 아크리안','novel','novel')
   self.assertEqual(len(results),1,category)
   self.assertEqual(results[0]['genre'],expected)
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
 def test_new_series_db_link_replaces_stale_mangabaka_work(self):
  existing='https://mangabaka.org/80492, https://ridibooks.com/books/123'
  incoming='https://mangabaka.org/664, https://ridibooks.com/books/123'
  merged=m._replace_provider_links(existing,incoming)
  self.assertEqual(merged,'https://mangabaka.org/664, https://ridibooks.com/books/123')
 def test_exact_series_db_title_prevents_same_author_wrong_work(self):
  provider=object.__new__(m.RabbitPluginsMetadataProvider)
  provider._series_db_path=lambda:SimpleNamespace(is_file=lambda:True)
  provider._series_db_search=lambda *_args,**_kwargs:[{
   '_match_titles':['옆자리 녀석이 그런 눈으로 쳐다본다'],
   'metadata':{'localized_series':'となりの席のヤツがそういう目で見てくる',
               'link':'https://mangabaka.org/664'}}]
  identity=provider._series_db_remote_identity(
   {'title':'옆자리 녀석이 그런 눈으로 쳐다본다 [단행본] (총 4권/미완결)'},
   {'author':'mmk'},'manga')
  self.assertEqual(identity['link'],'https://mangabaka.org/664')
 def test_series_db_mixed_quote_alias_matches_novel_folder(self):
  title='“너 따위가 마왕을 이길 수 있다고 생각하지 마”라며 용사 파티에서 추방되었으니 왕도에서 멋대로 살고 싶다'
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/'series.sqlite'
   with sqlite3.connect(path) as db:
    db.execute('CREATE TABLE series (id INTEGER, title TEXT, native_title TEXT, '
     'secondary_titles_ko TEXT, titles TEXT, type TEXT, links TEXT, authors TEXT, '
     'artists TEXT, cover_raw_url TEXT, status TEXT, final_volume INTEGER, content_rating TEXT)')
    db.execute('INSERT INTO series VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',(
     82700,'Do You Think Someone Like You Can Defeat the Demon King?',
     '「お前ごときが魔王に勝てると思うな」と勇者パーティを追放されたので、王都で気ままに暮らしたい',
     '[]',json.dumps([{'language':'ko','title':'"'+title[1:]}],ensure_ascii=False),
     'novel',json.dumps(['https://mangabaka.org/82700']),json.dumps(['kiki']),
     '[]','',None,None,None))
   provider=object.__new__(m.RabbitPluginsMetadataProvider)
   provider._series_db_path=lambda:path
   result=provider._series_db_search(title,'novel')
   self.assertEqual([item['id'] for item in result],['series_db:82700'])
   self.assertTrue(any(m._metadata_title_matches(title,match)
                       for match in result[0]['_match_titles']))
 def test_scan_restore_keeps_volume_ridi_and_recovers_other_sources(self):
  class LinkGateway:
   def __init__(self):
    self.settings={};self.updates=[]
   def get_setting(self,key,default=None): return self.settings.get(key,default)
   def set_setting(self,key,value): self.settings[key]=value
   def fetch_all(self,query,params=()):
    self.assert_query=query
    return [{'id':1,'link':'https://mangabaka.org/80492, https://ridibooks.com/books/volume-1','metadata_locked':0},
            {'id':2,'link':'https://mangabaka.org/664, https://ridibooks.com/books/volume-2','metadata_locked':0}]
   def execute(self,query,params=()): self.updates.append((query,params))
  gateway=LinkGateway()
  expected=('https://mangabaka.org/664, https://ridibooks.com/books/series, '
            'https://series.naver.com/comic/detail.series?productNo=14648405, '
            'https://www.yes24.com/product/goods/195471963')
  m._remember_external_links(gateway,2,'옆자리 녀석이 그런 눈으로 쳐다본다',expected)
  provider=SimpleNamespace(get_db_gateway=lambda *_:gateway)
  self.assertEqual(m._reconcile_external_links(provider,'general',2),2)
  for _,(link,book_id) in gateway.updates:
   self.assertIn(f'ridibooks.com/books/volume-{book_id}',link)
   self.assertIn('series.naver.com',link)
   self.assertIn('yes24.com',link)
   self.assertIn('mangabaka.org/664',link)
   self.assertNotIn('mangabaka.org/80492',link)
 def test_selected_product_genre_replaces_stale_variant(self):
  merged=m._merge_variant_genres('만화 e북, 19+','해외 순정, 만화 연재, 성인')
  self.assertEqual(merged,'만화 e북, 19+, 해외 순정, 성인')

 def test_new_final_file_refreshes_only_completion_fields(self):
  m._COMPLETION_SCAN_MARKERS.clear()
  class Gateway:
   def __init__(self):
    self.updates=[];self.settings={}
   def fetch_all(self,query,params=()):
    if 'ORDER BY id DESC LIMIT ?' in query:
     return [{'id':12,'series_name':'작품','title':'작품 02권 (완결)',
              'file_path':'/books/작품 02권 (완결).epub'}]
    if 'publication_status, tags, metadata_locked' in query:
     return [
      {'id':11,'title':'작품 01권','file_path':'/books/작품 01권.epub',
       'publication_status':'0','tags':'기존','metadata_locked':0},
      {'id':12,'title':'작품 02권 (완결)','file_path':'/books/작품 02권 (완결).epub',
       'publication_status':'0','tags':'기존','metadata_locked':0}]
    return []
   def fetch_one(self,query,params=()):return {'content_kind':'manga'}
   def execute(self,query,params=()):self.updates.append((query,params));return 1
   def get_setting(self,key,default=None):return self.settings.get(key,default)
   def set_setting(self,key,value):self.settings[key]=value
  gateway=Gateway();provider=object.__new__(m.RabbitPluginsMetadataProvider)
  provider.get_plugin_config=lambda *_:{'metadata_overwrite':True,
   'metadata_overwrite_kinds':'manga'}
  provider.get_db_gateway=lambda *_:gateway
  provider._search_metadata=lambda *args,**kwargs:[{'source':'ridi','title':'작품'}]
  provider._select_auto_candidates=lambda *args,**kwargs:[{'source':'ridi','title':'작품'}]
  provider._merge_metadata_candidates=lambda *args,**kwargs:{'metadata':{
   'publication_status':'2','publication_end_date':'2026-09-24','tags':'기존, 완결태그',
   'author':'다른 작가','summary':'다른 소개','cover':'https://example.com/cover'}}
  with patch.object(m,'_optional_column_sql',return_value='NULL'):
   first=provider._auto_collect('general',{'library_id':4,'new_books_count':1,
    '_rabbit_allow_overwrite':False})
   second=provider._auto_collect('general',{'library_id':4,'new_books_count':1,
    '_rabbit_allow_overwrite':True})
  self.assertEqual(first['updated'],0)
  self.assertEqual(second['updated'],3)
  self.assertEqual(len(gateway.updates),2)
  self.assertTrue(all('publication_status' in sql and 'tags' in sql
                      and 'author' not in sql and 'summary' not in sql
                      and 'cover' not in sql for sql,_ in gateway.updates))
  self.assertTrue(all('기존, 완결태그' in params for _,params in gateway.updates))
  self.assertIn('2026-09-24',next(iter(gateway.settings.values())))
  m._COMPLETION_SCAN_MARKERS.clear()
 def test_join_terms_flattens_and_deduplicates_lists(self):
  self.assertEqual(m._join_terms(['현대배경, 회사','회사, 일상']),'현대배경, 회사, 일상')
