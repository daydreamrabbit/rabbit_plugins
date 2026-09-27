import unittest
from unittest.mock import patch
from flask import Flask,session
from .. import rabbit_plugins as m
class WidgetTests(unittest.TestCase):
 def test_adult_has_separate_selection_and_access_check(self):
  p=object.__new__(m.RabbitPluginsMetadataProvider)
  p.get_plugin_config=lambda *a:{'home_library_sections':[{'library_id':99}], 'home_adult_library_sections':[{'library_id':1,'limit':1}]}
  class Gateway:
   def fetch_all(self,*a):return [dict(id=7,library_id=1,series_name='adult series',title='adult title')]
  used=[]
  p.get_db_gateway=lambda kind:(used.append(kind) or Gateway())
  app=Flask('adult-widget-test');app.secret_key='fixture'
  with app.test_request_context('/'),patch('services.category_service.CategoryService.get_libraries',return_value=[{'id':1,'name':'adult library'}]) as libraries,patch.object(m,'_load_core_dependencies',return_value=({'check_book_rating_permission':lambda kind,bid:kind=='adult'},None)),patch('services.book_service.get_cover_image_with_t',return_value='cover'):
   session.update(user_id=1,role='user',has_adult_access=0)
   self.assertFalse(p._home_recently_added('adult')['success'])
   session['has_adult_access']=1
   result=p._home_recently_added('adult')
   self.assertTrue(result['success']);self.assertEqual(used,['adult'])
   self.assertEqual(libraries.call_args.args[0],'adult')
   self.assertEqual([x['id'] for x in result['items'] if 'id' in x],[7])
   self.assertTrue(all(x['db_type']=='adult' for x in result['items']))
 def test_rating_filtered_before_series_grouping_even_for_admin(self):
  p=object.__new__(m.RabbitPluginsMetadataProvider)
  p.get_plugin_config=lambda *a:{'home_library_sections':[{'library_id':1,'limit':1}]}
  class Gateway:
   def fetch_all(self,*a):return [dict(id=2,library_id=1,series_name='same',title='restricted',cover_image='secret.jpg'),dict(id=1,library_id=1,series_name='same',title='allowed')]
  p.get_db_gateway=lambda *a:Gateway()
  check=lambda db,bid:bid==1
  app=Flask('widget-test');app.secret_key='fixture'
  with app.test_request_context('/'),patch('services.category_service.CategoryService.get_libraries',return_value=[{'id':1,'name':'library'}]),patch.object(m,'_load_core_dependencies',return_value=({'check_book_rating_permission':check},None)),patch('services.book_service.get_cover_image_with_t',return_value='allowed-cover'):
   session.update(user_id=1,role='admin',content_rating_max=0)
   data=p._home_recently_added('general')
   books=[x for x in data['items'] if 'id' in x]
   self.assertEqual([x['id'] for x in books],[1]);self.assertNotIn('restricted',str(data));self.assertNotIn('secret.jpg',str(data))
 def test_summary_heading_cleanup_and_body_preservation(self):
  for prefix in ['작품 소개\n\n','## 작품 소개\n','<h2>작품 소개</h2>','작품 소개: ','[줄거리]\n']:
   self.assertEqual(m._metadata_summary(prefix+'본문 <img src="cover.jpg">'),'본문 <img src="cover.jpg">')
  self.assertEqual(m._metadata_summary('이 작품 소개를 읽어 보세요.'),'이 작품 소개를 읽어 보세요.')
  self.assertEqual(m._metadata_summary('본문의 [줄거리] 표현'),'본문의 [줄거리] 표현')
