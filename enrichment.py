"""Apply reviewed, year-bound official evidence to an explicit program whitelist."""
from collections import Counter
from html import unescape
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit

from collectors.documents import decode_text
from service_verification import SourceVerifier, canonical, official_for_unit


KINDS = ('fees', 'duration', 'charter')
SELECTORS = {'department_code', 'department', 'code', 'name', 'direction', 'degree_type', 'study_mode'}


def _evidence_ok(verifier, evidence, context, documents, unit):
    if (not isinstance(evidence, dict) or not isinstance(evidence.get('document_id'), str)
            or not isinstance(evidence.get('url'), str)):
        return False
    page = evidence.get('page')
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        return False
    document = documents.get(evidence.get('document_id'))
    if (not document or not isinstance(document.get('status'), str)
            or not isinstance(document.get('content_type', ''), str)
            or not verifier.evidence_quotes(evidence, context, documents, unit, KINDS)):
        return False
    pages = verifier.pages(document, unit, KINDS)
    # SourceVerifier also tolerates the following page; enrichment requires this exact page.
    return page <= len(pages) and canonical(evidence['quote']) in canonical(pages[page - 1])


def _cohort_ok(verifier, document, context, documents, unit):
    evidence = document.get('cohort_evidence')
    return (isinstance(evidence, dict) and evidence.get('document_id') == document.get('id')
            and _evidence_ok(verifier, evidence, context, documents, unit)
            and canonical(unit['name']) in canonical(evidence['quote'])
            and set(re.findall(r'(?<!\d)20\d{2}(?!\d)', evidence['quote'])) == {str(context['year'])}
            and bool(re.search(str(context['year']) + r'(?:级|年).{0,40}(?:招生章程|学费|学制|收费)',
                               canonical(evidence['quote']))))


def _document_applies(verifier, document, context, documents, unit):
    if _cohort_ok(verifier, document, context, documents, unit):
        return True
    evidence, publication = document.get('applicability_evidence'), document.get('publication_page')
    if (not isinstance(evidence, dict) or not isinstance(evidence.get('document_id'), str)
            or not isinstance(publication, dict) or not isinstance(publication.get('content_type', ''), str)):
        return False
    charter = documents.get(evidence.get('document_id'))
    if (not charter or charter.get('kind') != 'charter'
            or not _cohort_ok(verifier, charter, context, documents, unit)
            or not _evidence_ok(verifier, evidence, context, documents, unit)
            or not official_for_unit(unit, publication.get('url'))
            or canonical(publication.get('url')) not in canonical(evidence['quote'])):
        return False
    source = verifier.read(publication)
    if not source:
        return False
    try:
        return urlsplit(document['url']).path in unescape(decode_text(source[1], publication.get('content_type', '')))
    except (ValueError, UnicodeError):
        return False


def _valid_number(value, maximum):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value <= maximum and math.isfinite(value)


def _field_ok(field, definition, verifier, context, documents, unit):
    if not isinstance(definition, dict):
        return False
    value, evidence = definition.get('value'), definition.get('evidence')
    if not isinstance(evidence, list) or not evidence:
        return False
    if not all(_evidence_ok(verifier, item, context, documents, unit)
               and _document_applies(verifier, documents[item['document_id']], context, documents, unit)
               for item in evidence):
        return False
    if field == 'tuition':
        if (not isinstance(value, dict) or set(value) != {'amount', 'period', 'currency'}
                or value.get('period') != 'year' or value.get('currency') != 'CNY'
                or not _valid_number(value.get('amount'), 10000000)):
            return False
        amount = value['amount']
        joined = canonical(' '.join(item['quote'] for item in evidence))
        if not re.search(r'元/(?:生[.·/])?(?:学年|年)|按学年缴费', joined):
            return False
    elif field == 'duration_years' and _valid_number(value, 20):
        amount = value
    else:
        return False
    number = str(int(amount)) if float(amount).is_integer() else str(amount)
    return bool(re.search(r'(?<![\d.])' + re.escape(number) + r'(?:年[。.]?)?\s*$', evidence[0]['quote']))


