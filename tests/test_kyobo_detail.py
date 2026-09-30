import unittest
from .. import rabbit_plugins as m
class KyoboTests(unittest.TestCase):
 def test_ebook_product_fields_and_full_description(self):
  page='''<input value="작품" id="cmdtHnglName"><input id="autrNm" value="작가"><input id="pbcmName" value="출판사"><input id="stdBksIdnfNum" value="9791199014602"><input id="elbkPublDate" value="20241125"><div id="bookIntc"><div><div class="info_text">첫 문단<br/>마지막 문단</div></div></div><div>추천 상품은 제외</div>'''
  r=m._kyobo_detail_metadata(page)
  self.assertEqual(r['author'],'작가')
  self.assertEqual(r['isbn'],'9791199014602')
  self.assertEqual(r['release_date'],'2024-11-25')
  self.assertEqual(r['summary'],'첫 문단\n마지막 문단')
 def test_missing_fields_do_not_invent_metadata(self):
  self.assertEqual(m._kyobo_detail_metadata('<meta name="description" content="eBook 작품 | 소개...">'),{})
 def test_ebook_introduction_ignores_expand_button(self):
  page='''<div id="bookIntc"><div class="auto_overflow_contents">
   <p>설명을 펼치기 전에 읽을 본문</p>
   <button type="button" class="btn_more_body"><span>펼치기</span></button>
   </div></div>'''
  self.assertEqual(m._kyobo_detail_metadata(page)['summary'],
                   '설명을 펼치기 전에 읽을 본문')
 def test_genre_from_product_categories(self):
  r=m._kyobo_detail_metadata('<input id="largeCtgrName" value="IT/프로그래밍"><input id="middleCtgrName" value="컴퓨터공학">')
  self.assertEqual(r['genre'],'IT/프로그래밍, 컴퓨터공학')
 def test_edit_release_date_save_clear_and_validate(self):
  from flask import Flask,session
  from unittest.mock import patch
  class Gateway:
   def __init__(self):self.writes=[]
   def fetch_all(self,*a):return [{'id':1,'library_id':1,'file_path':'book.txt'}]
   def execute(self,sql,args):self.writes.append((sql,args))
   def get_setting(self,*a):return {'value':'{}'}
   def set_setting(self,*a):pass
  p=object.__new__(m.RabbitPluginsMetadataProvider);g=Gateway();p.get_db_gateway=lambda *a:g
  app=Flask('date-test');app.secret_key='fixture'
  with app.test_request_context('/'),patch.object(m,'_optional_column_sql',return_value='NULL'),patch.object(m,'_comicinfo_metadata',return_value={'artist':'','count':0,'volume':0}):
   session['role']='admin'
   for value,expected in [('2024-11-25','2024-11-25'),('',None)]:
    r=p.run_context_menu_action('general','save_detail_metadata',{'series_name':'작품','release_date':value})
    self.assertTrue(r['success']);self.assertEqual(g.writes[-1][1],(expected,'작품'))
   count=len(g.writes)
   r=p.run_context_menu_action('general','save_detail_metadata',{'series_name':'작품','release_date':'2024-02-30'})
   self.assertFalse(r['success']);self.assertEqual(len(g.writes),count)

 def test_manual_blank_fields_survive_embedded_metadata(self):
  from flask import Flask,session
  from unittest.mock import patch
  import json
  class Gateway:
   def __init__(self):self.settings={};self.writes=[]
   def fetch_all(self,*a):return [{'id':1,'library_id':1,'file_path':'book.epub'}]
   def execute(self,sql,args):self.writes.append((sql,args))
   def get_setting(self,key,default=None):return {'value':self.settings.get(key,'{}')}
   def set_setting(self,key,value):self.settings[key]=value
  p=object.__new__(m.RabbitPluginsMetadataProvider);g=Gateway();p.get_db_gateway=lambda *a:g
  app=Flask('blank-edit-test');app.secret_key='fixture'
  with app.test_request_context('/'),patch.object(m,'_optional_column_sql',return_value='b.cover_artist'),patch.object(m,'_comicinfo_metadata',return_value={'artist':'내부 그림작가','count':0,'volume':0}):
   session['role']='admin'
   result=p.run_context_menu_action('adult','save_detail_metadata',{
    'series_name':'작품','cover_artist':'','release_date':'',
    'manual_fields':{'cover_artist':'','release_date':'','genre':'','summary':'',
                     'teams':'새 그룹','locations':'','characters':''}})
   self.assertTrue(result['success'])
   self.assertEqual(result['manual_empty_fields'],['cover_artist','genre','release_date','summary'])
   self.assertIn(('UPDATE books SET teams = ? WHERE series_name = ? AND COALESCE(is_deleted, 0) = 0',('새 그룹','작품')),g.writes)
   self.assertEqual(json.loads(g.settings[m._series_dates_key(1,'작품')])['manual_empty_fields'],result['manual_empty_fields'])
   result=p.run_context_menu_action('adult','save_detail_metadata',{
    'series_name':'작품','cover_artist':'새 그림작가','release_date':'2024-01-02',
    'manual_fields':{'cover_artist':'새 그림작가','release_date':'2024-01-02','genre':'판타지','summary':'소개'}})
   self.assertTrue(result['success'])
   self.assertEqual(result['manual_empty_fields'],[])

 def test_detail_metadata_editor_saves_and_clears_translator_for_series(self):
  from flask import Flask,session
  from unittest.mock import patch
  import json
  class Gateway:
   def __init__(self):self.settings={};self.writes=[]
   def fetch_all(self,*a):return [{'id':1,'library_id':17,'file_path':'book.cbz'}]
   def execute(self,sql,args):self.writes.append((sql,args))
   def get_setting(self,key,default=None):return {'value':self.settings.get(key,'{}')}
   def set_setting(self,key,value):self.settings[key]=value
  p=object.__new__(m.RabbitPluginsMetadataProvider);g=Gateway();p.get_db_gateway=lambda *a:g
  app=Flask('translator-edit-test');app.secret_key='fixture'
  def column_sql(_gateway,_table,alias,column):
   return f'{alias}.{column}' if column in {'cover_artist','translator'} else 'NULL'
  with app.test_request_context('/'),patch.object(m,'_optional_column_sql',side_effect=column_sql),patch.object(m,'_comicinfo_metadata',return_value={'artist':'그림작가','count':0,'volume':0}):
   session['role']='admin'
   for value in ('박경용',''):
    result=p.run_context_menu_action('general','save_detail_metadata',{
     'series_name':'작품','cover_artist':'그림작가',
     'manual_fields':{'cover_artist':'그림작가','translator':value}})
    self.assertTrue(result['success'])
    self.assertTrue(result['translator_saved'])
    self.assertIn(('UPDATE books SET translator = ? WHERE series_name = ? AND COALESCE(is_deleted, 0) = 0',
                   (value,'작품')),g.writes)
   self.assertIn('translator',json.loads(g.settings[m._series_dates_key(17,'작품')])['manual_empty_fields'])
   writes_before=len(g.writes)
   result=p.run_context_menu_action('general','save_detail_metadata',{
    'series_name':'작품','manual_fields':{'translator':'x'*501}})
   self.assertFalse(result['success'])
   self.assertEqual(len(g.writes),writes_before)
