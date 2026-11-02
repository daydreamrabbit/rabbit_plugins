import unittest
from unittest.mock import patch
from flask import Flask, session
from .. import manual_relations as m
from ..rabbit_plugins import RabbitPluginsMetadataProvider
class Gateway:
 def __init__(self):self.settings={};self.rows=[]
 def set_setting(self,k,v):self.settings[k]=v
 def fetch_all(self,q,p=()):
  if 'FROM settings' in q:return [{'key':k,'value':v} for k,v in self.settings.items()]
  return self.rows
class RelationsTests(unittest.TestCase):
 def setUp(self):
  self.g=Gateway();self.a={'library_id':1,'series_name':'원작'};self.b={'library_id':2,'series_name':'각색'}
 def test_symmetric_idempotent_and_removable(self):
  m.change(self.g,self.a,self.b);m.change(self.g,self.b,self.a)
  self.assertEqual(len(self.g.settings),1)
  self.assertEqual(m.linked(self.g,self.a)[0][1],m.identity(self.b))
  self.assertEqual(m.linked(self.g,self.b)[0][1],m.identity(self.a))
  m.change(self.g,self.b,self.a,True)
  self.assertEqual(m.linked(self.g,self.a),[])
 def test_same_series_rejected_but_same_title_in_different_library_allowed(self):
  self.assertFalse(m.change(self.g,self.a,self.a)['success'])
  self.assertTrue(m.change(self.g,self.a,dict(self.a,library_id=2))['success'])
 def test_hidden_books_not_in_related_cards(self):
  m.change(self.g,self.a,self.b)
  self.g.rows=[{'id':99,'library_id':2,'series_name':'각색','books_lv':'R18+','genre':'성인','tags':''}]
  self.assertEqual(m.cards(self.g,self.a,visible=lambda row:False),[])
  self.assertEqual(m.cards(self.g,self.a,check_rating=lambda *a:False),[])
 def test_deleted_targets_are_omitted(self):
  m.change(self.g,self.a,self.b)
  self.assertEqual(m.cards(self.g,self.a),[])
 def test_non_admin_cannot_manage(self):
  app=Flask('test');app.secret_key='test'
  p=object.__new__(RabbitPluginsMetadataProvider)
  with app.test_request_context():
   session['role']='user'
   for action in ('related_search','related_add','related_remove','related_list'):
    self.assertFalse(p.run_context_menu_action('general',action,{'book_id':1})['success'])
 def test_typed_relations_preserve_direction_regardless_of_sort(self):
  for kind,inverse in m.INVERSE.items():
   for a,b in ((self.a,self.b),(self.b,self.a)):
    with self.subTest(kind=kind,source=a):
     m.change(self.g,a,b,relation_type=kind)
     self.assertEqual(m.typed_links(self.g,a)[0][2],kind)
     self.assertEqual(m.typed_links(self.g,b)[0][2],inverse)
 def test_legacy_relations_remain_other_and_can_be_updated(self):
  import json
  m.change(self.g,self.a,self.b)
  key=next(iter(self.g.settings));value=json.loads(self.g.settings[key]);value.pop('types')
  self.g.settings[key]=json.dumps(value)
  self.assertEqual(m.typed_links(self.g,self.a)[0][2],'other')
  m.change(self.g,self.a,self.b,relation_type='adaptation')
  self.assertEqual(m.typed_links(self.g,self.b)[0][2],'original')
  self.assertEqual(len(self.g.settings),1)
 def test_invalid_type_does_not_write(self):
  self.assertFalse(m.change(self.g,self.a,self.b,relation_type='bad')['success'])
  self.assertEqual(self.g.settings,{})
 def test_both_sides_can_be_set_independently(self):
  for a,b in ((self.a,self.b),(self.b,self.a)):
   m.change(self.g,a,b,relation_type='adaptation',reverse_relation_type='main_story')
   self.assertEqual(m.typed_links(self.g,a)[0][2],'adaptation')
   self.assertEqual(m.typed_links(self.g,b)[0][2],'main_story')
   m.change(self.g,b,a,relation_type='other',reverse_relation_type='adaptation')
   self.assertEqual(m.typed_links(self.g,a)[0][2],'adaptation')
   self.assertEqual(m.typed_links(self.g,b)[0][2],'other')
 def test_invalid_reverse_type_does_not_write(self):
  self.assertFalse(m.change(self.g,self.a,self.b,relation_type='adaptation',reverse_relation_type='bad')['success'])
  self.assertEqual(self.g.settings,{})
 def test_search_excludes_linked_series_before_limit(self):
  import sqlite3,json
  db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
  self.addCleanup(db.close)
  db.executescript('CREATE TABLE books(id INTEGER,library_id INTEGER,series_name TEXT,is_deleted INTEGER); CREATE TABLE libraries(id INTEGER,name TEXT,content_kind TEXT); CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT); INSERT INTO libraries VALUES(1,"서재","comic"); INSERT INTO books VALUES(1,1,"원작",0);')
  for i in range(2,34):db.execute('INSERT INTO books VALUES(?,1,?,0)',(i,f'작품 {i:02d}'))
  class SQLGateway:
   def fetch_one(self,q,p=()):
    r=db.execute(q,p).fetchone();return dict(r) if r else None
   def fetch_all(self,q,p=()):return [dict(r) for r in db.execute(q,p)]
   def set_setting(self,k,v):db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(k,v))
  g=SQLGateway()
  for i in range(2,33):m.change(g,{'library_id':1,'series_name':'다른 본편'},{'library_id':1,'series_name':f'작품 {i:02d}'})
  app=Flask('test');app.secret_key='test';provider=object.__new__(RabbitPluginsMetadataProvider);provider.get_db_gateway=lambda _:g;provider._discovery_data=lambda *a,**kw:{'relations':{}}
  with app.test_request_context():
   session['role']='admin'
   result=provider.run_context_menu_action('general','related_search',{'book_id':1,'query':'작품'})
  self.assertEqual([r['book_id'] for r in result['items']],[33])

 def test_connected_series_includes_other_works_and_removal_restores_eligibility(self):
  c={'library_id':3,'series_name':'가이드북 후속작'}
  m.change(self.g,self.b,c,relation_type='sequel')
  self.assertEqual(m.linked(self.g,self.a),[])
  self.assertIn((3,'가이드북 후속작'),m.connected_series(self.g))
  self.assertIn((2,'각색'),m.connected_series(self.g))
  self.assertNotIn((4,'가이드북 후속작'),m.connected_series(self.g))
  m.change(self.g,self.b,c,remove=True)
  self.assertEqual(m.connected_series(self.g),[])

 def test_automatic_cards_visible_and_manual_override(self):
  auto={'library_id':2,'series_name':'각색','book_id':2}
  result={'relations':{'adaptation':[auto]},'recommendations':[]}
  merged=m.merge_result(result,[])
  card=merged['relations']['adaptation'][0]
  self.assertEqual(card['connection_origin'],'automatic')
  self.assertEqual(card['reverse_relation_type'],'original')
  manual=dict(auto,relation_type='sequel',reverse_relation_type='prequel')
  merged=m.merge_result(result,[manual])
  self.assertNotIn('adaptation',merged['relations'])
  self.assertEqual(merged['relations']['sequel'][0]['connection_origin'],'manual')
 def test_automatic_removal_is_symmetric_and_readd_restores(self):
  m.change(self.g,self.a,self.b,remove=True)
  self.assertIn(tuple(m.identity(self.b)),m.suppressed(self.g,self.a))
  self.assertIn(tuple(m.identity(self.a)),m.suppressed(self.g,self.b))
  result={'relations':{'adaptation':[dict(self.b,book_id=2)]}}
  self.assertEqual(m.merge_result(result,[],m.suppressed(self.g,self.a))['relations'],{})
  m.change(self.g,self.a,self.b,relation_type='adaptation')
  self.assertEqual(m.suppressed(self.g,self.a),set())
