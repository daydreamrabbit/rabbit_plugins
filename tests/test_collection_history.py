import unittest
from unittest.mock import patch
from .. import collection_history as h
from .. import rabbit_plugins as m

class Gateway:
    def __init__(self): self.settings={}
    def get_setting(self,k,default=None): return {'value':self.settings.get(k,'')}
    def set_setting(self,k,v): self.settings[k]=v

class CollectionHistoryTests(unittest.TestCase):
    def setUp(self): self.g=Gateway()
    def attempt(self,sig='same'): return h.attempt(self.g,'test',999,'history-test',sig,m.fcntl)
    def test_unchanged_work_skips_next_scan(self):
        with self.attempt() as state: state['status']='applied'
        with self.attempt() as state: self.assertIsNone(state)
    def test_absent_provider_fields_do_not_trigger_continuous_collection(self):
        with self.attempt() as state: state['status']='no_match'
        with self.attempt() as state: self.assertIsNone(state)
    def test_changed_files_allow_immediate_collection(self):
        with self.attempt() as state: state['status']='applied'
        with self.attempt('new-volume') as state: self.assertIsNotNone(state)
    def test_concurrent_series_attempt_is_rejected(self):
        with self.attempt() as first:
            with self.attempt() as second: self.assertIsNone(second)
            first['status']='applied'
    def test_network_failure_retries_after_five_minutes(self):
        with patch.object(h.time,'time',return_value=1000):
            with self.attempt() as state:
                state['status']='no_match';h.fetch_failed.set(True)
        with patch.object(h.time,'time',return_value=1200):
            with self.attempt() as state: self.assertIsNone(state)
        with patch.object(h.time,'time',return_value=1301):
            with self.attempt() as state: self.assertIsNotNone(state)
    def test_failed_attempt_releases_lock(self):
        with self.assertRaises(RuntimeError):
            with self.attempt(): raise RuntimeError('network')
        with self.attempt('changed') as state: self.assertIsNotNone(state)
    def test_config_and_completion_filename_invalidate_fingerprint(self):
        files=[{'id':1,'file_path':'book 1.cbz','file_size':100}]
        first=h.fingerprint(files,{'metadata_sources':'ridi,naver'},'manga')
        self.assertNotEqual(first,h.fingerprint(files,{'metadata_sources':'naver,ridi'},'manga'))
        self.assertNotEqual(first,h.fingerprint([dict(files[0],file_path='book 1 (완결).cbz')],{'metadata_sources':'ridi,naver'},'manga'))
    def test_linked_series_skips_search_and_repeated_scan_then_new_volume_refreshes(self):
        from unittest.mock import Mock
        class Books(Gateway):
            def __init__(self):
                super().__init__();self.size=100
            def fetch_all(self,q,p=()):
                return [{'id':1,'library_id':1,'series_name':'작품','title':'작품 1권',
                         'file_path':'/books/작품 1권.cbz','file_size':self.size,'file_mtime':1,
                         'link':'https://ridibooks.com/books/123','author':'작가',
                         '_content_kind':'manga','_metadata_missing':1}]
            def fetch_one(self,*args): return {'content_kind':'manga'}
        g=Books();p=object.__new__(m.RabbitPluginsMetadataProvider)
        p.get_db_gateway=lambda *_:g
        p.get_plugin_config=lambda *_:{'metadata_sources':'ridi','metadata_collect_cover':False}
        p._refresh_newly_completed_series=lambda *a,**kw:(set(),0)
        p._search_metadata=Mock(return_value=[])
        p._merge_metadata_candidates=Mock(return_value={'source':'merged','metadata':{'author':'작가'}})
        p._apply_metadata=Mock(return_value=(True,'ok'))
        with patch.object(m,'_optional_column_sql',return_value='NULL'),patch.object(
                m,'_remote_fetch_metadata',return_value={'title':'작품','author':'작가'}) as fetch:
            p._auto_collect('general',{'library_id':1})
            p._auto_collect('general',{'library_id':1})
            self.assertEqual(fetch.call_count,1)
            self.assertEqual(p._apply_metadata.call_count,1)
            p._search_metadata.assert_not_called()
            g.size=200
            p._auto_collect('general',{'library_id':1})
            self.assertEqual(fetch.call_count,2)
