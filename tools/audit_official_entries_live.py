"""Stratified fresh discovery audit. Default is an offline sample, never a paid Agent run."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import threading
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import evaluate_official_discovery as base
from tools.prepare_data import _file_digest, _is_link, _manifest, _path, _target

MAX_REPLAY_BYTES=16*1024*1024


class PublicInputs:
    """Only committed public manifest members, checked again on every read."""
    def __init__(self,root,manifest=None):
        self.root=Path(root).resolve()
        self.packages=manifest['packages'] if manifest else _manifest(self.root/'data/bundles')
        self.files={row['path']:row for package in self.packages for row in package['files']}

    def read(self,name,limit=8*1024*1024):
        parsed=_path(name)
        if parsed.parts[1].casefold() in {'runtime','bundles'}:
            raise ValueError('Runtime state and bundle containers are not public input members.')
        descriptor=self.files.get(name)
        if not descriptor or descriptor['size']>limit:raise ValueError('Missing or oversized public member: '+name)
        path=_target(self.root,name).resolve()
        if not path.is_relative_to(self.root):raise ValueError('Public input escapes its root.')
        with path.open('rb') as stream:raw=stream.read(descriptor['size']+1)
        if len(raw)!=descriptor['size'] or hashlib.sha256(raw).hexdigest()!=descriptor['sha256']:
            raise ValueError('Public member changed: '+name)
        return raw

    def json(self,name):return json.loads(self.read(name).decode('utf-8'))


def public_declarations(unit,annual_row,documents,inputs):
    from public_sources import _chsi_school_ids, _chsi_unit_codes
    identifiers={row.get('document_id') for row in annual_row.get('evidence',[])}
    selected,issues=[],[]
    for row in documents:
        url=row.get('url','')
        if row.get('id') not in identifiers or urlsplit(url).hostname!='yz.chsi.com.cn':continue
        if not (_chsi_school_ids(url)=={str(unit['id'])} or unit.get('unit_code') and
                _chsi_unit_codes(url)=={str(unit['unit_code'])}):continue
        try:
            raw=inputs.read(row['local_path'],2*1024*1024)
            if hashlib.sha256(raw).hexdigest()!=row.get('sha256'):raise ValueError('Registry SHA differs from original.')
            selected.append({**deepcopy(row),'unit_id':unit['id'],'raw_path':str((inputs.root/row['local_path']).resolve()),
                             'evidence_origin':'public_bundle'})
        except (OSError,ValueError,KeyError) as error:
            issues.append({'document_id':row.get('id'),'code':'public_original_unavailable','reason':str(error)})
        if len(selected)>=8:break
    return selected,issues


def strata(unit):
    # Sampling heuristic only: it makes no claim about official institution type.
    school = bool(re.search(r'大学|(?<!科)学院|学校|党校|职业|军医', unit.get('name','')))
    return ('registered_' if unit.get('official_url') else 'unregistered_') + ('school' if school else 'research')


def select_sample(units, annual, excluded, count=120, seed='official-entries-round2-20261109'):
    confirmed = {str(row.get('unit_id')): row for row in annual
                 if row.get('annual_status')=='confirmed' and row.get('year')==2027}
    rank = lambda row:hashlib.sha256((seed+':'+str(row['id'])).encode()).hexdigest()
    available, codes = [], set()
    for source in sorted(units,key=rank):
        row = confirmed.get(str(source['id']))
        code = str((row or {}).get('unit_code') or '')
        if row and str(source['id']) not in excluded and code and code not in codes:
            available.append(deepcopy(source));codes.add(code)
    if type(count) is not int or not 1<=count<=len(available):
        raise ValueError('Requested sample exceeds the confirmed, non-overlapping population.')
    groups = sorted({strata(row) for row in available})
    capacity = Counter(strata(row) for row in available)
    quotas = Counter()
    for _ in range(count):
        group = min((key for key in groups if quotas[key]<capacity[key]),key=lambda key:(quotas[key],key))
        quotas[group]+=1
    used, selected, identities = Counter(), [], set()
    def choose(row):
        selected.append(row);identities.add(row['id']);used[strata(row)]+=1
    provinces = {row.get('province','') for row in available}
    for province in sorted(provinces,key=lambda value:(sum(row.get('province','')==value for row in available),value)):
        choices = [row for row in available if row.get('province','')==province and used[strata(row)]<quotas[strata(row)]]
        if choices and len(selected)<count:
            choose(min(choices,key=lambda row:(used[strata(row)]/quotas[strata(row)],rank(row))))
    for row in available:
        if row['id'] not in identities and used[strata(row)]<quotas[strata(row)]:choose(row)
    if len(selected)!=count:raise ValueError('Unable to fill the stratified sample.')
    return sorted(selected,key=lambda row:(row.get('province',''),strata(row),rank(row)))


def _account(source):
    host=urlsplit(str(source.get('url') or '')).hostname or ''
    return source.get('source_role')=='official_account' or host in {'mp.weixin.qq.com','yz.chsi.com.cn'}


def phase_metrics(row,records,*,mode='live',replay_transport_calls=0):
    if mode not in {'live','replay'}:raise ValueError('Unknown measurement mode.')
    records=[item for item in records if item.get('case_id',row['case_id'])==row['case_id']]
    result=row.get('result') or {}
    entries=result.get('entry_sources') or []
    sources=[*entries,*result.get('navigation_sources',[]),*result.get('document_sources',[])]
    independent=[source for source in sources if not _account(source)
                 and source.get('authority_current') is not False]
    def proof(source):
        return (source.get('status')=='official_original_read' and bool(source.get('sha256')) and
                any(record.get('url')==source.get('url') and record.get('status')==200
                    and record.get('complete') is True and record.get('integrity_verified') is True
                    and record.get('sha256')==source['sha256'] for record in records))
    read_urls=sorted({source['url'] for source in independent if proof(source)})
    target_urls=set(row.get('metrics',{}).get('read_urls',[]))
    account_urls={source.get('url') for source in sources if _account(source)}
    return {'known_independent_entry':any(not _account(source) for source in entries),
            'official_account_known':any(_account(source) for source in entries),
            'independent_website_http_read':bool(read_urls),'independent_website_read_urls':read_urls,
            'requested_body_from_live_website':bool(target_urls.intersection(read_urls)),
            'requested_body_from_cached_account':bool(target_urls.intersection(account_urls)),
            'public_http_requests':len(records) if mode=='live' else 0,
            'recorded_response_count':len(records),
            'replay_transport_calls':replay_transport_calls if mode=='replay' else 0,
            'initial_seed_missing':not entries and not records}


def build_cases(inputs,count,seed):
    units=inputs.json('data/units.json')['units']
    annual=inputs.json('data/annual_registry_2027.json')
    previous=json.loads((base.FIXTURES/'acceptance-summary.json').read_text('utf-8'))
    excluded={row['unit_id'] for row in previous['cases']}
    selected=select_sample(units,annual['units'],excluded,count,seed)
    by_id={str(row.get('unit_id')):row for row in annual['units']}
    cases=[]
    for unit in selected:
        declarations,issues=public_declarations(unit,by_id[unit['id']],annual['documents'],inputs)
        cases.append({'case_id':'round2-'+hashlib.sha256(unit['id'].encode()).hexdigest()[:16],
            'unit':unit,'requested_year':2027,'kind':'charter','cohort':'fresh_stratified120',
            'sampling_stratum':strata(unit),'starting_condition':'registered_homepage' if unit.get('official_url')
                else 'public_bundle_declarations' if declarations else 'no_registered_url_or_public_declaration',
            'saved_declarations':declarations,'seed_issues':issues})
    return cases,excluded


def _source_hashes():
    return {**base._source_hashes(),'collectors/discovery.py':
            hashlib.sha256((ROOT/'collectors/discovery.py').read_bytes()).hexdigest(),
            str(Path(__file__).relative_to(ROOT)).replace('\\','/'):
            hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def validate_replay_records(records,directory):
    """Validate every original, including errors and cached URLs never dispatched."""
    root=Path(directory).absolute()
    if _is_link(root) or not root.is_dir():raise ValueError('Replay recordings root is missing or linked.')
    resolved_root=root.resolve()
    if not isinstance(records,list) or len(records)>20000:raise ValueError('Invalid replay record list.')
    for record in records:
        if not isinstance(record,dict):raise ValueError('Invalid replay record.')
        name=record.get('raw_path')
        if (not isinstance(name,str) or not 0<len(name)<=500 or '\\' in name or ':' in name
                or any(part in {'','.', '..'} for part in name.split('/'))):
            raise ValueError('Unsafe replay original path.')
        parts=name.split('/')
        chain=[root.joinpath(*parts[:index]) for index in range(1,len(parts)+1)]
        path=chain[-1]
        if any(_is_link(item) for item in chain) or not path.resolve().is_relative_to(resolved_root):
            raise ValueError('Replay original is linked or outside the recordings root.')
        if not path.is_file() or path.stat().st_size>MAX_REPLAY_BYTES:
            raise ValueError('Replay original is missing or oversized.')
        digest=record.get('sha256')
        if not isinstance(digest,str) or not re.fullmatch(r'[0-9a-f]{64}',digest):
            raise ValueError('Invalid replay original SHA256.')
        size,actual=_file_digest(path,MAX_REPLAY_BYTES)
        if actual!=digest:raise ValueError('Replay original SHA256 does not match.')
        # Legacy recordings may omit size; they still pass the same byte cap and SHA.
        if 'response_bytes' in record and (type(record['response_bytes']) is not int or record['response_bytes']!=size):
            raise ValueError('Replay original size does not match.')


def _load_replay(directory):
    path=Path(directory)/'recordings.json'
    if _is_link(path) or not path.is_file() or path.stat().st_size>32*1024*1024:
        raise ValueError('Replay index is missing, linked or oversized.')
    with path.open('rb') as stream:raw=stream.read(32*1024*1024+1)
    if len(raw)>32*1024*1024:raise ValueError('Replay index changed beyond its limit.')
    records=json.loads(raw.decode('utf-8'))
    validate_replay_records(records,Path(directory)/'recordings')
    return records


def _summarize(rows):
    groups={'all':rows}
    groups.update({key:[row for row in rows if row['sampling_stratum']==key]
                   for key in sorted({row['sampling_stratum'] for row in rows})})
    fields=('known_independent_entry','official_account_known','independent_website_http_read',
            'requested_body_from_live_website','requested_body_from_cached_account','initial_seed_missing')
    return {key:{'total':len(group),'phase_metrics':{name:sum(row['phases'][name] for row in group) for name in fields},
        'target_metrics':{name:sum(row['metrics'][name] for row in group) for name in base.METRICS[:-1]},
        **{name:sum(row['phases'][name] for row in group) for name in
           ('public_http_requests','recorded_response_count','replay_transport_calls')}} for key,group in groups.items()}


def main(argv=None):
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--out',type=Path,required=True)
    cli.add_argument('--public-root',type=Path,default=ROOT)
    cli.add_argument('--count',type=int,default=120)
    cli.add_argument('--seed',default='official-entries-round2-20261109')
    cli.add_argument('--workers',type=int,choices=range(1,5),default=4)
    cli.add_argument('--interval',type=float,default=2)
    mode=cli.add_mutually_exclusive_group()
    mode.add_argument('--live',action='store_true')
    mode.add_argument('--replay',type=Path)
    args=cli.parse_args(argv)
    if not 2<=args.interval<=60:cli.error('The per-host interval must be 2..60 seconds.')
    if args.replay and args.out.resolve()==args.replay.resolve():cli.error('Replay output must differ from its original recording.')
    if args.out.exists() and (not args.out.is_dir() or (args.live or args.replay) and any(args.out.iterdir())):
        cli.error('Use a new empty output directory; original audit evidence is preserved.')
    inputs=PublicInputs(args.public_root)
    cases,excluded=build_cases(inputs,args.count,args.seed)
    args.out.mkdir(exist_ok=True,parents=True)
    sample={'mode':'live' if args.live else 'replay' if args.replay else 'sample_only_no_network',
        'seed':args.seed,'requested_year':2027,'cases':cases,'excluded_previous_ids':sorted(excluded),
        'strata':dict(Counter(row['sampling_stratum'] for row in cases)),
        'province_count':len({row['unit'].get('province') for row in cases}),
        'institution_type_boundary':'Lexical sampling heuristic, not verified institution type.',
        'input_manifest_sha256':hashlib.sha256((inputs.root/'data/bundles/manifest.json').read_bytes()).hexdigest(),
        'boundary':'Production discover_official_entry navigation only; no model, Yuanbao, paid call or user database.'}
    (args.out/'sample.json').write_text(json.dumps(sample,ensure_ascii=False,indent=2),'utf-8')
    if not args.live and not args.replay:
        print(json.dumps({key:value for key,value in sample.items() if key not in {'cases','excluded_previous_ids'}},ensure_ascii=False,indent=2))
        return 0
    replay_records=_load_replay(args.replay) if args.replay else []
    pacing_lock,next_host_request=threading.Lock(),{}
    before=_source_hashes();started=base.stamp()
    def run(case):
        if args.replay:
            records=[row for row in replay_records if row['case_id']==case['case_id']]
            transport=base.ReplayTransport(records,args.replay/'recordings')
        else:
            transport=base.RecordingTransport(args.out/'recordings'/case['case_id'],args.interval)
            transport.lock,transport.next_host_request=pacing_lock,next_host_request
        row=base.evaluate_case(case,transport,replay=bool(args.replay))
        records=list(transport.records.values()) if args.replay else transport.records
        records=[{**value,'case_id':case['case_id'],'unit_id':case['unit']['id']} for value in records]
        row={**row,'sampling_stratum':case['sampling_stratum'],'registered_url_present':bool(case['unit'].get('official_url')),
             'public_bundle_declaration_count':len(case['saved_declarations']),'seed_issues':case['seed_issues'],
             'phases':phase_metrics(row,records,mode='replay' if args.replay else 'live',
                 replay_transport_calls=len(transport.calls) if args.replay else 0)}
        if not args.replay:
            records=[{**value,'raw_path':case['case_id']+'/'+value['raw_path']} for value in records]
        (args.out/(case['case_id']+'.json')).write_text(json.dumps(row,ensure_ascii=False,indent=2),'utf-8')
        print(json.dumps({'unit_id':row['unit_id'],'name':row['name'],'phases':row['phases']},ensure_ascii=False),flush=True)
        return row,records
    rows,records=[],[]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures=[pool.submit(run,case) for case in cases]
        for future in as_completed(futures):
            row,record=future.result();rows.append(row);records.extend(record)
    report={'started_at':started,'finished_at':base.stamp(),'mode':sample['mode'],'summary':_summarize(rows),
        'rows':sorted(rows,key=lambda row:row['case_id']),'source_sha256':before,'finished_source_sha256':_source_hashes(),
        'paid_calls':0,'user_databases_opened':0,'new_CHSI_requests':0,'boundary':sample['boundary']}
    report['source_changed_during_run']=report['source_sha256']!=report['finished_source_sha256']
    (args.out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    (args.out/'recordings.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(report['summary'],ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':raise SystemExit(main())
