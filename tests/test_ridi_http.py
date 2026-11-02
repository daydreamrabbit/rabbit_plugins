import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import requests
from .. import ridi_http as h

class RidiHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = patch.object(h, 'CACHE_PATH', Path(self.tmp.name)/'requests.sqlite')
        self.path.start()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.path.stop)
    def response(self, status=200, retry=None):
        r=requests.Response();r.status_code=status;r.url='https://ridibooks.com/books/1'
        r._content=b'<script>var bookDetail = {};</script>';r.headers['Content-Type']='text/html';r.encoding='utf-8'
        if retry:r.headers['Retry-After']=retry
        return r
    def test_success_cached_and_returned_as_independent_response(self):
        with patch.object(h.requests,'get',return_value=self.response()) as get:
            first=h.get('https://ridibooks.com/books/1');second=h.get('https://ridibooks.com/books/1')
        self.assertEqual(get.call_count,1);self.assertEqual(first.text,second.text)
        self.assertIsNot(first,second)
    def test_retry_after_blocks_other_books_and_search(self):
        with patch.object(h.requests,'get',return_value=self.response(429,'120')) as get:
            with self.assertRaises(requests.HTTPError):h.get('https://ridibooks.com/books/1')
            with self.assertRaises(h.RidiDeferred):h.get('https://ridibooks.com/search?q=other')
            self.assertEqual(get.call_count,1)
    def test_expired_cooldown_allows_later_request(self):
        with patch.object(h.time,'time',return_value=100):
            with patch.object(h.requests,'get',return_value=self.response(429,'120')):
                with self.assertRaises(requests.HTTPError):h.get('https://ridibooks.com/books/1')
        with patch.object(h.time,'time',return_value=3701):
            with patch.object(h.requests,'get',return_value=self.response()) as get:
                self.assertEqual(h.get('https://ridibooks.com/books/1').status_code,200)
                self.assertEqual(get.call_count,1)
    def test_cached_success_available_during_cooldown(self):
        with patch.object(h.requests,'get',return_value=self.response()):h.get('https://ridibooks.com/books/1')
        with patch.object(h.time,'sleep'),patch.object(h.requests,'get',return_value=self.response(429)) as get:
            with self.assertRaises(requests.HTTPError):h.get('https://ridibooks.com/books/2')
            with self.assertRaises(h.RidiDeferred):h.get('https://ridibooks.com/books/1')
            self.assertEqual(get.call_count,1)
    def test_inter_request_interval(self):
        def response(url, **kwargs):
            r=self.response();r.url=url;return r
        with patch.object(h.random,'uniform',return_value=1.25),patch.object(h.time,'time',return_value=100),patch.object(h.requests,'get',side_effect=response),patch.object(h.time,'sleep') as sleep:
            h.get('https://ridibooks.com/books/1');h.get('https://ridibooks.com/books/2')
            sleep.assert_called_once_with(11.25)
    def test_retry_http_date_and_exponential_default(self):
        self.assertEqual(h._retry_delay('Thu, 01 Jan 1970 00:02:00 GMT',1,100),20)
        self.assertEqual(h._retry_delay(None,1,100),60)
        self.assertEqual(h._retry_delay(None,2,100),120)
    def test_network_error_also_backs_off(self):
        with patch.object(h.requests,'get',side_effect=requests.Timeout) as get:
            with self.assertRaises(requests.Timeout):h.get('https://ridibooks.com/books/1')
            with self.assertRaises(h.RidiDeferred):h.get('https://ridibooks.com/books/2')
            self.assertEqual(get.call_count,1)
    def test_failed_ridi_metadata_does_not_write_search_or_lower_source_fields(self):
        from .. import rabbit_plugins as m
        from unittest.mock import Mock
        p=object.__new__(m.RabbitPluginsMetadataProvider)
        item=p._merge_metadata_candidates([
            {'source':'ridi','metadata':{'_fetch_error':'HTTPError','author':'flattened names','summary':'search snippet'}},
            {'source':'naver','metadata':{'author':'English author','summary':'other summary'}}],'manga')
        gateway=Mock()
        ok,message=p._apply_metadata(gateway,1,item,{'metadata_overwrite':True})
        self.assertFalse(ok);gateway.execute.assert_not_called()
    def test_search_rate_limit_prevents_lower_provider_automatic_overwrite(self):
        from .. import rabbit_plugins as m
        from unittest.mock import Mock
        with patch.object(h.requests,'get',return_value=self.response(429,'60')):
            with self.assertRaises(requests.HTTPError):h.get('https://ridibooks.com/search?q=test')
        self.assertTrue(h.cooling_down())
        p=object.__new__(m.RabbitPluginsMetadataProvider);g=Mock()
        ok,_=p._apply_metadata(g,1,{'source':'merged','metadata':{'author':'English name'}},
                              {'metadata_sources':'ridi,naver','metadata_overwrite':True})
        self.assertFalse(ok);g.execute.assert_not_called()
    def test_readonly_cache_is_not_reported_as_provider_rate_limit(self):
        import sqlite3
        from unittest.mock import Mock
        db=Mock();db.execute.side_effect=sqlite3.OperationalError('attempt to write a readonly database')
        with patch.object(h.sqlite3,'connect',return_value=db):
            with self.assertRaises(requests.RequestException) as error:
                h.get('https://ridibooks.com/books/1')
        self.assertNotIsInstance(error.exception,h.RidiDeferred)
        self.assertIn('로컬 캐시 저장 오류',str(error.exception))
        db.close.assert_called_once()

    def test_invalid_page_retried_once_without_caching(self):
        invalid=self.response();invalid._content=b'<script id="__NEXT_DATA__">{"props":{"pageProps":{"adultBridge":{"title":"login"}}}}</script>'
        with patch.object(h.time,'sleep'), patch.object(h.requests,'get',side_effect=[invalid,self.response()]) as get:
            self.assertTrue(h.has_product_payload(h.get('https://ridibooks.com/books/1').text))
            self.assertEqual(get.call_count,2)
            self.assertEqual(get.call_args.kwargs['headers']['Cache-Control'],'no-cache')
            h.get('https://ridibooks.com/books/1')
            self.assertEqual(get.call_count,2)

    def test_repeated_invalid_page_fails_and_is_not_cached(self):
        invalid=self.response();invalid._content=b'<script id="__NEXT_DATA__">{}</script>'
        with patch.object(h.time,'sleep'),patch.object(h.requests,'get',return_value=invalid) as get:
            with self.assertRaises(requests.RequestException):h.get('https://ridibooks.com/books/1')
            self.assertEqual(get.call_count,2)
        import sqlite3
        with sqlite3.connect(h.CACHE_PATH) as db:
            self.assertEqual(db.execute('select count(*) from pages').fetchone()[0],0)

    def test_old_invalid_cache_is_evicted(self):
        import sqlite3
        with patch.object(h.requests,'get',return_value=self.response()):h.get('https://ridibooks.com/books/1')
        with sqlite3.connect(h.CACHE_PATH) as db:
            db.execute('update pages set body=?',('<script id="__NEXT_DATA__">{}</script>',))
        with patch.object(h.time,'sleep'),patch.object(h.requests,'get',return_value=self.response()) as get:
            h.get('https://ridibooks.com/books/1');self.assertEqual(get.call_count,1)

    def test_series_delete_evicts_only_target_pages_and_preserves_gate(self):
        import sqlite3
        with patch.object(h.requests,'get',return_value=self.response()):h.get('https://ridibooks.com/books/1')
        with sqlite3.connect(h.CACHE_PATH) as db:
            db.execute('INSERT INTO pages VALUES (?,?,?)',('https://ridibooks.com/books/2','valid',99999999999))
            db.execute('INSERT INTO pages VALUES (?,?,?)',('https://ridibooks.com/search?q=Same','valid',99999999999))
            gate=db.execute('SELECT * FROM gate').fetchall()
        h.invalidate_series_cache(['Same'],['https://ridibooks.com/books/1'])
        with sqlite3.connect(h.CACHE_PATH) as db:
            self.assertEqual(db.execute('SELECT url FROM pages').fetchall(),[('https://ridibooks.com/books/2',)])
            self.assertEqual(db.execute('SELECT * FROM gate').fetchall(),gate)

    def test_429_blocks_for_at_least_one_hour(self):
        import sqlite3
        with patch.object(h.time,'time',return_value=100),patch.object(h.requests,'get',return_value=self.response(429,'3')):
            with self.assertRaises(requests.HTTPError):h.get('https://ridibooks.com/books/1')
        with sqlite3.connect(h.CACHE_PATH) as db:self.assertEqual(db.execute('select next_at from gate').fetchone()[0],3700)

    def test_priority_search_failure_discards_local_results_and_stops_fallback(self):
        from .. import rabbit_plugins as m
        from .. import collection_history as history
        p=m.RabbitPluginsMetadataProvider()
        def fail(*a,**kw):history.fetch_failed.set(True);return []
        token=history.priority_failed.set('')
        try:
            with patch.object(p,'cache_get',return_value=None),patch.object(p,'_series_db_search',return_value=[]),patch.object(m,'_remote_search',side_effect=fail) as remote:
                self.assertEqual(p._search_metadata('작품제목',{'metadata_sources':'series_db,naver,ridi'},content_kind='manga'),[])
                self.assertEqual(remote.call_count,1)
                self.assertEqual(history.priority_failed.get(),'naver')
        finally:history.priority_failed.reset(token)

    def test_jitter_bounds_and_cache_does_not_reserve_another_slot(self):
        import sqlite3
        for jitter in (0.5, 2.0):
            with patch.object(h.random, 'uniform', return_value=jitter) as rand, patch.object(h.time, 'time', return_value=100), patch.object(h.requests, 'get', return_value=self.response()) as get:
                h.CACHE_PATH.unlink(missing_ok=True)
                h.get('https://ridibooks.com/books/1')
                h.get('https://ridibooks.com/books/1')
                rand.assert_called_once_with(0.5, 2.0)
                self.assertEqual(get.call_count, 1)
                with sqlite3.connect(h.CACHE_PATH) as db:
                    self.assertEqual(db.execute('SELECT next_at FROM gate').fetchone()[0], 110+jitter)

    def test_transaction_rechecks_cooldown_before_using_cache(self):
        import sqlite3
        with patch.object(h.requests, 'get', return_value=self.response()):
            h.get('https://ridibooks.com/books/1')
        with sqlite3.connect(h.CACHE_PATH) as db:
            db.execute('UPDATE gate SET next_at=?, failures=1', (h.time.time()+3600,))
        with patch.object(h, 'cooling_down', return_value=False), patch.object(h.requests, 'get') as get:
            with self.assertRaises(h.RidiDeferred):h.get('https://ridibooks.com/books/1')
            get.assert_not_called()

    def test_long_retry_after_not_shortened_by_jitter(self):
        import sqlite3
        with patch.object(h.time,'time',return_value=100), patch.object(h.random,'uniform') as rand, patch.object(h.requests,'get',return_value=self.response(429,'7200')):
            with self.assertRaises(requests.HTTPError):h.get('https://ridibooks.com/books/1')
            rand.assert_not_called()
        with sqlite3.connect(h.CACHE_PATH) as db:
            self.assertEqual(db.execute('SELECT next_at FROM gate').fetchone()[0],7300)
