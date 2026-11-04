"""Prepared one-document local-model smoke; requires already prepared public model files."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile

from embedding_catalog import LOCAL_REVISION,LOCAL_MODEL
from knowledge_vectors import VectorIndex
from settings_store import SettingsService
from probe_fixtures import Fixture


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--allow-local-model',action='store_true')
    parser.add_argument('--model-dir',type=Path)
    args=parser.parse_args()
    project=Path(__file__).resolve().parents[4]
    if not args.allow_local_model or not (project/'data/runtime/node4/claude-b/report.json').is_file():
        raise SystemExit('Local smoke requires a submitted B report and an explicit --allow-local-model flag')
    source=args.model_dir or project/'data/runtime/embedding-models/bge-small-zh-v1.5'/LOCAL_REVISION
    if not source.is_dir():
        print(json.dumps({'status':'not_run','reason':'prepared_public_model_missing'}))
        raise SystemExit(77)
    with tempfile.TemporaryDirectory(prefix='n4b-local-owned-') as temporary:
        data=Path(temporary)/'data'
        target=data/'runtime/embedding-models/bge-small-zh-v1.5'/LOCAL_REVISION
        shutil.copytree(source,target)
        fixture=Fixture(data)
        settings=SettingsService(data)
        draft={'kind':'embedding','provider':'local_bge','model':LOCAL_MODEL}
        receipt=settings.test(draft)
        settings.save({**draft,'validation_token':receipt['validation_token']})
        index=VectorIndex(fixture.store,settings)
        try:
            wanted=fixture.save()
            status=index.build()
            cached=set((data/'runtime/vector-index/cache').rglob('*.bin'))
            result=index.search('跨考是否需要额外考试',unit_id='unit-a',year=2027,limit=2)
            if not result['segments']:raise AssertionError('Local smoke produced no trusted source hit')
            if any(hit['entry_id']!=wanted['id'] for hit in result['segments']):
                raise AssertionError('Local smoke returned an unrelated source')
            if cached!=set((data/'runtime/vector-index/cache').rglob('*.bin')):
                raise AssertionError('Query inference persisted into the public embedding cache')
            print(json.dumps({'status':'passed','scope':'one synthetic public source, real local model and native Milvus',
                              'space_id':status['active_space_id'],'provider':status['active_provider'],
                              'hits':len(result['segments']),'usage':result.get('usage'),
                              'usage_known':result.get('usage_known'),'latency_ms':result.get('latency_ms')}))
        finally:
            index.close();fixture.close()


if __name__=='__main__':main()
