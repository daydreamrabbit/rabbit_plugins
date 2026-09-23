import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from .. import provider_search as api
from .. import rabbit_plugins as m

class AuthorTests(unittest.TestCase):
    def test_discovery_verifies_author_and_filters_title(self):
        calls=[]
        def request(obj,url,cfg,**kw):
            calls.append(url)
            if '/novel/' in url:
                body='<a href="/user/42" class="writer-name">작가</a>'
            else:
                body=json.dumps({'status':200,'result':{'novel':[
                    {'novel_no':1,'mem_no':42,'writer_nick':'작가','novel_name':'작품 속 검객',
                     'is_del':'0','novel_genre':'["무협", "회귀"]','novel_thumb':'/imagebox/cover/a.jpg'},
                    {'novel_no':2,'mem_no':42,'writer_nick':'다른작가','novel_name':'작품 속 검객'}]}})
            return body.encode(),SimpleNamespace(geturl=lambda:url)
        with patch.object(api.SearchAdapter,'_search_novelpia',return_value=[]),patch.object(api.SearchAdapter,'_get_json',return_value={'results':[
                {'url':'https://evil.example/novel/1'}, {'url':'https://novelpia.com/novel/1'}]}), \
             patch.object(api.SearchAdapter,'_request',request):
            rows,ids=api.search_novelpia_author('작품','작가',lambda q,t:q in t,search_url='https://search.example/search')
            self.assertEqual(ids,['42'])
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['genre'],'웹소설, 무협')
            self.assertEqual(rows[0]['tags'],'회귀')
            self.assertEqual(rows[0]['cover'],'https://images.novelpia.com/imagebox/cover/a.jpg')
            rows,_=api.search_novelpia_author('작품','작가',lambda q,t:q==t,known_ids=['42'])
            self.assertEqual(rows,[])
        self.assertFalse(any('evil.example' in c for c in calls))

    def test_unknown_author_does_not_guess(self):
        with patch.object(api.SearchAdapter,'_search_novelpia',return_value=[]),patch.object(api.SearchAdapter,'_get_json',return_value={'results':[{'url':'https://novelpia.com/novel/1'}]}), \
             patch.object(api.SearchAdapter,'_request',return_value=(b'<a class="writer-name" href="/user/99">wrong</a>',SimpleNamespace(geturl=lambda:'https://novelpia.com/novel/1'))):
            self.assertEqual(api.search_novelpia_author('작품','작가',lambda q,t:True,search_url='https://search.example/search'),([],[]))

    def test_main_fallback_manual_and_automatic(self):
        p=object.__new__(m.RabbitPluginsMetadataProvider)
        cache={}
        p.cache_get=lambda key:cache.get(key)
        p.cache_set=lambda key,value,**kw:cache.update({key:value})
        def fallback(query,author,matches,**kw):
            self.assertEqual(author,'작가')
            title='작품 속 검객'
            return ([dict(id='novelpia:1',source='novelpia',url='https://novelpia.com/novel/1',title=title,author=author)] if matches(query,title) else []),['42']
        cfg={'metadata_sources':'novelpia','novelpia_author_search_url':'https://search.example/search'}
        with patch.object(m,'search_additional_provider',side_effect=lambda *a:[]),patch.object(m,'search_novelpia_author',side_effect=fallback) as f:
            self.assertEqual(len(p._search_metadata('작품 [작가]',cfg,content_kind='novel',manual=True)),1)
            self.assertEqual(p._search_metadata('작품 [작가]',cfg,content_kind='novel'),[])
            self.assertEqual(f.call_args.kwargs['known_ids'],['42'])

    def test_new_author_without_external_service(self):
        payload={'status':200,'result':{'novel':[{'novel_no':7,'mem_no':42,'writer_nick':'작가','novel_name':'작품'}]}}
        with patch.object(api.SearchAdapter,'_search_novelpia',return_value=[{'author_id':'42','author':'작가'}]) as lookup, \
             patch.object(api.SearchAdapter,'_request',return_value=(json.dumps(payload).encode(),None)):
            rows,ids=api.search_novelpia_author('작품','작가',lambda q,t:q==t)
        self.assertEqual(ids,['42'])
        self.assertEqual(rows[0]['title'],'작품')
        self.assertEqual(lookup.call_args.args[1]['NOVELPIA_SEARCH_TYPE'],'writer_nick')

    def test_public_discovery_without_author_rejects_unrelated_title(self):
        def request(obj,url,cfg,**kw):
            if 'search.yahoo' in url:
                body='<a href="https://novelpia.com/novel/1">작품</a><a href="https://novelpia.com/novel/2">작품</a>'
            elif '/novel/' in url:
                title='무관한 제목' if url.endswith('/1') else '작품'
                body=f'<title>노벨피아 - 웹소설로 꿈꾸는 세상! - {title}</title><a class="writer-name" href="/user/42">작가</a>'
            else:
                body=json.dumps({'status':200,'result':{'novel':[{'novel_no':2,'mem_no':42,'writer_nick':'작가','novel_name':'작품'}]}})
            return body.encode(),SimpleNamespace(geturl=lambda:url)
        with patch.object(api.SearchAdapter,'_request',request):
            rows,ids=api.search_novelpia_author('작품','',lambda q,t:q==t)
        self.assertEqual(ids,['42'])
        self.assertEqual([r['url'] for r in rows],['https://novelpia.com/novel/2'])

    def test_public_engine_failure_uses_next_engine(self):
        adapter=api.SearchAdapter(lambda q,t:q==t)
        def request(url,cfg,**kw):
            if 'yahoo' in url:
                raise TimeoutError('fixture')
            body='<a href="https://novelpia.com/novel/2">작품</a>' if 'naver' in url else '<title>작품</title><a class="writer-name" href="/user/42">작가</a>'
            return body.encode(),SimpleNamespace(geturl=lambda:url)
        with patch.object(adapter,'_request',side_effect=request):
            self.assertEqual(api._discover_novelpia_profiles(adapter,'작품','',lambda q,t:q==t,{}),{'42':'작가'})

    def test_main_without_author_uses_public_fallback(self):
        p=object.__new__(m.RabbitPluginsMetadataProvider)
        p.cache_get=lambda key:None
        p.cache_set=lambda *a,**kw:None
        row=dict(id='novelpia:2',source='novelpia',url='https://novelpia.com/novel/2',title='작품',author='작가')
        with patch.object(m,'search_additional_provider',return_value=[]),patch.object(m,'search_novelpia_author',return_value=([row],['42'])) as lookup:
            rows=p._search_metadata('작품',{'metadata_sources':'novelpia'},content_kind='novel',manual=True)
        self.assertEqual(len(rows),1)
        self.assertEqual(lookup.call_args.args[1],'')
