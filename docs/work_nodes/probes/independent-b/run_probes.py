"""SHA-bound independent probes; preparing this runner performs no inference."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(project):
    paths=[*project.glob('*.py'),*(project/'tests').glob('*.py')]
    return {str(path.relative_to(project)).replace('\\','/'):digest(path) for path in sorted(paths)}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--mode',choices=['fake','query-accounting','regressions','native','local'],default='fake')
    parser.add_argument('--expected-report-sha',required=True)
    parser.add_argument('--model-dir',type=Path)
    args=parser.parse_args()
    folder=Path(__file__).resolve().parent;project=folder.parents[3]
    report=project/'data/runtime/node4/claude-b/report.json'
    if not report.is_file() or digest(report)!=args.expected_report_sha:
        raise SystemExit('Claude B report is absent or its SHA differs; no probes were started')
    before=snapshot(project);started=time.monotonic()
    output=folder/'runs'/args.expected_report_sha[:16]
    output.mkdir(parents=True,exist_ok=True)
    name={'fake':'probe_governance.py','query-accounting':'probe_query_accounting.py','regressions':'probe_regressions.py',
          'native':'probe_native.py','local':'probe_local.py'}[args.mode]
    command=[sys.executable,'-X','utf8',str(folder/name)]
    if args.mode=='local':
        command+=['--allow-local-model']
        if args.model_dir:command+=['--model-dir',str(args.model_dir)]
    environment={**os.environ,'PYTHONUTF8':'1','PYTHONPATH':str(project)+os.pathsep+str(project/'tests')}
    result=subprocess.run(command,cwd=project,env=environment,capture_output=True,text=True,timeout=300)
    (output/(args.mode+'.stdout.log')).write_text(result.stdout,encoding='utf-8')
    (output/(args.mode+'.stderr.log')).write_text(result.stderr,encoding='utf-8')
    after=snapshot(project)
    changed=sorted(key for key in before.keys()|after.keys() if before.get(key)!=after.get(key))
    payload={'mode':args.mode,'report_sha256':args.expected_report_sha,'source_test_sha256':before,
             'source_changed_during_run':changed,'exit_code':result.returncode,
             'latency_seconds':round(time.monotonic()-started,3),'stdout':args.mode+'.stdout.log',
             'stderr':args.mode+'.stderr.log','scope':'temporary databases and fake remote providers; local mode uses only prepared public files'}
    (output/(args.mode+'-report.json')).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'mode':args.mode,'exit_code':result.returncode,'changed':changed}))
    raise SystemExit(1 if changed or digest(report)!=args.expected_report_sha else result.returncode)


if __name__=='__main__':main()
