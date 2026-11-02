import unittest
import json
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from flask import Flask, session
from .. import rabbit_plugins as m
from .test_provider_search import isolate_provider_state


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
 def setUp(self):
  isolate_provider_state(self)

 def test_linked_ridi_volume_outweighs_romanized_store_credit(self):
  query='기계 인간 마리 + (플러스)'
  remote={'title':'기계 인간 마리','author':'아키모토 아키',
          'cover_artist':'아키모토 아키','publisher':'서울미디어코믹스',
          'summary':'부모 시리즈 소개','isbn':'부모 ISBN','publication_status':'연재',
          'cover_by_title':{'기계인간마리1권':'base-cover',
                            '기계인간마리+(플러스)1권':'plus-cover'}}
  with patch.object(m,'_remote_fetch_metadata',return_value=remote):
   linked=m._ridi_linked_volume_candidate(query,'https://ridibooks.com/books/845030662')
   self.assertIsNone(m._ridi_linked_volume_candidate(
       '다른 작품','https://ridibooks.com/books/845030662'))
  self.assertEqual(linked['metadata']['cover'],'plus-cover')
  self.assertNotIn('summary',linked['metadata'])
  self.assertNotIn('isbn',linked['metadata'])
  selected=m.RabbitPluginsMetadataProvider._select_auto_candidates(
      [linked,{'source':'yes24','title':query,'author':'Aki Akimoto'}],
      query,'manga','single',['ridi','yes24'],author_hint='아키모토 아키')
  self.assertEqual([item['source'] for item in selected],['ridi'])
  self.assertEqual(m._metadata_author_cleanup('Aki Akimoto','아키모토 아키','아키모토 아키'),
                   '아키모토 아키')

 def test_confirm_held_same_work_rechecks_all_sources(self):
  held=[{'source':'ridi','title':'곰과 토끼는 친구로 지낼 수 없다','author':'이누요시 아키라'},
        {'source':'naver','title':'곰과 토끼는 친구로 지낼 수 없다 [단행본] (총 2권/미완결)','author':'Akira Inuyoshi'},
        {'source':'yes24','title':'곰과 토끼는 친구로 지낼 수 없다','author':'이누요시 아키라 글그림'}]
  fresh=[{'source':'ridi','title':held[0]['title'],'author':held[0]['author']},
         {'source':'naver','title':held[1]['title'],'url':'https://series.naver.com/1'},
         {'source':'yes24','title':held[2]['title'],'author':'이누요시 아키라'}]
  class Gateway:
   def fetch_one(self,sql,_params=()):
    if 'FROM books' in sql:
     return {'id':1,'series_name':held[0]['title'],'library_id':2,
             'title':held[0]['title']+' 01권','file_path':'/books/01권.cbz'}
    return {'content_kind':'manga'}
   def get_setting(self,_key):
    return {'value':json.dumps({'reason':'author_conflict','query':held[0]['title'],
                                'candidates':held})}
  provider=object.__new__(m.RabbitPluginsMetadataProvider)
  provider.get_db_gateway=lambda *_:Gateway()
  provider.get_plugin_config=lambda *_:{'metadata_sources':'ridi,naver,yes24'}
  provider._search_metadata=lambda *_args,**_kwargs:fresh
  selected=[]
  provider._merge_metadata_candidates=lambda rows,_kind,**_kwargs:(selected.extend(rows) or
       {'source':'merged','metadata':{'author':'이누요시 아키라'}})
  applied_options=[]
  def apply(*_args,**kwargs):
   applied_options.append(kwargs)
   return True,'적용 완료'
  provider._apply_metadata=apply
  app=Flask(__name__);app.secret_key='test'
  with app.test_request_context('/'):
   session['role']='admin'
   with patch.object(m,'_optional_column_sql',return_value='NULL'),\
        patch.object(m,'_remote_fetch_metadata',return_value={'author':'Akira Inuyoshi'}):
    result=provider.run_context_menu_action('general','metadata_confirm_same_work',{'book_id':1})
  self.assertTrue(result['success'])
  self.assertEqual([item['source'] for item in selected],['ridi','naver','yes24'])
  self.assertTrue(applied_options[0]['preserve_existing'])

 def test_confirm_held_same_work_uses_ridi_role_credits(self):
  title='[바닐라] 이세계 NTR ~절친의 여자를 최강 스킬로 함락시키는 방법~'
  ridi_url='https://ridibooks.com/books/806015466'
  yes24_url='https://www.yes24.com/product/goods/141731553'
  held=[{'source':'ridi','title':title,'author':'고리 란도우','url':ridi_url},
        {'source':'yes24','title':title,'author':'사토 케네츠','url':yes24_url}]
  fresh=[{'source':'ridi','title':title,'author':'사토 켄에츠, 고리 란도우',
          'metadata':{'author':'사토 켄에츠, 고리 란도우'},'url':ridi_url},
         {'source':'yes24','title':title,'author':'사토 케네츠',
          'metadata':{'author':'사토 케네츠'},'url':yes24_url}]
  class Gateway:
   def fetch_one(self,sql,_params=()):
    if 'FROM books' in sql:
     return {'id':1,'series_name':title,'library_id':2,
             'title':title+' 01권','file_path':'/books/01권.cbz'}
    return {'content_kind':'manga'}
   def get_setting(self,_key):
    return {'value':json.dumps({'reason':'author_conflict','query':title,
                                'candidates':held})}
  provider=object.__new__(m.RabbitPluginsMetadataProvider)
  provider.get_db_gateway=lambda *_:Gateway()
  provider.get_plugin_config=lambda *_:{'metadata_sources':'ridi,yes24'}
  provider._search_metadata=lambda *_args,**_kwargs:fresh
  applied=[]
  def apply(_gateway,_book_id,item_data,*_args,**_kwargs):
   applied.append(item_data)
   return True,'적용 완료'
  provider._apply_metadata=apply
  app=Flask(__name__);app.secret_key='test'
  app.config['TESTING']=True
  details={'author':'고리 란도우','cover_artist':'사토 켄에츠'}
  with app.test_request_context('/'):
   session['role']='admin'
   with patch.object(m,'_optional_column_sql',return_value='NULL'),\
        patch.object(m,'_remote_fetch_metadata',return_value=details) as fetch,\
        patch.object(m,'_yes24_public_metadata',return_value={}):
    result=provider.run_context_menu_action(
        'general','metadata_confirm_same_work',{'book_id':1})
  self.assertTrue(result['success'])
  fetch.assert_called_once_with(ridi_url,'ridi')
  self.assertEqual(applied[0]['metadata']['author'],'고리 란도우')
  self.assertEqual(applied[0]['metadata']['cover_artist'],'사토 켄에츠')

 def test_confirm_legacy_flattened_ridi_hold_accepts_role_split(self):
  title='[바닐라] 이세계 NTR ~절친의 여자를 최강 스킬로 함락시키는 방법~'
  ridi_url='https://ridibooks.com/books/806015466'
  yes24_url='https://www.yes24.com/product/goods/141731553'
  held=[{'source':'ridi','title':title,'author':'사토 켄에츠, 고리 란도우','url':ridi_url},
        {'source':'yes24','title':title,'author':'사토 케네츠','url':yes24_url}]
  fresh=[{'source':'ridi','title':title,'author':'사토 켄에츠, 고리 란도우',
          'metadata':{'author':'사토 켄에츠, 고리 란도우'},'url':ridi_url},
         {'source':'yes24','title':title,'author':'사토 케네츠',
          'metadata':{'author':'사토 케네츠'},'url':yes24_url}]
  class Gateway:
   def fetch_one(self,sql,_params=()):
    if 'FROM books' in sql:
     return {'id':1,'series_name':title,'library_id':2,
             'title':title+' 01권','file_path':'/books/01권.cbz'}
    return {'content_kind':'manga'}
   def get_setting(self,_key):
    return {'value':json.dumps({'reason':'author_conflict','query':title,
                                'candidates':held})}
  provider=object.__new__(m.RabbitPluginsMetadataProvider)
  provider.get_db_gateway=lambda *_:Gateway()
  provider.get_plugin_config=lambda *_:{'metadata_sources':'ridi,yes24'}
  provider._search_metadata=lambda *_args,**_kwargs:fresh
  applied=[]
  def apply(_gateway,_book_id,item_data,*_args,**_kwargs):
   applied.append(item_data)
   return True,'적용 완료'
  provider._apply_metadata=apply
  app=Flask(__name__);app.secret_key='test'
  app.config['TESTING']=True
  details={'author':'고리 란도우','cover_artist':'사토 켄에츠'}
  with app.test_request_context('/'):
   session['role']='admin'
   with patch.object(m,'_optional_column_sql',return_value='NULL'),\
        patch.object(m,'_remote_fetch_metadata',return_value=details) as fetch,\
        patch.object(m,'_yes24_public_metadata',return_value={}):
    result=provider.run_context_menu_action(
        'general','metadata_confirm_same_work',{'book_id':1})
  self.assertTrue(result['success'])
  fetch.assert_called_once_with(ridi_url,'ridi')
  self.assertEqual(applied[0]['metadata']['author'],'고리 란도우')
  self.assertEqual(applied[0]['metadata']['cover_artist'],'사토 켄에츠')

 def test_confirm_gamers_hold_accepts_three_flattened_ridi_credits(self):
  title='[코믹] 게이머즈!'
  ridi_url='https://ridibooks.com/books/845017181'
  yes24_url='https://www.yes24.com/product/goods/99422387'
  ridi_credits='타카하시 츠바사, 아오이 세키나, 사보텐'
  held=[{'source':'ridi','title':title,'author':ridi_credits,'url':ridi_url},
        {'source':'yes24','title':title,'author':'Tsubasa TAKAHASHI','url':yes24_url}]
  fresh=[{'source':'ridi','title':title,'author':ridi_credits,
          'metadata':{'author':ridi_credits},'url':ridi_url},
         {'source':'yes24','title':title,'author':'아오이 세키나',
          'metadata':{'author':'아오이 세키나'},'url':yes24_url}]
  class Gateway:
   def fetch_one(self,sql,_params=()):
    if 'FROM books' in sql:
     return {'id':1,'series_name':'게이머즈!','library_id':3,
             'title':'게이머즈! 01권','file_path':'/books/게이머즈! 01권.cbz'}
    return {'content_kind':'manga'}
   def get_setting(self,_key):
    return {'value':json.dumps({'reason':'author_conflict','query':'게이머즈!',
                                'candidates':held})}
  provider=object.__new__(m.RabbitPluginsMetadataProvider)
  provider.get_db_gateway=lambda *_:Gateway()
  provider.get_plugin_config=lambda *_:{'metadata_sources':'ridi,yes24'}
  provider._search_metadata=lambda *_args,**_kwargs:fresh
  received=[]
  merge_candidates=provider._merge_metadata_candidates
  def merge_and_capture(rows,kind,**kwargs):
   received.extend(rows)
   return merge_candidates(rows,kind,**kwargs)
  provider._merge_metadata_candidates=merge_and_capture
  applied=[]
  provider._apply_metadata=lambda _gateway,_book_id,item_data,*_args,**_kwargs: (
      applied.append(item_data) or (True,'적용 완료'))
  app=Flask(__name__);app.secret_key='test'
  app.config['TESTING']=True
  details={'author':'아오이 세키나',
           'cover_artist':'타카하시 츠바사, 사보텐'}
  with app.test_request_context('/'):
   session['role']='admin'
   with patch.object(m,'_optional_column_sql',return_value='NULL'),\
        patch.object(m,'_remote_fetch_metadata',return_value=details) as fetch,\
        patch.object(m,'_yes24_public_metadata',return_value={}):
    result=provider.run_context_menu_action(
        'general','metadata_confirm_same_work',{'book_id':1})
  self.assertTrue(result['success'], result)
  fetch.assert_called_once_with(ridi_url,'ridi')
  self.assertEqual(applied[0]['metadata']['author'],'아오이 세키나')
  self.assertEqual(applied[0]['metadata']['cover_artist'],
                   '타카하시 츠바사, 사보텐')
  # A refreshed search can return a different product after the hold was
  # created. Explicit confirmation must still apply the exact saved candidates.
  applied.clear()
  received.clear()
  fresh[1]['url']='https://www.yes24.com/product/goods/another'
  with app.test_request_context('/'):
   session['role']='admin'
   with patch.object(m,'_optional_column_sql',return_value='NULL'),\
        patch.object(m,'_remote_fetch_metadata',return_value=details),\
        patch.object(m,'_yes24_public_metadata',return_value={}):
    changed_search=provider.run_context_menu_action(
        'general','metadata_confirm_same_work',{'book_id':1})
  self.assertTrue(changed_search['success'], changed_search)
  self.assertEqual(applied[0]['metadata']['author'],'아오이 세키나')
  self.assertEqual(received[1]['url'],yes24_url)

 def test_settings_can_list_legacy_and_current_author_conflict_holds(self):
  candidates=[{'source':'ridi','title':'작품','author':'작가 A','url':'https://ridibooks.com/books/1'},
              {'source':'yes24','title':'작품','author':'작가 B','url':'https://yes24.com/1'}]
  legacy_series='기존 보류 작품'
  current_series='새 보류 작품'
  setting_patterns=[]
  class Gateway:
   def __init__(self, db_type): self.db_type=db_type
   def fetch_all(self, sql, _params=()):
    if 'FROM settings' in sql:
     setting_patterns.append(_params[0])
     if self.db_type=='general':
      return [{'key':m._metadata_hold_key(2,legacy_series), 'value':json.dumps({
       'reason':'author_conflict','query':legacy_series,'checked_at':'2026-09-30T10:00:00',
       'candidates':candidates})}]
     return [{'key':m._metadata_hold_key(5,current_series), 'value':json.dumps({
      'reason':'author_conflict','query':current_series,'checked_at':'2026-09-30T11:00:00',
      'book_id':51,'library_id':5,'series_name':current_series,'book_count':3,
      'candidates':candidates})}]
    if 'SELECT id, name FROM libraries' in sql:
     return [{'id':2,'name':'일반 테스트'},{'id':5,'name':'성인 테스트'}]
    if 'GROUP BY b.library_id, b.series_name' in sql:
     return [{'book_id':20,'library_id':2,'series_name':legacy_series,
              'book_count':2,'library_name':'일반 테스트'}]
    return []
  provider=object.__new__(m.RabbitPluginsMetadataProvider)
  provider.get_db_gateway=lambda db_type:Gateway(db_type)
  provider.get_plugin_config=lambda *_args:{}
  app=Flask(__name__);app.secret_key='test'
  with app.test_request_context('/'):
   session['role']='admin'
   result=provider.run_context_menu_action('general','metadata_list_holds',{})
  self.assertTrue(result['success'])
  self.assertEqual({(item['db_type'],item['series_name'],item['book_id']) for item in result['holds']},
                   {('general',legacy_series,20),('adult',current_series,51)})
  direct=next(item for item in result['holds'] if item['series_name']==current_series)
  self.assertEqual(direct['book_count'],3)
  self.assertEqual(direct['library_name'],'성인 테스트')
  self.assertEqual(setting_patterns,['rabbit_plugins:metadata!_hold:%']*2)

 def test_adult_uses_shared_conversion_settings(self):
  provider=object.__new__(m.RabbitPluginsMetadataProvider)
  def stored(_provider,db_type,default=None):
   return ({'metadata_publisher_map':'ワニマガジン社 => WANIMAGAZINE'}
           if db_type=='general' else {})
  with patch.object(m.BaseMetadataProvider,'get_plugin_config',stored):
   config=provider.get_plugin_config('adult',{})
  self.assertEqual(config['metadata_publisher_map'],'ワニマガジン社 => WANIMAGAZINE')

 def test_publisher_rule_updates_existing_value_without_overwrite(self):
  gateway=ApplyGateway();provider=object.__new__(m.RabbitPluginsMetadataProvider)
  config={'metadata_overwrite':False,'metadata_collect_cover':False,
          'metadata_publisher_map':'내부 출판사 => 변경 출판사'}
  item={'source':'munpia','metadata':{'publisher':'내부 출판사'}}
  with patch.object(m,'_optional_column_sql',return_value='NULL'):
   ok,_=provider._apply_metadata(gateway,1,item,config,fields=['publisher'])
  self.assertTrue(ok)
  updates=[params for query,params in gateway.updates if query.startswith('UPDATE books SET')]
  self.assertTrue(any('변경 출판사' in params for params in updates))

 def test_automatic_apply_preserves_existing_scalars_and_unions_tags_links(self):
  gateway=ApplyGateway();provider=object.__new__(m.RabbitPluginsMetadataProvider)
  config={'metadata_overwrite':False,'metadata_collect_cover':False}
  item={'source':'merged','metadata':{
   'author':'외부 작가','isbn':'외부 ISBN','publisher':'외부 출판사',
   'summary':'외부 소개','genre':'외부 장르','tags':'신규 태그, 공통 태그',
   'link':'https://ridibooks.com/books/1, https://www.yes24.com/product/goods/1',
  }}
  with patch.object(m,'_optional_column_sql',return_value='NULL'):
   ok,_=provider._apply_metadata(
    gateway,1,item,config,
    fields=['author','isbn','publisher','summary','genre','tags','link'])
  self.assertTrue(ok)
  updates=[(sql,params) for sql,params in gateway.updates
           if sql.startswith('UPDATE books SET')]
  self.assertEqual(len(updates),1)
  sql,params=updates[0]
  self.assertIn('tags = ?',sql)
  self.assertIn('link = ?',sql)
  self.assertEqual(params[0],'내부 태그, 신규 태그, 공통 태그')
  self.assertEqual(set(params[1].split(', ')),{
   'https://ridibooks.com/books/1',
   'https://www.yes24.com/product/goods/1',
  })
  self.assertEqual(params[-1],1)
  for existing in ('내부 작가','내부 ISBN','내부 출판사','내부 소개','내부 장르'):
   self.assertNotIn(existing,params)

 def test_ridi_adult_genre_survives_same_work_manual_overwrite(self):
  provider=object.__new__(m.RabbitPluginsMetadataProvider)
  page=('<script>var bookDetail = {"genre_name":"만화 e북",'
        '"category_name":"해외 순정","age_limit":"19",'
        '"is_adult_only":true};</script>')
  candidate={'source':'ridi','title':'멘헤라 집착남인 카이 군은 나를 절대 놓아주지 않아',
             'metadata':{'genre':m._join_terms(m._ridi_genres(page))}}
  merged=provider._merge_metadata_candidates([candidate],'manga')
  self.assertEqual(merged['metadata']['genre'],'성인, 만화 e북, 해외 순정')

  class Gateway(ApplyGateway):
   def fetch_one(self,query,params=()):
    row=super().fetch_one(query,params)
    if row and 'FROM books b WHERE b.id' in query:
     row['genre']='해외 순정, 만화 e북'
    return row
  gateway=Gateway()
  config={'metadata_manual_overwrite':True,'metadata_collect_cover':False}
  with patch.object(m,'_optional_column_sql',return_value='NULL'):
   ok,_=provider._apply_metadata(gateway,1,merged,config,fields=['genre'],
                                  manual=True,preserve_existing=True)
  self.assertTrue(ok)
  genre_update=next(params for sql,params in gateway.updates
                    if sql.startswith('UPDATE books SET') and 'genre = ?' in sql)
  self.assertEqual(genre_update[0],'성인, 해외 순정, 만화 e북')

 def test_same_work_preserves_comicinfo_values_and_clears_placeholder_isbn(self):
  class Gateway(ApplyGateway):
   def fetch_one(self,query,params=()):
    row=super().fetch_one(query,params)
    if row and 'FROM books b WHERE b.id' in query:
     row.update({'author':'네코마타미사, 테바','cover_artist':'테바, 네코마타미사',
                 'publisher':'넥스큐브','summary':'ComicInfo 소개',
                 'genre':'해외 순정, 만화 e북','tags':'현대배경, 일상',
                 'isbn':'0000000000000','link':'https://mangabaka.org/605781'})
    return row
  gateway=Gateway();provider=object.__new__(m.RabbitPluginsMetadataProvider)
  item={'source':'merged','metadata':{
   'author':'테바, 네코마타미사','cover_artist':'테바, 네코마타미사',
   'publisher':'외부 출판사','summary':'외부 소개',
   'genre':'성인, 해외 순정, 만화 e북','tags':'현대배경, 일상, 고화질',
   'isbn':'0000000000000',
   'link':'https://ridibooks.com/books/840081557, https://www.yes24.com/product/goods/196897931'}}
  config={'metadata_manual_overwrite':True,'metadata_overwrite':True,
          'metadata_collect_cover':False}
  def optional_column(_gateway,table,_alias,column):
   return 'b.cover_artist' if table=='books' and column=='cover_artist' else 'NULL'
  with patch.object(m,'_optional_column_sql',side_effect=optional_column):
   ok,_=provider._apply_metadata(gateway,1,item,config,
    fields=['author','cover_artist','publisher','summary','genre','tags','isbn','link'],
    manual=True,preserve_existing=True)
  self.assertTrue(ok)
  updates=[params for sql,params in gateway.updates if sql.startswith('UPDATE books SET')]
  flattened=[value for params in updates for value in params]
  self.assertIn('',flattened)
  self.assertIn('성인, 해외 순정, 만화 e북',flattened)
  self.assertIn('현대배경, 일상, 고화질',flattened)
  self.assertTrue(any(isinstance(value,str) and 'mangabaka.org/605781' in value
                      and 'ridibooks.com/books/840081557' in value
                      and 'yes24.com/product/goods/196897931' in value
                      for value in flattened))
  for preserved in ('테바, 네코마타미사','외부 출판사','외부 소개'):
   self.assertNotIn(preserved,flattened)

 def test_apply_clears_placeholder_isbn_and_retries_it_as_missing(self):
  class Gateway(ApplyGateway):
   def fetch_one(self,query,params=()):
    row=super().fetch_one(query,params)
    if row and 'FROM books b WHERE b.id' in query:
     row['isbn']='0000000000000'
    return row
  gateway=Gateway();provider=object.__new__(m.RabbitPluginsMetadataProvider)
  with patch.object(m,'_optional_column_sql',return_value='NULL'):
   ok,_=provider._apply_metadata(
    gateway,1,{'source':'merged','metadata':{}},
    {'metadata_collect_cover':False},fields=['genre'])
  self.assertTrue(ok)
  update=next((params for sql,params in gateway.updates
               if sql.startswith('UPDATE books SET') and 'isbn = ?' in sql),None)
  self.assertIsNotNone(update)
  self.assertIn('',update)
  missing=m._metadata_missing_condition(['isbn'])
  self.assertIn("IN ('0000000000', '0000000000000')",missing)

 def test_apply_stores_uci_even_when_it_is_not_an_editable_metadata_field(self):
  class Gateway(ApplyGateway):
   def __init__(self):
    super().__init__();self.settings={}
   def get_setting(self,key,default=None):
    return {'value':self.settings[key]} if key in self.settings else default
   def set_setting(self,key,value):
    self.settings[key]=value
  gateway=Gateway();provider=object.__new__(m.RabbitPluginsMetadataProvider)
  item={'source':'ridi','metadata':{'uci':'G704-001234.2026.1.007'}}
  with patch.object(m,'_optional_column_sql',return_value='NULL'):
   ok,_=provider._apply_metadata(
    gateway,1,item,{'metadata_collect_cover':False},fields=['author'])
  self.assertTrue(ok)
  self.assertEqual(m._read_metadata_uci(gateway,1),'G704-001234.2026.1.007')

 def test_saved_comicinfo_age_rating_survives_provider_overwrite(self):
  class Gateway(ApplyGateway):
   def fetch_one(self,query,params=()):
    if 'SELECT books_lv FROM books' in query:return {'books_lv':'R18+'}
    return super().fetch_one(query,params)
  gateway=Gateway();provider=object.__new__(m.RabbitPluginsMetadataProvider)
  config={'metadata_overwrite':True,'metadata_collect_cover':False}
  item={'source':'ridi','metadata':{'books_lv':'adult only'}}
  def optional_column(_gateway,table,alias,column):
   return f'{alias}.{column}' if table=='books' and column=='books_lv' else 'NULL'
  with patch.object(m,'_optional_column_sql',side_effect=optional_column):
   ok,_=provider._apply_metadata(gateway,1,item,config,fields=['books_lv'])
  self.assertTrue(ok)
  self.assertFalse(any('adult only' in params for query,params in gateway.updates))

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

 def test_korean_and_romanized_creator_names_match(self):
  title='멘헤라 집착남인 카이 군은 나를 절대 놓아주지 않아'
  self.assertTrue(m._metadata_author_matches(
   '테바, 네코마타미사','Teba, Nekomata Misa'))
  self.assertTrue(m._metadata_author_matches('사카이 마유','SAKAI Mayu'))
  rows=[
   {'source':'ridi','title':title,'author':'테바, 네코마타미사'},
   {'source':'yes24','title':title,'author':'Teba, Nekomata Misa'},
  ]
  diagnostics=[]
  selected=m.RabbitPluginsMetadataProvider._select_auto_candidates(
   rows,title,'manga','single',['ridi','yes24'],diagnostics=diagnostics)
  self.assertEqual([item['source'] for item in selected],['ridi','yes24'])
  self.assertEqual(diagnostics,[])
 def test_author_conflict_exposes_candidates_for_manual_review(self):
  diagnostics=[]
  with patch.object(m,'_remote_fetch_metadata',return_value={'author':'신이난'}):
   selected=m.RabbitPluginsMetadataProvider._select_auto_candidates(
    self.rows(),'작품','novel','',['ridi','naver','munpia'],diagnostics=diagnostics)
  self.assertEqual(selected,[])
  self.assertEqual(diagnostics[0]['reason'],'author_conflict')
  self.assertEqual({row['author'] for row in diagnostics[0]['candidates']},
                   {'신이난','야참타임'})

 def test_ridi_author_conflict_candidate_uses_writer_role_only(self):
  title='[바닐라] 이세계 NTR ~절친의 여자를 최강 스킬로 함락시키는 방법~'
  rows=[
   {'source':'ridi','title':title,'author':'사토 켄에츠, 고리 란도우',
    'url':'https://ridibooks.com/books/806015466',
    'metadata':{'author':'사토 켄에츠, 고리 란도우'}},
   {'source':'yes24','title':title,'author':'사토 케네츠',
    'url':'https://www.yes24.com/product/goods/141731553','_metadata_fetched':True,
    'metadata':{'author':'사토 케네츠'}},
  ]
  diagnostics=[]
  with patch.object(m,'_remote_fetch_metadata',return_value={
      'author':'고리 란도우','cover_artist':'사토 켄에츠'}):
   selected=m.RabbitPluginsMetadataProvider._select_auto_candidates(
    rows,title,'manga','single',['ridi','yes24'],diagnostics=diagnostics)
  self.assertEqual(selected,[])
  self.assertEqual(diagnostics[0]['candidates'][0]['source'],'ridi')
  self.assertEqual(diagnostics[0]['candidates'][0]['author'],'고리 란도우')
  self.assertEqual({row['author'] for row in diagnostics[0]['candidates']},
                   {'고리 란도우','사토 케네츠'})
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
 def test_auto_collect_uses_scanned_author_without_reopening_cbz(self):
  class Gateway:
   def fetch_all(self,query,params=()):
    if 'SELECT b.id, b.series_name' in query:
     self.query=query
     return [{'id':9,'series_name':'작품','title':'작품 01권',
      'title_alias':'','file_path':'/books/작품 01권.cbz','file_format':'cbz',
      'file_mtime':'1','file_size':10,'link':'','library_id':7,'author':'내장 작가'}]
    return []
   def fetch_one(self,query,params=()):
    if 'FROM libraries' in query:return {'content_kind':'novel'}
    return {}
   def get_setting(self,key,default=None):return default
   def set_setting(self,key,value):pass
  gateway=Gateway();provider=object.__new__(m.RabbitPluginsMetadataProvider)
  provider.get_plugin_config=lambda *_:{'metadata_sources':'ridi',
   'metadata_fields':'author,summary'}
  provider.get_db_gateway=lambda *_:gateway
  provider._refresh_newly_completed_series=lambda *_args,**_kwargs:(set(),0)
  provider._search_metadata=lambda *_args,**_kwargs:[]
  progress=[]
  with patch.object(m,'_optional_column_sql',return_value='NULL'),\
       patch.object(m,'_comicinfo_metadata',side_effect=AssertionError('CBZ reopened')):
   result=provider._auto_collect('general',{'library_id':7,
    '_rabbit_progress_callback':lambda event,**details:progress.append((event,details))})
  self.assertEqual(result['rows'],1)
  self.assertIn('b.author, l.content_kind AS _content_kind, NULL AS localized_series, CASE WHEN',gateway.query)
  self.assertEqual(progress[0][0],'started')
  self.assertTrue(any(event=='progress' and details.get('completed')==1
                      for event,details in progress))

 def test_manual_blank_isbn_does_not_retrigger_hold_with_overwrite_enabled(self):
  title='멘헤라 집착남인 카이 군은 나를 절대 놓아주지 않아'
  class Gateway:
   def __init__(self):
    self.settings={m._series_dates_key(7,title):json.dumps(
     {'manual_empty_fields':['isbn']},ensure_ascii=False)}
   def fetch_all(self,query,params=()):
    if 'SELECT b.id, b.series_name' in query:
     self.query=query
     return [{'id':45246,'series_name':title,'title':title+' 01권',
      'title_alias':'','file_path':'/books/01권.cbz','file_format':'cbz',
      'file_mtime':'1','file_size':10,'link':'','library_id':7,'author':'테바, 네코마타미사',
      '_content_kind':'manga','_metadata_missing':1,
      '_metadata_missing_without_isbn':0}]
    return []
   def fetch_one(self,query,params=()):return {}
   def get_setting(self,key,default=None):
    return {'value':self.settings[key]} if key in self.settings else default
   def set_setting(self,*_args):pass
  gateway=Gateway();provider=object.__new__(m.RabbitPluginsMetadataProvider)
  provider.get_plugin_config=lambda *_:{'metadata_sources':'ridi,yes24',
   'metadata_fields':'author,summary,genre,isbn','metadata_overwrite':True,
   'metadata_overwrite_kinds':'manga'}
  provider.get_db_gateway=lambda *_:gateway
  provider._refresh_newly_completed_series=lambda *_args,**_kwargs:(set(),0)
  provider._search_metadata=Mock(side_effect=AssertionError('manual ISBN clear retried search'))
  with patch.object(m,'_optional_column_sql',return_value='NULL'):
   result=provider._auto_collect('general',{'library_id':7})
  self.assertEqual(result['rows'],0)
  self.assertEqual(result['processed'],0)
  provider._search_metadata.assert_not_called()
  self.assertIn('_metadata_missing_without_isbn',gateway.query)

 def test_empty_auto_collect_clears_scan_activity(self):
  provider=object.__new__(m.RabbitPluginsMetadataProvider)
  provider.get_plugin_config=lambda *_:{'metadata_sources':'ridi'}
  provider.get_db_gateway=lambda *_:CaptureGateway()
  provider._refresh_newly_completed_series=lambda *_args,**_kwargs:(set(),0)
  progress=[]
  with patch.object(m,'_optional_column_sql',return_value='NULL'):
   result=provider._auto_collect('general',{'library_id':7,
    '_rabbit_progress_callback':lambda event,**details:progress.append((event,details))})
  self.assertEqual(result['processed'],0)
  self.assertEqual([event for event,_ in progress],['started','progress','clear'])

 def test_batch_scan_auto_collect_is_limited_to_scanned_book_ids(self):
  gateway=CaptureGateway();provider=object.__new__(m.RabbitPluginsMetadataProvider)
  provider.get_plugin_config=lambda *_:{'metadata_sources':'ridi'}
  provider.get_db_gateway=lambda *_:gateway
  provider._refresh_newly_completed_series=lambda *_args,**_kwargs:(set(),0)
  provider._auto_collect('general',{'library_id':19,'book_ids':[28677,28678,28677]})
  self.assertIn('AND b.id IN (?,?)',gateway.query)
  self.assertEqual(gateway.params[-2:],(28677,28678))

 def test_blank_publication_start_date_triggers_scoped_auto_collect(self):
  class Gateway(CaptureGateway):
   def __init__(self,start_date,metadata_missing=0):
    super().__init__();self.start_date=start_date;self.metadata_missing=metadata_missing
   def fetch_all(self,query,params=()):
    if query.startswith('SELECT b.id'):
     self.query=query;self.params=params
    return [{'id':28677,'series_name':'작품','title':'작품 01권','title_alias':'',
     'file_path':'/books/작품 01권.epub','file_format':'epub','file_mtime':1,
     'file_size':10,'link':'','library_id':19,'author':'작가',
     '_metadata_missing':self.metadata_missing}]
   def fetch_one(self,query,params=()):
    if 'FROM libraries' in query:return {'content_kind':'novel'}
    return {}
   def get_setting(self,key,default=None):
    return {'value':json.dumps({'start':self.start_date})}
   def set_setting(self,*_):pass
  for start_date,metadata_missing,expected_searches in (
   ('',0,1),('2026-08-07',0,0),('2026-08-07',1,1)):
   with self.subTest(start_date=start_date,metadata_missing=metadata_missing):
    gateway=Gateway(start_date,metadata_missing)
    provider=object.__new__(m.RabbitPluginsMetadataProvider)
    provider.get_plugin_config=lambda *_:{
     'metadata_sources':'ridi','metadata_fields':['publication_start_date'],
     'metadata_collect_cover':False}
    provider.get_db_gateway=lambda *_:gateway
    provider._refresh_newly_completed_series=lambda *_args,**_kwargs:(set(),0)
    search=Mock(return_value=[])
    provider._search_metadata=search
    provider._select_auto_candidates=lambda *_args,**_kwargs:[]
    provider._auto_collect('general',{'library_id':19,'book_ids':[28677]})
    self.assertIn('COALESCE(isbn, "") = ""',gateway.query)
    self.assertEqual(search.call_count,expected_searches)

 def test_targeted_auto_collect_does_not_reconcile_every_library_link(self):
  provider=object.__new__(m.RabbitPluginsMetadataProvider)
  provider.get_plugin_config=lambda *_:{'metadata_auto_enabled':True}
  provider._auto_collect=lambda *_args,**_kwargs:{
   'rows':1,'processed':1,'matched':1,'updated':1}
  with patch.object(m,'_reconcile_external_links') as reconcile:
   result=provider._queue_auto_collect('general',{
    'library_id':19,'book_ids':[28677], '_rabbit_allow_overwrite':True})
  self.assertTrue(result['success'])
  reconcile.assert_not_called()

 def test_new_book_hook_defers_to_single_scan_completion_collection(self):
  provider=object.__new__(m.RabbitPluginsMetadataProvider)
  provider._queue_auto_collect=lambda *_args,**_kwargs:(_ for _ in ()).throw(
   AssertionError('new-book hook must not duplicate the completion collection'))
  result=provider.on_scan_new_books_detected('general',{'library_id':19})
  self.assertTrue(result['skipped'])
 def test_metadata_date_assignment_does_not_open_cbz(self):
  gateway=ApplyGateway();provider=object.__new__(m.RabbitPluginsMetadataProvider)
  item={'source':'yes24','metadata':{
   'publication_start_date':'2024-01-01','publication_end_date':'2025-01-01'}}
  with patch.object(m,'_optional_column_sql',return_value='NULL'),\
       patch.object(m,'_comicinfo_metadata',side_effect=AssertionError('CBZ reopened')):
   applied,_=provider._apply_metadata(gateway,1,item,{'metadata_collect_cover':False},
    fields=['publication_start_date','publication_end_date'])
  self.assertTrue(applied)
  self.assertTrue(any('release_date' in query for query,_ in gateway.updates))

 def test_series_cover_does_not_replace_numbered_volume_covers(self):
  rows=[
   {'id':1,'library_id':1,'series_name':'마음속이 보이는 왕자와 마음속은 새까만 영애',
    'title':'마음속이 보이는 왕자와 마음속은 새까만 영애 01권',
    'title_alias':'','file_path':'/books/마음속이 보이는 왕자와 마음속은 새까만 영애 01권.cbz',
    'cover_image':'1/internal-01.webp','metadata_locked':0,'link':'','volume_index':1},
   {'id':2,'library_id':1,'series_name':'마음속이 보이는 왕자와 마음속은 새까만 영애',
    'title':'마음속이 보이는 왕자와 마음속은 새까만 영애 02권',
    'title_alias':'','file_path':'/books/마음속이 보이는 왕자와 마음속은 새까만 영애 02권.cbz',
    'cover_image':'1/internal-02.webp','metadata_locked':0,'link':'','volume_index':2},
  ]
  class Gateway(ApplyGateway):
   def fetch_one(self,query,params=()):
    if 'FROM libraries' in query:return {'content_kind':'manga'}
    if 'FROM books b WHERE b.id' in query:return dict(rows[0])
    return {}
   def fetch_all(self,query,params=()):
    if query.startswith('SELECT id, title, title_alias'):return [dict(row) for row in rows]
    return []
  provider=object.__new__(m.RabbitPluginsMetadataProvider)
  config={'metadata_overwrite':True,'metadata_overwrite_kinds':'manga',
   'metadata_collect_cover':True,'metadata_cover_kinds':'manga',
   'metadata_cover_overwrite':True,'metadata_cover_overwrite_kinds':'manga'}
  representative={'source':'merged','cover_source':'series_db',
   'metadata':{'cover':'https://images.example/volume-01-representative.webp'}}
  gateway=Gateway()
  with patch.object(m,'_optional_column_sql',return_value='NULL'),\
       patch('tools.scanner.cover.download_cover_from_url') as download,\
       patch.object(m,'_remember_external_cover'):
   ok,_=provider._apply_metadata(gateway,1,representative,config,fields=['cover'])
  self.assertTrue(ok)
  download.assert_not_called()
  self.assertFalse(any('cover_image' in query for query,_ in gateway.updates))

  exact_maps={'1':'https://img.ridi/volume-01','2':'https://img.ridi/volume-02'}
  per_volume={'source':'merged','cover_source':'ridi',
   'metadata':{'cover':'https://images.example/volume-01-representative.webp'},
   'cover_by_volume':exact_maps,
   'cover_maps_by_source':{'ridi':{'cover_by_volume':exact_maps,'cover_by_title':{}}}}
  gateway=Gateway()
  with patch.object(m,'_optional_column_sql',return_value='NULL'),\
       patch('tools.scanner.cover.download_cover_from_url') as download,\
       patch.object(m,'_remember_external_cover'):
   download.side_effect=['1/external-01.webp','1/external-02.webp']
   ok,_=provider._apply_metadata(gateway,1,per_volume,config,fields=['cover'])
  self.assertTrue(ok)
  requested=[call.args[1] for call in download.call_args_list]
  self.assertEqual(requested,['https://img.ridi/volume-01','https://img.ridi/volume-02'])
  cover_writes=[params[0] for query,params in gateway.updates if 'SET cover_image' in query]
  self.assertEqual(cover_writes,['1/external-01.webp','1/external-02.webp'])

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
  self.assertIn('내부 태그, 외부 태그',update)
  self.assertNotIn('내부 장르',update)

 def test_automatic_overwrite_merges_provider_tags_into_each_volume(self):
  class Gateway(ApplyGateway):
   def fetch_one(self,query,params=()):
    if 'FROM libraries' in query:return {'content_kind':'manga'}
    if 'FROM books b WHERE b.id' in query:
     book_id=params[0]
     tags='내부 태그' if book_id==1 else '다른 권 로컬 태그'
     return {'id':book_id,'series_name':'작품','library_id':4,
      'file_path':f'/books/작품 {book_id}권.cbz','cover_image':'',
      'metadata_locked':0,'author':'','isbn':'','publisher':'','summary':'',
      'link':'','genre':'','tags':tags,'release_date':'','title_alias':'',
      'localized_series':'','cover_artist':''}
    return {}
   def fetch_all(self,query,params=()):
    if query.startswith('SELECT id, title, title_alias'):
     return [{'id':1,'title':'작품 1권','title_alias':'','file_path':'/books/작품 1권.cbz',
      'cover_image':'','metadata_locked':0,'link':'','volume_index':1},
      {'id':2,'title':'작품 2권','title_alias':'','file_path':'/books/작품 2권.cbz',
      'cover_image':'','metadata_locked':0,'link':'','volume_index':2}]
    return []
  gateway=Gateway();provider=object.__new__(m.RabbitPluginsMetadataProvider)
  item={'source':'ridi','metadata':{'tags':'공급처 태그'}}
  with patch.object(m,'_optional_column_sql',return_value='NULL'):
   ok,_=provider._apply_metadata(gateway,1,item,
    {'metadata_overwrite':True,'metadata_collect_cover':False},fields=['tags'])
  self.assertTrue(ok)
  tag_updates=[params for query,params in gateway.updates
   if query.startswith('UPDATE books SET') and 'tags' in query]
  self.assertEqual(len(tag_updates),2)
  self.assertIn('내부 태그, 공급처 태그',tag_updates[0])
  self.assertIn('다른 권 로컬 태그, 공급처 태그',tag_updates[1])
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

 def test_series_db_search_falls_back_to_original_title_without_korean_alias(self):
  korean='한글 대체 제목이 없는 작품'
  original='死刑宣告された賢女が嫌われ王子に溺愛されて、幸せになるまでの物語'
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/'series.sqlite'
   with sqlite3.connect(path) as db:
    db.execute('CREATE TABLE series (id INTEGER, title TEXT, native_title TEXT, '
     'secondary_titles_ko TEXT, titles TEXT, type TEXT, links TEXT, authors TEXT, '
     'artists TEXT, cover_raw_url TEXT, status TEXT, final_volume INTEGER, content_rating TEXT)')
    db.execute('INSERT INTO series VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',(
     132249,'English catalogue title',original,'[]','[]','novel',
     json.dumps(['https://mangabaka.org/132249']),json.dumps(['En Kito']),
     '[]','',None,None,None))
   provider=object.__new__(m.RabbitPluginsMetadataProvider)
   provider._series_db_path=lambda:path
   with patch.object(m,'_metadata_source_order',return_value=['series_db']):
    results=provider._search_metadata(korean,{'metadata_sources':'series_db'},
     'general','novel',manual=True,original_title=original)
   self.assertEqual([item['id'] for item in results],['series_db:132249'])
   selected=provider._select_auto_candidates(results,korean,'novel','',
     ['series_db'],original_title=original)
   self.assertEqual([item['id'] for item in selected],['series_db:132249'])
   self.assertEqual(results[0]['metadata']['localized_series'],original)

 def test_series_db_title_lookup_ignores_korean_alias_spacing(self):
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/'series.sqlite'
   with sqlite3.connect(path) as db:
    db.execute('CREATE TABLE series (id INTEGER, title TEXT, native_title TEXT, '
     'secondary_titles_ko TEXT, titles TEXT, type TEXT, links TEXT, authors TEXT, '
     'artists TEXT, cover_raw_url TEXT, status TEXT, final_volume INTEGER, content_rating TEXT)')
    db.execute('INSERT INTO series VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',(
     132250,'아이를 가질 때 까지 내게 안겨라',
     '死刑宣告された賢女が嫌われ王子に溺愛されて、幸せになるまでの物語',
     '[]','[]','novel','[]','[]','[]','',None,None,None))
   provider=object.__new__(m.RabbitPluginsMetadataProvider)
   provider._series_db_path=lambda:path
   result=provider._series_db_search('아이를 가질 때까지 내게 안겨라','novel')
   self.assertEqual([item['id'] for item in result],['series_db:132250'])

 def test_scan_restore_keeps_volume_ridi_and_recovers_other_sources(self):
  class LinkGateway:
   def __init__(self):
    self.settings={};self.updates=[]
   def get_setting(self,key,default=None): return self.settings.get(key,default)
   def set_setting(self,key,value): self.settings[key]=value
   def fetch_all(self,query,params=()):
    self.assert_query=query
    if 'FROM settings' in query:
     return [{'key':key,'value':value} for key,value in self.settings.items()
             if key.startswith(m._EXTERNAL_LINK_PREFIX)]
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
    if 'b.publication_status, b.tags, b.metadata_locked' in query:
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
