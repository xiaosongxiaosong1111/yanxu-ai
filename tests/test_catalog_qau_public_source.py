import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from collectors import catalog_qau_public_source as proof_module
from collectors.catalog_qau_public_source import build_capture_receipt, verify_qau_public_source


UNIT={'id':'368296','name':'青岛农业大学','official_url':'https://www.qau.edu.cn/'}
HOST='https://yjszsw.qau.edu.cn'
CID='a1fabce0a39b4c888e4773a9728c389b'
ARTICLE=HOST+'/content/tzgg/'+CID
SCRIPT=HOST+'/static/front.js'
CALLBACK=HOST+'/getAttaDownloadurl?'+urlencode({'contentid':CID,'modelname':'news','n':'5'})
PDF=b'%PDF-1.7\n test'
BODY='''<h1>2027年青岛农业大学硕士研究生招生章程、专业目录和参考书目</h1>
<script src="/static/front.js"></script>
<a id="attach1" href="javascript:void(0);" class="attachment-item">2-青岛农业大学2027年硕士研究生招生专业目录.pdf</a>
<script>$(function(){Cms.attachment('', 'news', '%s', "5", 'attach');})</script>''' % CID
JS='''Cms.attachment = function(base, model,contentId, n, prefix) {
$.get(base + "/getAttaDownloadurl", {"contentid": contentId, "modelname": model, "n": n},
function(data){if(null!=data){for(var i=0;i<n;i++){$("#"+prefix+i).attr("href",base+data[i]);}}}, "json");
}'''
RESOURCE='/userfiles/image/test2026/2026/09/20260930195528172.pdf'
SIGNED=HOST+'/attachdownload?'+urlencode({'url':RESOURCE,'t':'1794387350209','contentid':CID,'passcode':'a'*32})
RAW_CALLBACK=json.dumps(['/irrelevant',SIGNED.removeprefix(HOST),'/other','/other2','/other3']).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


class SourceProofTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.files={}
        self.document=self.file('pdf','original.pdf',PDF,ARTICLE)
        self.article=self.file('article','article.html',BODY.encode(),ARTICLE)
        self.script=self.file('js','front.js',JS.encode(),SCRIPT)
        self.manifest={'parent':ARTICLE,'callback':{'url':CALLBACK,'final_url':CALLBACK,'sha256':sha(RAW_CALLBACK)},
          'exact_button_published_url':SIGNED,'original':{'url':SIGNED,'final_url':SIGNED,'sha256':sha(PDF),'type':'PDF'}}
        self.receipt=build_capture_receipt(self.manifest,RAW_CALLBACK,PDF,BODY,JS,UNIT)
        expected = {'article':sha(BODY.encode()),'script':sha(JS.encode()),
                    'receipt':sha(json.dumps(self.receipt).encode()),'pdf':sha(PDF)}
        trusted=patch.object(proof_module,'_ARCHIVE_SHA256',expected,create=True)
        trusted.start()
        self.addCleanup(trusted.stop)

    def file(self,id,name,value,url):
        (self.root/name).write_bytes(value)
        return {'id':id,'unit_id':UNIT['id'],'year':2027,'local_path':name,'sha256':sha(value),'url':url}

    def verify(self, article=None, script=None, receipt=None, document=None):
        receipt=self.receipt if receipt is None else receipt
        capture=self.file('receipt','receipt.json',json.dumps(receipt).encode(),ARTICLE)
        doc={**(document or self.document),'qau_public_download':{
              'article':article or self.article,'script':script or self.script,'receipt':capture}}
        return verify_qau_public_source(doc,UNIT,self.root)

    def test_private_raw_response_redacts_entire_signed_query(self):
        content=json.dumps(self.receipt,ensure_ascii=False)
        self.assertNotIn('passcode',content)
        self.assertNotIn('1794387350209',content)
        self.assertNotIn(SIGNED,content)
        self.assertEqual(self.receipt['resource_path'],RESOURCE)
        self.assertEqual(self.receipt['callback_sha256'],sha(RAW_CALLBACK))
        self.assertTrue(self.verify()['verified'])

    def test_article_scope_visibility_and_button_position_are_replayed(self):
        for bad in (BODY.replace('id="attach1"','id="attach2"'),
                    BODY.replace('<a id','<div hidden><a id').replace('</a>','</a></div>'),
                    BODY.replace('2027年','2026年'),
                    BODY.replace("'news'","'photo'")):
            with self.subTest(bad=bad):
                article=self.file('article','bad.html',bad.encode(),ARTICLE)
                self.assertFalse(self.verify(article=article)['verified'])

    def test_button_script_original_integrity_and_destination_required(self):
        changed=self.file('js','bad.js',JS.replace('getAttaDownloadurl','someOtherAction').encode(),SCRIPT)
        self.assertFalse(self.verify(script=changed)['verified'])
        (self.root/'front.js').write_bytes(b'changed')
        self.assertFalse(self.verify()['verified'])

    def test_capture_digest_and_callback_exact_index_are_required(self):
        wrong=RAW_CALLBACK.replace(SIGNED.removeprefix(HOST).encode(),b'/different')
        with self.assertRaises(ValueError):
            build_capture_receipt(self.manifest,wrong,PDF,BODY,JS,UNIT)
        changed={**self.receipt,'button_index':2}
        self.assertFalse(self.verify(receipt=changed)['verified'])
        self.assertFalse(self.verify(receipt={**self.receipt,'passcode':'never-store'})['verified'])

    def test_forged_receipt_and_pdf_with_rewritten_metadata_are_rejected(self):
        malicious=self.file('pdf','replacement.pdf',b'%PDF-1.7\\n adversarial',ARTICLE)
        receipt={**self.receipt,'pdf_sha256':malicious['sha256']}
        self.assertFalse(self.verify(receipt=receipt,document=malicious)['verified'])
        rewritten={**self.receipt,'callback_sha256':'3'*64}
        self.assertFalse(self.verify(receipt=rewritten)['verified'])

    def test_pdf_path_tampering_fails(self):
        (self.root/'original.pdf').write_bytes(b'%PDF-1.7\n replaced')
        self.assertFalse(self.verify()['verified'])

    def test_public_document_url_cannot_be_signed(self):
        self.assertFalse(self.verify(document={**self.document,'url':SIGNED})['verified'])
