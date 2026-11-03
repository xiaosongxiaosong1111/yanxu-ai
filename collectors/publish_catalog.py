"""Explicitly replay saved evidence and publish a local, read-only query revision."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from catalog_index import CatalogIndex, CatalogIndexError, SCHEMA_VERSION
from service import AdmissionService, ServiceError
from .annual_registry import verify_annual_registry
from .catalog_semantic_audit import check_semantic_audit, MAX_AUDIT_BYTES
from .documents import PROJECT_ROOT


class PublicationError(ValueError):
    pass


def _checked_revision(value):
    digest=hashlib.sha256()
    encoder=json.JSONEncoder(ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':'))
    for chunk in encoder.iterencode(value):digest.update(chunk.encode())
    return digest.hexdigest()[:24]


def _audit_digest(root):
    path=root/'data/catalog_semantic_audits.json'
    if not path.exists():
        return None
    try:
        if not path.resolve().is_relative_to(root) or not path.is_file() or not 0<path.stat().st_size<=MAX_AUDIT_BYTES:
            raise ValueError('audit_path')
        with path.open('rb') as stream: raw=stream.read(MAX_AUDIT_BYTES+1)
        if len(raw)>MAX_AUDIT_BYTES:
            raise ValueError('audit_size')
        return hashlib.sha256(raw).hexdigest()
    except (OSError,ValueError) as error:
        raise PublicationError('根语义审核文件不可安全读取，保留上次发布版本。') from error


def _document_refs(value):
    if isinstance(value,dict):
        if isinstance(value.get('document_id'),str): yield value['document_id']
        if isinstance(value.get('catalog_document_id'),str): yield value['catalog_document_id']
        for child in value.values(): yield from _document_refs(child)
    elif isinstance(value,list):
        for child in value: yield from _document_refs(child)


def _pdf_semantics(service,registry,admissions,data):
    root=data.parent
    before=_audit_digest(root)
    referenced=set(_document_refs(admissions['programs']))
    documents={row['id']:row for row in admissions['documents']}
    units={row['id']:row for row in registry['units']}
    selected=[doc for identifier,doc in documents.items() if identifier in referenced
              and doc.get('kind')=='catalog' and doc.get('catalog_active',True)
              and str(doc.get('catalog_parser','')).lower().startswith('pdf_table')]
    for document in selected:
        unit=units.get(document.get('unit_id'))
        parsed=service.verifier._source_programs(document,unit,document.get('year'),{},documents) if unit else None
        if parsed is None or not check_semantic_audit(document,parsed['programs'],root):
            raise PublicationError('被采用的PDF表格来源缺少当前原文、适配器及整源记录集的独立语义批准：'+document['id']+'；保留上次发布版本。')
    if before!=_audit_digest(root):
        raise PublicationError('核验期间根语义审核文件发生变化，保留上次发布版本，请重试。')
    return before,len(selected)


def _annual_scope(registry,year,data):
    declared=registry['metadata'].get('annual_scope',{})
    if not isinstance(declared,dict):
        raise PublicationError('年度单位范围声明格式不正确，未发布。')
    if declared.get('status')!='verified':
        return None,None
    if type(year) is not int or declared.get('year')!=year:
        raise PublicationError('年度单位范围与目录参考年不一致，未发布。')
    path=data/f'annual_registry_{year}.json'
    try:
        if not 0<path.stat().st_size<=32*1024*1024:
            raise ValueError('size')
        raw=path.read_bytes()
        annual=json.loads(raw.decode('utf-8-sig'))
        metadata=annual['metadata']
        if (metadata.get('year')!=year or metadata.get('complete') is not True
                or metadata.get('annual_scope_status')!='verified'
                or metadata.get('unconfirmed_index_units')!=0):
            raise ValueError('incomplete')
        for field in ('official_total','confirmed_units','not_listed_index_records'):
            if (type(metadata.get(field)) is not int or type(declared.get(field)) is not int
                    or metadata[field]<0 or declared[field]!=metadata[field]):
                raise ValueError('count')
        records={row['unit_id']:row for row in annual['units']}
        if (len(records)!=len(annual['units']) or set(records)!={row['id'] for row in registry['units']}
                or sum(row.get('annual_status')=='confirmed' for row in records.values())!=metadata['official_total']
                or metadata['confirmed_units']!=metadata['official_total']
                or sum(row.get('annual_status')=='not_listed' for row in records.values())!=metadata['not_listed_index_records']):
            raise ValueError('identities')
        if any(row.get('annual_year')!=year or row.get('annual_status')!=records[row['id']]['annual_status']
               for row in registry['units']):
            raise ValueError('unit_flags')
        if not verify_annual_registry(annual,registry,project_root=data.parent):
            raise ValueError('originals')
    except (OSError,ValueError,TypeError,KeyError,AttributeError) as error:
        raise PublicationError('已声明核准的年度单位名单缺失、声明不符或原文重放失败；保留上次发布版本。') from error
    return annual,hashlib.sha256(raw).hexdigest()


def _annual_coverage(coverage,annual):
    if annual is None:
        return {**coverage,'catalog_complete':False,'release_ready':False,'annual_scope_status':'unverified'}
    excluded=[row['unit_id'] for row in annual['units'] if row['annual_status']=='not_listed']
    missing=[identity for identity in coverage.get('missing_unit_ids',[]) if identity not in excluded]
    details=[{**row,'status':'not_listed','verified':False,'annual_not_listed':True,
              'notes':'未列入该年度独立硕士招生单位名单，不据此推断停招。'} if row['unit_id'] in excluded else row
             for row in coverage.get('unit_catalog_status',[])]
    return {**coverage,'annual_total':annual['metadata']['official_total'],'annual_scope_status':'verified',
            'annual_not_listed_unit_ids':excluded,'missing_unit_ids':missing,'missing_units_count':len(missing),
            'verified_unit_ids':[identity for identity in coverage.get('verified_unit_ids',[]) if identity not in excluded],
            'unit_catalog_status':details}


def publish(data_dir=PROJECT_ROOT/'data'):
    data=Path(data_dir).resolve()
    if not all((data/name).is_file() for name in ('units.json','admissions.json')):
        raise PublicationError('请指定包含 units.json 与 admissions.json 的资料目录。')
    target=data/'runtime/catalog-index.sqlite'
    if not target.resolve().is_relative_to(data):
        raise PublicationError('发布索引路径超出本次指定资料目录，未写入。')
    service=AdmissionService(data,search_api_key='')
    registry,admissions,coverage,revision=service._state()
    annual,annual_digest=_annual_scope(registry,admissions['metadata'].get('reference_year',2027),data)
    audit_digest,pdf_sources=_pdf_semantics(service,registry,admissions,data)
    coverage=_annual_coverage(coverage,annual)
    documents={row['id']:row for row in admissions['documents']}
    units={row['id']:row for row in registry['units']}
    invalid=[row['id'] for row in admissions['programs'] if row.get('unit_id') not in units
             or not service.verifier.program(row,documents,units[row['unit_id']])]
    conflicts=sum(len(row.get('evidence_conflicts') or {}) for row in admissions['programs'])
    flags=[flag for condition,flag in ((not coverage.get('registry_complete'),'registry_incomplete'),
        (not coverage.get('catalog_complete'),'catalog_partial'),(bool(invalid),'invalid_program_originals'),
        (bool(conflicts),'conflicting_fields_suspended'),(annual is None,'annual_scope_unverified')) if condition]
    coverage={**coverage,'invalid_program_count':len(invalid),'invalid_program_ids':invalid,
              'suspended_field_count':conflicts,'publication_flags':flags}
    if not service._cache.status().get('data_revision') or audit_digest!=_audit_digest(data.parent):
        raise PublicationError('核验期间资料或根语义审核文件发生变化，保留上次发布版本，请待采集或导入结束后重试。')
    # A stricter parser can change trust without changing any saved source bytes.
    checked={'catalog_schema':SCHEMA_VERSION,'source_revision':revision,'annual_digest':annual_digest,'semantic_audit_digest':audit_digest,
             'registry':registry,'admissions':admissions,'coverage':coverage}
    revision=_checked_revision(checked)
    result=CatalogIndex(target).sync(registry,admissions,coverage,revision,verifier=service.verifier)
    return {**result,'document_count':len(documents),'invalid_program_count':len(invalid),
        'invalid_program_ids':invalid,'invalid_flags':flags,'suspended_field_count':conflicts,
        'verified_catalog_units':len(coverage.get('verified_unit_ids',[])),
        'catalog_complete':coverage.get('catalog_complete') is True,
        'annual_status':'verified' if annual else 'unverified','annual_total':coverage.get('annual_total'),
        'verification_mode':'originals_replayed','pdf_semantic_sources':pdf_sources}


def main(argv=None):
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--data-dir',type=Path,default=PROJECT_ROOT/'data')
    args=cli.parse_args(argv)
    try:
        result=publish(args.data_dir)
    except (PublicationError,ServiceError,CatalogIndexError,OSError) as error:
        print(json.dumps({'error':str(error),'published':False},ensure_ascii=False),file=sys.stderr)
        return 1
    print(json.dumps(result,ensure_ascii=False),flush=True)
    return 0


if __name__=='__main__': raise SystemExit(main())
