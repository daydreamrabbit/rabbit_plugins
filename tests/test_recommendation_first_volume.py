import sqlite3
import unittest
from unittest.mock import patch
from .. import rabbit_plugins as r

class RecommendationCoverTests(unittest.TestCase):
 def test_later_added_first_volume_replaces_earlier_second(self):
  db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;self.addCleanup(db.close)
  db.execute('CREATE TABLE books(id INTEGER,library_id INTEGER,series_name TEXT,series_alias TEXT,localized_series TEXT,author TEXT,cover_updated_at TEXT,books_lv TEXT,genre TEXT,tags TEXT,cover_image TEXT,is_deleted INTEGER,document_volume_index REAL)')
  for id,volume,cover,deleted in [(1,2,'second',0),(2,3,'third',0),(3,1,'deleted-first',1)]:
   db.execute("INSERT INTO books VALUES(?,2,'작품','','','','','','성장','','',?,?)",(id,deleted,volume))
   db.execute('UPDATE books SET cover_image=? WHERE id=?',(cover,id))
  class Gateway:
   def fetch_all(self,q,p=()):return [dict(row) for row in db.execute(q,p)]
  provider=object.__new__(r.RabbitPluginsMetadataProvider)
  def rows():
   with patch.object(r,'_optional_column_sql',side_effect=lambda g,t,a,c:a+'.'+c):
    return provider._matching_book_rows(Gateway(),{'series_name':'다른 작품'},'',(),{'genre':{'성장'}})
  self.assertTrue(all(x['first_cover']=='second' for x in rows()))
  db.execute("INSERT INTO books VALUES(4,2,'작품','','','','','','성장','','first',0,1)")
  self.assertTrue(all(x['first_cover']=='first' for x in rows()))
