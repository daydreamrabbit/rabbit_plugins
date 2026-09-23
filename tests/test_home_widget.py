import unittest
from unittest.mock import patch
from flask import Flask,session
from .. import rabbit_plugins as m
class WidgetTests(unittest.TestCase):
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
  for prefix in ['작품 소개\n\n','## 작품 소개\n','<h2>작품 소개</h2>','작품 소개: ']:
   self.assertEqual(m._metadata_summary(prefix+'본문 <img src="cover.jpg">'),'본문 <img src="cover.jpg">')
  self.assertEqual(m._metadata_summary('이 작품 소개를 읽어 보세요.'),'이 작품 소개를 읽어 보세요.')
