"""Regression fixtures for the second cover/provider audit (no live writes)."""
import json
import sqlite3
import unittest
from unittest.mock import patch
from .. import rabbit_plugins as m


class Gateway:
    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('CREATE TABLE settings (`key` TEXT PRIMARY KEY,value TEXT); CREATE TABLE libraries(id INTEGER,content_kind TEXT); INSERT INTO libraries VALUES(2,"manga"); CREATE TABLE books(id INTEGER,library_id INTEGER,file_path TEXT,cover_image TEXT,metadata_locked INTEGER,is_deleted INTEGER,cover_updated_at TEXT); INSERT INTO books VALUES(7,2,"/old","internal.webp",0,0,NULL);')
    def fetch_all(self,q,p=()): return [dict(r) for r in self.db.execute(q,p)]
    def fetch_one(self,q,p=()): return next(iter(self.fetch_all(q,p)),None)
    def execute(self,q,p=()): return self.db.execute(q,p).rowcount
    def get_setting(self,k,d=None): return self.fetch_one('SELECT value FROM settings WHERE `key`=?',(k,)) or d
    def set_setting(self,k,v):
        if len(v.encode())>65535: raise ValueError('TEXT overflow')
        self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(k,v))

class AuditTests(unittest.TestCase):
    def provider(self,g):
        p=object.__new__(m.RabbitPluginsMetadataProvider)
        p.get_db_gateway=lambda _:g
        p.get_plugin_config=lambda *_:{'metadata_collect_cover':True,'metadata_cover_overwrite':True}
        return p
    def restore(self,g,pref,exists=True):
        g.set_setting(m._EXTERNAL_COVER_PREFERENCES_KEY,json.dumps({'7':pref}))
        with patch.object(m,'_stored_cover_exists',return_value=exists,create=True):
            return m._reconcile_external_covers(self.provider(g),'general')
    def pref(self,**kw): return dict(path='2/external.webp',library_id=2,content_kind='manga',**kw)
    def test_01_failed_priority_provider_does_not_select_lower_cover(self):
        p=self.provider(Gateway())
        result=p._merge_metadata_candidates([{'source':'ridi','metadata':{'_fetch_error':'HTTPError'}},{'source':'naver','metadata':{'cover':'https://img/low'}}],'manga')
        self.assertTrue(result.get('cover_deferred'))
        self.assertFalse(result['metadata'].get('cover'))
    def test_02_preferences_scale_past_single_text_column(self):
        g=Gateway()
        for n in range(600): m._remember_external_cover(g,n+1,'2/'+('한'*60)+str(n)+'.webp',2,'manga')
        self.assertEqual(len(m._external_cover_preferences(g)),600)
    def test_03_bad_preference_entry_is_skipped(self):
        self.assertEqual(self.restore(Gateway(),'corrupt'),0)
    def test_04_moved_library_is_not_restored(self):
        g=Gateway(); pref=self.pref(); pref['library_id']=9
        self.assertEqual(self.restore(g,pref),0)
    def test_05_locked_book_is_not_restored(self):
        g=Gateway(); g.execute('UPDATE books SET metadata_locked=1')
        self.assertEqual(self.restore(g,self.pref()),0)
    def test_06_missing_saved_image_is_not_restored(self):
        self.assertEqual(self.restore(Gateway(),self.pref(),exists=False),0)
    def test_07_restore_does_not_overwrite_concurrent_change(self):
        g=Gateway(); orig=g.execute
        def race(q,p=()):
            if q.startswith('UPDATE books SET cover_image'): orig('UPDATE books SET cover_image="manual.webp"')
            return orig(q,p)
        g.execute=race
        with patch.object(m,'_stored_cover_exists',return_value=True,create=True): self.restore(g,self.pref())
        self.assertEqual(g.fetch_one('SELECT cover_image FROM books')['cover_image'],'manual.webp')
    def test_08_ambiguous_local_identity_does_not_merge_links(self):
        p=self.provider(Gateway())
        p._series_db_exact_candidates=lambda *a,**k:[{'id':'a','metadata':{'localized_series':'A','link':'https://mangabaka.org/1'}},{'id':'b','metadata':{'localized_series':'B','link':'https://mangabaka.org/2'}}]
        self.assertEqual(p._manual_series_db_metadata('same','manga'),{})
    def test_09_invalid_cover_map_value_is_not_stringified(self):
        p=self.provider(Gateway())
        result=p._merge_metadata_candidates([{'source':'ridi','cover_by_volume':{'1':None,'2':{'bad':1}}}],'manga')
        self.assertFalse(result.get('cover_by_volume'))
    def test_10_first_volume_publication_date_keeps_priority(self):
        p=self.provider(Gateway())
        result=p._merge_metadata_candidates([{'source':'ridi','release_dates_by_volume':{'1':'2020-01-01'}},{'source':'ridi','release_dates_by_volume':{'1':'2021-01-01'}}],'manga')
        self.assertEqual(result['release_dates_by_volume']['1'],'2020-01-01')
    def test_11_explicit_overwrite_does_not_overwrite_concurrent_change(self):
        g=Gateway(); row=g.fetch_one('SELECT * FROM books'); g.execute('UPDATE books SET cover_image="manual.webp"')
        m._commit_external_cover(g,row,'external.webp',True)
        self.assertEqual(g.fetch_one('SELECT cover_image FROM books')['cover_image'],'manual.webp')
    def test_12_negative_book_id_not_persisted(self):
        g=Gateway(); m._remember_external_cover(g,-1,'2/x.webp',2,'manga')
        self.assertEqual(m._external_cover_preferences(g),{})

    def test_http_failure_state_reaches_merge(self):
        import requests
        p=self.provider(Gateway())
        p._series_db_remote_identity=lambda *a,**k:{}
        with patch('requests.get',side_effect=requests.HTTPError('429')):
            detail=m._remote_fetch_metadata('https://ridibooks.com/books/1','ridi')
        self.assertEqual(detail['_fetch_error'],'HTTPError')
        result=p._merge_metadata_candidates([
            {'source':'ridi','url':'https://ridibooks.com/books/1','_metadata_fetched':True,'metadata':detail},
            {'source':'naver','metadata':{'cover':'https://img/low','summary':'usable'}}],'manga')
        self.assertTrue(result['cover_deferred'])
        self.assertEqual(result['metadata']['summary'],'usable')

    def test_confirmed_no_cover_can_fall_back(self):
        p=self.provider(Gateway())
        result=p._merge_metadata_candidates([{'source':'ridi','metadata':{'title':'book'}},{'source':'naver','metadata':{'cover':'https://img/low'}}],'manga')
        self.assertFalse(result.get('cover_deferred'))
        self.assertEqual(result['metadata']['cover'],'https://img/low')

    def test_lower_failure_does_not_block_successful_priority_cover(self):
        p=self.provider(Gateway())
        result=p._merge_metadata_candidates([{'source':'ridi','metadata':{'cover':'https://img/high'}},{'source':'naver','metadata':{'_fetch_error':'timeout'}}],'manga')
        self.assertFalse(result.get('cover_deferred'))
        self.assertEqual(result['metadata']['cover'],'https://img/high')

    def test_successful_restore_and_legacy_preference_compatibility(self):
        g=Gateway()
        self.assertEqual(self.restore(g,self.pref()),1)
        self.assertEqual(g.fetch_one('SELECT cover_image FROM books')['cover_image'],'2/external.webp')
        m._remember_external_cover(g,7,'2/new.webp',2,'manga')
        self.assertEqual(m._external_cover_preferences(g)['7']['path'],'2/new.webp')
