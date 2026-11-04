"""Prepared fixed-vector/real-Lite facade persistence probe; never a remote call."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from milvus_backend import MilvusBackend
from probe_fixtures import Fixture


def child(stage,data_dir):
    root=Path(data_dir).resolve().parent
    marker=root/'.owned-independent-probe'
    if not root.name.startswith('n4b-native-owned-') or not marker.is_file():
        raise RuntimeError('Refusing a database without an independent ownership marker')
    fixture=Fixture(Path(data_dir),backend=MilvusBackend(Path(data_dir)))
    try:
        if stage=='write':
            fixture.save('wanted')
            fixture.save('other-school',unit='unit-b')
            fixture.save('other-year',year=2026)
            status=fixture.index.build()
            if status['status']!='ready':raise AssertionError(status)
            print(json.dumps({'stage':'write','status':status['status'],'space_id':status['active_space_id']}))
        else:
            result=fixture.index.search('跨专业',unit_id='unit-a',year=2027,limit=1,allow_remote=True)
            if not result['segments']:raise AssertionError('Persistent facade index returned no source')
            hit=result['segments'][0]
            if hit['source']['unit_id']!='unit-a' or hit['source']['year']!=2027:
                raise AssertionError('The restarted native adapter leaked another scope')
            print(json.dumps({'stage':'read','hits':len(result['segments']),'space_id':result['space_id']}))
    finally:fixture.close()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--stage',choices=['write','read'])
    parser.add_argument('--data-dir')
    args=parser.parse_args()
    project=Path(__file__).resolve().parents[4]
    if not (project/'data/runtime/node4/claude-b/report.json').is_file():
        raise SystemExit('Claude B report not submitted; native probe is not running')
    if args.stage:
        child(args.stage,args.data_dir)
        return
    with tempfile.TemporaryDirectory(prefix='n4b-native-owned-') as temporary:
        root=Path(temporary);(root/'.owned-independent-probe').write_text('temporary synthetic public data',encoding='utf-8')
        environment={**os.environ,'PYTHONPATH':str(project)+os.pathsep+os.environ.get('PYTHONPATH','')}
        for stage in ('write','read'):
            result=subprocess.run([sys.executable,'-X','utf8',str(Path(__file__).resolve()),
                                   '--stage',stage,'--data-dir',str(root/'data')],
                                  cwd=project,env=environment,capture_output=True,text=True,timeout=120)
            print(result.stdout,end='')
            if result.returncode:
                print(result.stderr,end='',file=sys.stderr)
                raise SystemExit(result.returncode)


if __name__=='__main__':main()
