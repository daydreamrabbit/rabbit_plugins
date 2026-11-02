import json
import unittest
from unittest.mock import Mock, patch
from .. import deferred_metadata as d, rabbit_plugins as m, ridi_http

class DeferredTests(unittest.TestCase):
    def setUp(self):
        from .. import collection_history
        for var, value in [(collection_history.fetch_failed, False), (collection_history.priority_failed, '')]:
            token = var.set(value)
            self.addCleanup(var.reset, token)

    def test_delay_validation(self):
        for value, expected in [(None,0), ('no',0),(-1,0),('60',3600),(99999,604800)]:
            self.assertEqual(d.delay_seconds({'metadata_deferred_minutes':value}),expected)

    def test_only_distinct_enabled_sources(self):
        self.assertTrue(d.distinct_sources([{'source':'ridi'},{'source':'naver'}],['ridi','naver']))
        for candidates in [[],[{'source':'ridi'}],[{'source':'ridi'},{'source':'ridi'}],[{'source':'ridi'},None]]:
            self.assertFalse(d.distinct_sources(candidates,['ridi','naver']))
        self.assertFalse(d.distinct_sources([{'source':'ridi'},{'source':'yes24'}],['ridi']))

    def provider(self):
        p=Mock();p.get_plugin_config.return_value={'metadata_auto_enabled':True,'metadata_deferred_minutes':1,'metadata_sources':'ridi,naver'}
        p.get_db_gateway.return_value.get_setting.return_value={'value':''}
        p.get_db_gateway.return_value.fetch_all.return_value=[]
        return p

    def hold(self):
        return {'book_id':1,'library_id':2,'series_name':'test','checked_at':'2000-01-01T00:00:00', 'candidates':[{'source':'ridi'},{'source':'naver'}]}

    def test_cooldown_blocks_all_processing(self):
        p=self.provider()
        with patch.object(ridi_http,'cooling_down',return_value=True), patch.object(m,'_metadata_hold_entries',return_value=[]): d.tick(p,'general',m.fcntl)
        p._confirm_same_work.assert_not_called()
        p._queue_auto_collect.assert_not_called()

    def test_due_hold_confirmed_and_paced(self):
        p=self.provider()
        with patch.object(ridi_http,'cooling_down',return_value=False),patch.object(m,'_metadata_hold_entries',return_value=[self.hold()]):
            d.tick(p,'general',m.fcntl)
        p._confirm_same_work.assert_called_once_with('general',1,automatic=True)
        p.get_db_gateway.return_value.set_setting.assert_called_once()

    def test_future_hold_not_applied(self):
        p=self.provider();p.get_db_gateway.return_value.get_setting.return_value={'value':'99999999999'}
        with patch.object(ridi_http,'cooling_down',return_value=False),patch.object(m,'_metadata_hold_entries',return_value=[self.hold()]):d.tick(p,'general',m.fcntl)
        p._confirm_same_work.assert_not_called()

    def test_due_failure_retries_book_without_confirming(self):
        p=self.provider();g=p.get_db_gateway.return_value
        state={'status':'retry','book_id':1,'library_id':2,'series_name':'test','checked_at':0,'retry_at':0}
        g.fetch_all.return_value=[{'key':'history','value':json.dumps(state)}]
        with patch.object(ridi_http,'cooling_down',return_value=False),patch.object(m,'_metadata_hold_entries',return_value=[]):d.tick(p,'general',m.fcntl)
        p._queue_auto_collect.assert_called_once_with('general',{'book_ids':[1],'library_id':2})
        p._confirm_same_work.assert_not_called()

    def test_automatic_confirmation_refuses_unavailable_candidate(self):
        p=object.__new__(m.RabbitPluginsMetadataProvider)
        g=Mock();p.get_db_gateway=lambda *_:g
        g.fetch_one.side_effect=lambda sql,args: ({'metadata_locked':0} if 'SELECT metadata_locked' in sql else {'id':1,'library_id':2,'series_name':'test','title':'test','file_path':'/test.cbz','content_kind':'manga'})
        held=[{'source':'ridi','title':'test','author':'a','url':'https://ridibooks.com/books/1'}, {'source':'naver','title':'test','author':'b','url':'https://series.naver.com/1'}]
        p.get_plugin_config=lambda *_:{'metadata_sources':'ridi,naver'}
        p._search_metadata=Mock(return_value=[]);p._apply_metadata=Mock()
        with patch.object(m,'_optional_column_sql',return_value='NULL'),patch.object(m,'_read_metadata_hold',return_value={'candidates':held}):
            result=p._confirm_same_work('general',1,automatic=True)
        self.assertFalse(result['success']);p._apply_metadata.assert_not_called()

    def test_automatic_confirmation_success_uses_priority_preserving_merge(self):
        p=object.__new__(m.RabbitPluginsMetadataProvider);g=Mock();p.get_db_gateway=lambda *_:g
        g.fetch_one.side_effect=lambda sql,args: ({'metadata_locked':0} if 'SELECT metadata_locked' in sql else {'id':1,'library_id':2,'series_name':'test','title':'test','file_path':'/test.cbz','content_kind':'manga'})
        held=[{'source':'naver','title':'test','author':'b','url':'https://series.naver.com/1'}, {'source':'ridi','title':'test','author':'a','url':'https://ridibooks.com/books/1'}]
        p.get_plugin_config=lambda *_:{'metadata_sources':'ridi,naver'}
        p._search_metadata=Mock(return_value=held);p._apply_metadata=Mock(return_value=(True,'ok'));p._merge_metadata_candidates=Mock(return_value={})
        with patch.object(m,'_optional_column_sql',return_value='NULL'),patch.object(m,'_read_metadata_hold',return_value={'candidates':held}),patch.object(m,'_remote_fetch_metadata',return_value={'author':'a'}):
            result=p._confirm_same_work('general',1,automatic=True)
        self.assertTrue(result['success'])
        self.assertEqual([x['source'] for x in p._merge_metadata_candidates.call_args.args[0]],['ridi','naver'])
        self.assertTrue(p._apply_metadata.call_args.kwargs['preserve_existing'])

    def test_saved_hold_processed_with_sources_now_disabled(self):
        p=self.provider();p.get_plugin_config.return_value['metadata_sources']='series_db'
        with patch.object(ridi_http,'cooling_down',return_value=False),patch.object(m,'_metadata_hold_entries',return_value=[self.hold()]):
            d.tick(p,'general',m.fcntl)
        p._confirm_same_work.assert_called_once_with('general',1,automatic=True)

    def test_disabled_ridi_hold_still_respects_cooldown(self):
        p=self.provider();p.get_plugin_config.return_value['metadata_sources']='series_db'
        with patch.object(ridi_http,'cooling_down',return_value=True),patch.object(m,'_metadata_hold_entries',return_value=[self.hold()]):
            d.tick(p,'general',m.fcntl)
        p._confirm_same_work.assert_not_called()

    def test_status_explains_wait_and_cooldown(self):
        p=self.provider();g=p.get_db_gateway.return_value;c=p.get_plugin_config.return_value
        self.assertIn('순차 처리',d.describe_hold(self.hold(),c,g))
        self.assertIn('제한 해제',d.describe_hold(self.hold(),c,g,True))
        g.get_setting.return_value={'value':'9999999999'}
        self.assertIn('예정',d.describe_hold(self.hold(),c,g))
