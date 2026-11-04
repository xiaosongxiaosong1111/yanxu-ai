"""N4 retrieval methods and explicit build APIs use only isolated fake backends."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.parse import urlencode
from unittest.mock import Mock

from knowledge_store import KnowledgeError
from service import AdmissionService, ServiceError
from server import create_server, RateLimiter
from tests.test_knowledge_hybrid import Store, Index, record


class Vector(Index):
    def __init__(self,rows):super().__init__(rows);self.builds=[];self.closed=False
    def status(self):return {'status':'ready','active_provider':'local_bge','active_space_id':'vs_fixture','building':False}
    def start_build(self,profile_id=None):
        self.builds.append(profile_id);return {**self.status(),'started':True,'build_id':'vb_fixture'}
    def close(self):self.closed=True


class HybridHTTPTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='n4-hybrid-http-');self.addCleanup(temporary.cleanup)
        self.data=Path(temporary.name)/'data';self.row=record(1,'计算机招生科目301')
        self.store=Store([self.row]);self.vector=Vector([self.row])
        self.service=AdmissionService(self.data,knowledge=self.store,search_api_key='')
        self.service._vectors=self.vector
        self.server=create_server(port=0,service=self.service,rate_limit=1000)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.stop)
    def stop(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)
    def request(self,path,method='GET',body=None,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_address[1],timeout=4)
        self.addCleanup(connection.close)
        values={'Content-Type':'application/json'} if body is not None else {}
        connection.request(method,path,body=json.dumps(body) if body is not None else None,headers={**values,**(headers or {})})
        response=connection.getresponse();return response.status,json.loads(response.read())

    def test_get_is_local_only_and_post_explicitly_allows_remote_with_same_scope(self):
        payload={'q':'计算机','unit_id':'u','year':2027,'entry_id':self.row['entry_id'],'limit':2}
        status,value=self.request('/api/knowledge/retrieve?'+urlencode(payload))
        self.assertEqual(status,200);self.assertEqual(value['data']['returned_count'],1)
        self.assertFalse(self.vector.calls[-1][1]['allow_remote'])
        status,value=self.request('/api/knowledge/retrieve','POST',payload)
        self.assertEqual(status,200);self.assertTrue(self.vector.calls[-1][1]['allow_remote'])
        self.assertEqual(self.vector.calls[-1][1]['unit_id'],'u');self.assertNotIn('total',value['data'])

    def test_status_is_pure_and_build_only_starts_on_explicit_bounded_post(self):
        status,value=self.request('/api/knowledge/vectors')
        self.assertEqual(status,200);self.assertEqual(value['data']['active_provider'],'local_bge')
        self.assertEqual(self.vector.calls,[]);self.assertEqual(self.vector.builds,[])
        status,value=self.request('/api/knowledge/vectors/build','POST',{'profile_id':'ep_fixture'})
        self.assertEqual(status,202);self.assertTrue(value['data']['started'])
        self.assertEqual(self.vector.builds,['ep_fixture'])
        self.assertEqual(self.request('/api/knowledge/vectors/build')[0],405)
        self.assertEqual(self.request('/api/knowledge/vectors','POST',{})[0],405)

    def test_unknown_duplicate_scope_private_query_and_invalid_bounds_never_embed(self):
        paths=['/api/knowledge/retrieve?q=408&q=301','/api/knowledge/retrieve?q=408&limit=21',
               '/api/knowledge/retrieve?q=408&collection=private','/api/knowledge/retrieve?q=13812345678',
               '/api/knowledge/retrieve?q=408&entry_id=../../private','/api/knowledge/vectors?profile_id=private']
        for path in paths:
            with self.subTest(path=path):self.assertEqual(self.request(path)[0],400)
        self.assertEqual(self.vector.calls,[])

    def test_mutation_and_explicit_remote_submit_reject_cross_origin_and_rate_limit(self):
        for path,body in [('/api/knowledge/retrieve',{'q':'计算机'}),('/api/knowledge/vectors/build',{})]:
            with self.subTest(path=path):self.assertEqual(self.request(path,'POST',body,{'Origin':'https://evil.example'})[0],403)
        self.server.knowledge_limiter=RateLimiter(limit=0)
        self.assertEqual(self.request('/api/knowledge/vectors/build','POST',{})[0],429)
        self.server.retrieval_limiter=RateLimiter(limit=0)
        self.assertEqual(self.request('/api/knowledge/retrieve','POST',{'q':'计算机'})[0],429)
        self.assertEqual(self.vector.calls,[]);self.assertEqual(self.vector.builds,[])

    def test_unknown_backend_errors_are_safe_and_closing_closes_the_shared_index(self):
        self.vector.start_build=Mock(side_effect=RuntimeError('private-api-canary C:/private'))
        status,value=self.request('/api/knowledge/vectors/build','POST',{})
        self.assertEqual(status,503);self.assertNotIn('private-api-canary',str(value))
        self.service.close();self.assertTrue(self.vector.closed)
        with self.assertRaises(ServiceError):self.service.knowledge_request('vectors',{})


if __name__=='__main__':unittest.main()