def _rule_targets(rule, programs, units):
    if not isinstance(rule, dict) or not isinstance(rule.get('id'), str) or not rule['id']:
        return None
    year, ids, selector = rule.get('year'), rule.get('program_ids'), rule.get('selector')
    if (not isinstance(year, int) or isinstance(year, bool) or not 2000 <= year <= 2100
            or not isinstance(rule.get('unit_id'), str) or rule['unit_id'] not in units
            or not isinstance(ids, list) or not ids
            or any(not isinstance(item, str) or not item for item in ids) or len(ids) != len(set(ids))
            or not isinstance(selector, dict) or not selector or set(selector) - SELECTORS
            or not isinstance(selector.get('study_mode'), str)
            or selector['study_mode'] not in {'全日制', '非全日制'}
            or not ('degree_type' in selector or {'code', 'department_code'} <= set(selector))):
        return None
    for value in selector.values():
        if not (isinstance(value, str) and value or isinstance(value, list) and value
                and all(isinstance(item, str) and item for item in value)):
            return None
    selected = [program for program in programs if program.get('id') in ids]
    if len(selected) != len(ids) or len({program.get('id') for program in selected}) != len(ids):
        return None
    if any(program.get('unit_id') != rule['unit_id'] or program.get('year') != year
           or any(program.get(key) not in value if isinstance(value, list) else program.get(key) != value
                  for key, value in selector.items()) for program in selected):
        return None
    fields = rule.get('fields')
    return selected if isinstance(fields, dict) and fields and not set(fields) - {'tuition', 'duration_years'} else None


def _report(admissions, documents, programs, invalid, applied, changed):
    return {**admissions, 'documents': documents, 'programs': programs,
            'metadata': {**admissions.get('metadata', {}), 'enrichment': {
                'applied_count': len(changed), 'invalid_rule_ids': list(dict.fromkeys(invalid)),
                'applied_rule_ids': applied,
                'notice': '仅补齐所列年度白名单的费用和学制，招生资格与下一年度政策仍需核验。'}}}


def apply_enrichment(registry, admissions, data_dir):
    """Return new admission data; missing overlays leave the original object intact."""
    path = Path(data_dir) / 'program_enrichment.json'
    if not path.exists():
        return admissions
    originals, programs = admissions.get('documents', []), admissions.get('programs', [])
    try:
        overlay = json.loads(path.read_text('utf-8-sig'), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (OSError, ValueError, UnicodeError):
        return _report(admissions, originals, programs, ['overlay'], [], set())
    if not isinstance(overlay, dict):
        return _report(admissions, originals, programs, ['overlay'], [], set())
    extra = overlay.get('documents', [])
    extra = [item for item in extra if isinstance(item, dict)] if isinstance(extra, list) else []
    # Keep even failed source dependencies so restoring a source invalidates the snapshot cache.
    merged = [*originals, *[document for document in extra if document not in originals]]
    rules = overlay.get('rules')
    if overlay.get('schema_version') != 1 or not isinstance(rules, list):
        return _report(admissions, merged, programs, ['overlay'], [], set())
    counts = Counter(document.get('id') for document in merged if isinstance(document.get('id'), str))
    documents = {document['id']: document for document in merged
                 if isinstance(document.get('id'), str) and counts[document['id']] == 1}
    units = {unit['id']: unit for unit in registry.get('units', []) if isinstance(unit.get('id'), str)}
    verifier, invalid, applied, changed = SourceVerifier(data_dir), [], [], set()
    rule_counts = Counter(rule.get('id') for rule in rules if isinstance(rule, dict) and isinstance(rule.get('id'), str))
    for index, rule in enumerate(rules):
        identifier = rule.get('id') if isinstance(rule, dict) and isinstance(rule.get('id'), str) else f'rule-{index}'
        targets = _rule_targets(rule, programs, units)
        if targets is None or rule_counts[identifier] != 1:
            invalid.append(identifier)
            continue
        context = {'unit_id': rule['unit_id'], 'year': rule['year']}
        valid = {field: definition for field, definition in rule['fields'].items()
                 if _field_ok(field, definition, verifier, context, documents, units[rule['unit_id']])}
        if len(valid) != len(rule['fields']):
            invalid.append(identifier)
        updated = []
        for program in programs:
            updates, evidence = {}, []
            if program.get('id') in rule['program_ids']:
                for field, definition in valid.items():
                    if program.get(field) is not None:
                        continue
                    proof = [dict(item) for item in definition['evidence']]
                    updates = {**updates, field: {**definition['value'], 'evidence': proof} if field == 'tuition' else definition['value']}
                    if field == 'duration_years':
                        updates = {**updates, 'duration_evidence': proof}
                    evidence.extend(proof)
            if updates:
                changed.add(program['id'])
                updates = {**updates, 'enrichment_evidence': [*program.get('enrichment_evidence', []), *evidence]}
            updated.append({**program, **updates} if updates else program)
        if updated != programs:
            applied.append(identifier)
        programs = updated
    return _report(admissions, merged, programs, invalid, applied, changed)
