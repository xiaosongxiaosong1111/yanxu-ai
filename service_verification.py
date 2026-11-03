"""Release checks derive their evidence from saved source bytes, not counters."""

from html import unescape
from html.parser import HTMLParser
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import stat
import threading
import unicodedata
from urllib.parse import parse_qs, urlsplit


PUBLIC_CATALOG_ADAPTERS=frozenset({'public_form_v1','public_aspx_v1','university_form_v1','ucas_public_v1','nuaa_public_v1'})
STATIC_CATALOG_ADAPTERS=frozenset({'office_national_v1','html_structured_v1','office_structured_v1',
    'html_national_v1','html_south_v1','pdf_table_national_v1','pdf_table_national_v2','pdf_table_hit_v1'})
CATALOG_ADAPTERS = {'html_table_v1', 'pdf_row_v1', 'pdf_table_v1', 'office_table_v1',
                    'zsml_table', 'zsml_tree', 'catalog_index_v1', 'chsi_detail_v1', 'pdf_table_v2',
                    *STATIC_CATALOG_ADAPTERS,*PUBLIC_CATALOG_ADAPTERS}
ZSML_ROUTE = r'[0-9A-Z]{6}(?:_\d+)?'
CACHE_LIMITS={'_bytes':(128,64*1024*1024),'_texts':(64,8*1024*1024),
              '_normalized':(64,8*1024*1024),'_parsed':(16,None)}


def canonical(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', unescape(str(value)))).casefold()


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}: self.skip += 1

    def handle_endtag(self, tag):
        if tag in {'script', 'style'} and self.skip: self.skip -= 1

    def handle_data(self, value):
        if not self.skip: self.parts.append(value)


def plain_text(value):
    if not re.search(r'<[a-zA-Z][^>]*>', value):
        return unescape(value)
    parser = VisibleText()
    parser.feed(value)
    return ' '.join(parser.parts)


def domain(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
            return None
        parts = parsed.hostname.lower().rstrip('.').split('.')
        return '.'.join(parts[-3:]) if len(parts) >= 3 and parts[-2:] in [['edu', 'cn'], ['ac', 'cn'], ['gov', 'cn'], ['org', 'cn']] else parsed.hostname.removeprefix('www.')
    except (TypeError, ValueError, AttributeError):
        return None


def official_for_unit(unit, url):
    root = domain(unit.get('official_url'))
    try:
        parsed = urlsplit(url)
        if parsed.hostname == 'yz.chsi.com.cn':
            from public_sources import official_source_url
            return bool(domain(url)) and official_source_url(url, unit)
        return bool(domain(url)) and bool(root) and (parsed.hostname == root or parsed.hostname.endswith('.' + root))
    except (TypeError, ValueError):
        return False


def exam_groups(text):
    value = plain_text(text)
    value = re.sub(r'考试方式\s*院系所\s*专业\s*研究方向\s*学习方式\s*专业研究方向备注\s*考试科目', '', value)
    marks = list(re.finditer(r'([①②③④])\s*', value))
    result = {}
    for index, mark in enumerate(marks):
        slot = '①②③④'.index(mark.group(1)) + 1
        segment = value[mark.end():marks[index + 1].start() if index + 1 < len(marks) else len(value)]
        codes = list(re.finditer(r'(?<!\d)([1-9]\d{2})(?!\d)', segment))
        for position, code in enumerate(codes):
            name = segment[code.end():codes[position + 1].start() if position + 1 < len(codes) else len(segment)]
            name = re.sub(r'^\s*[-－—]\s*', '', name, count=1)
            name = re.sub(r'(?:或者|或|/|、|,|，|;|；|\s)+$', '', name.strip())
            pair = (code.group(1), canonical(name))
            result.setdefault(slot, set()).add(pair)
    return result


def html_catalog_year(raw, year):
    titles = re.findall(r'<[^>]+class=["\'][^"\']*sch-sspe-swtit[^"\']*["\'][^>]*>(.*?)</[^>]+>', raw, re.S | re.I)
    if not titles:
        titles = re.findall(r'<(?:h[1-6]|title)\b[^>]*>(.*?)</(?:h[1-6]|title)>', raw, re.S | re.I)
    for title in titles:
        text = plain_text(title)
        years = re.findall(r'(?<!\d)(20\d{2})(?!\d)', text)
        if years and re.search(r'硕士|招生|专业目录', text):
            return str(year) in years
    text = canonical(plain_text(raw))
    return bool(re.search(re.escape(str(year)) + r'年.{0,24}(?:硕士|研究生|招生)', text))


def citation_signature(evidence):
    items = [evidence] if isinstance(evidence, dict) else evidence
    if not isinstance(items, list) or not items: return None
    result = set()
    for item in items:
        if not isinstance(item, dict) or any(not isinstance(item.get(field), str) or not item[field].strip()
                for field in ('document_id', 'url')): return None
        page = item.get('page')
        if page is not None and (not isinstance(page, int) or isinstance(page, bool) or page < 1): return None
        if item.get('kind') == 'structured_fields':
            if ('quote' in item or page is not None or not isinstance(item.get('field_path'), str)
                    or not item['field_path'] or not isinstance(item.get('structured_fields'), dict)): return None
            try:
                payload = json.dumps(item['structured_fields'], ensure_ascii=False, sort_keys=True, allow_nan=False)
            except (ValueError, TypeError): return None
            result.add((item['document_id'], item['url'], None, 'structured_fields', item['field_path'], payload))
        else:
            if not isinstance(item.get('quote'), str) or not item['quote'].strip(): return None
            result.add((item['document_id'], item['url'], page, canonical(plain_text(item['quote']))))
    return frozenset(result)


def ordinary_catalog_scope(text, *, html=False):
    """Only an original main title can classify an entire source as a special admission route."""
    title_pattern = r'(?:招生.*(?:目录|简章|章程)|专业目录)'
    if html:
        article = [match['body'] for match in re.finditer(
            r'<(?P<tag>span|div|h[1-6]|p)\b[^>]*class=["\'][^"\']*(?:sch-sspe-swtit|article[-_]title|content[-_]title|news[-_]title|art[-_]title)[^"\']*["\'][^>]*>(?P<body>.*?)</(?P=tag)>',
            text, re.I | re.S)]
        headings = re.findall(r'<h[1-6]\b[^>]*>(.*?)</h[1-6]>', text, re.I | re.S)
        browser_titles = re.findall(r'<title\b[^>]*>(.*?)</title>', text, re.I | re.S)
        prefix = re.split(r'<table\b', text, maxsplit=1, flags=re.I)[0]
        paragraphs = re.findall(r'<p\b[^>]*>(.*?)</p>', prefix, re.I | re.S)
        rows = [row for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>', text, re.I | re.S)[:3]
                if not re.search(r'<(?:a|table)\b', row, re.I) and len(canonical(plain_text(row))) <= 300]
        candidates = article + headings + browser_titles + paragraphs + rows
        # Portal navigation and unrelated headings are separate blocks, never title fragments.
        title = next((canonical(plain_text(value)) for value in candidates
                      if re.search(title_pattern, canonical(plain_text(value)))), '')
    else:
        candidates = text.splitlines()[:8]
        title, combined = '', ''
        for candidate in candidates:
            value = canonical(plain_text(candidate))
            combined += value
            if re.search(title_pattern, value) or re.search(title_pattern, combined):
                title = combined
                break
    title = re.sub(r'[（(][^）)]*(?:不含|不包括|不招收|不接收|包含|含|统考)[^）)]*[）)]', '', title)
    excluded = (r'单独考试|推荐免试|(?:仅|只)(?:接收|招收)?推免|推免.{0,24}(?:目录|简章|章程)'
                r'|博士(?!点).{0,24}(?:招生|专业目录|简章|章程)|港澳台|港澳|台湾|留学生|国际学生')
    return not bool(title and re.search(excluded, title))


class SourceVerifier:
    def __init__(self, data_dir):
        self.root = Path(data_dir).resolve()
        self._bytes, self._texts, self._parsed = {}, {}, {}
        self._trees = {}
        self._tree_route_codes = {}
        self._normalized = {}
        self._lock = threading.Lock()

    def _remember(self, bucket, key, value):
        count,budget=CACHE_LIMITS[bucket]
        def weight(item):
            identity,result=item
            if bucket=='_bytes':return len(result)
            if bucket=='_texts':return sum(len(page) for page in result)
            if bucket=='_normalized':return len(result)+sum(len(page) for page in identity)
            return 0
        incoming=(key,value)
        if budget is not None and weight(incoming)>budget:return
        with self._lock:
            items=[item for item in getattr(self,bucket).items() if item[0]!=key]+[incoming]
            total=sum(weight(item) for item in items)
            while len(items)>count or budget is not None and total>budget:
                total-=weight(items[0]);items=items[1:]
            setattr(self,bucket,dict(items))

    def read(self, record, key='local_path'):
        raw_path, digest = record.get(key), record.get('sha256')
        if not isinstance(raw_path, str) or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            return None
        path = Path(raw_path)
        if not path.is_absolute(): path = (self.root.parent if path.parts and path.parts[0] == self.root.name else self.root) / path
        try:
            path = path.resolve()
            if not path.is_relative_to(self.root): return None
            file_stat = path.stat()
            if not stat.S_ISREG(file_stat.st_mode): return None
            fingerprint = (str(path), file_stat.st_mtime_ns, file_stat.st_size, digest)
            with self._lock: raw = self._bytes.get(fingerprint)
            if raw is None:
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != digest: return None
                self._remember('_bytes',fingerprint,raw)
            return path, raw
        except OSError:
            return None

    def pages(self, document, unit, kinds=('catalog',)):
        if not self._document_allowed(document, unit, kinds): return []
        source = self.read(document)
        if not source: return []
        return self._source_pages(document, source)

    def _chsi_proof(self, document, unit, year):
        from collectors.chsi_public_proof import verify_public_detail
        return verify_public_detail(document, unit, year, project_root=self.root)

    def document_allowed(self, document, unit, kinds=('catalog',)):
        return self._document_allowed(document, unit, kinds)

    def _document_allowed(self, document, unit, kinds=('catalog',)):
        if document.get('kind') not in kinds or document.get('status') not in {'parsed', 'verified'}: return False
        if document.get('catalog_parser') == 'chsi_detail_v1':
            try:
                return self._chsi_proof(document, unit, document.get('year')).get('verified') is True
            except (ValueError, OSError, ImportError, TypeError): return False
        return official_for_unit(unit, document.get('url'))

    def _chsi_programs(self, document, unit, year):
        if (document.get('unit_id') != unit.get('id') or document.get('year') != year
                or type(year) is not int or document.get('status') not in {'parsed', 'verified'}
                or document.get('kind') != 'catalog' or not document.get('catalog_active', True)): return None
        try:
            proof = self._chsi_proof(document, unit, year)
            source = self.read(document)
            if proof.get('verified') is not True or not source: return None
            key = ('chsi_detail_v1', str(source[0]), document['sha256'], document.get('id'),
                   document.get('url'), document.get('method', 'GET'), unit.get('id'), year, proof['signature'])
            with self._lock: cached = self._parsed.get(key)
            if cached is not None: return cached
            from collectors.catalog_chsi import parse_chsi_detail_html
            from collectors.documents import decode_text
            result = parse_chsi_detail_html(decode_text(source[1], document.get('content_type', '')),
                unit, year, document, context_html=proof['context_html'])
            result = {**result, 'documents': proof.get('documents', [])}
        except (ValueError, OSError, ImportError, TypeError, KeyError): return None
        self._remember('_parsed',key,result)
        return result

    def _context_stamp(self, document, documents):
        fields=('learning_mode_document','publication_page','public_form_context','public_form_profile','public_aspx_page',
                'request_method','request_body','request_sha256','request_document')
        metadata={key:document[key] for key in fields if key in document}
        if not metadata: return ''
        metadata={**metadata,'original':{key:document.get(key) for key in
            ('id','local_path','sha256','url','requested_url','content_type','unit_id','year','kind','status','catalog_parser','catalog_active')}}
        records={}
        def visit(value):
            if isinstance(value,dict):
                if isinstance(value.get('local_path'),str) and isinstance(value.get('sha256'),str):
                    records[value.get('id') or value['local_path']]=value
                for item in value.values(): visit(item)
            elif isinstance(value,list):
                for item in value: visit(item)
            elif isinstance(value,str) and value in documents and value not in records:
                if len(records)>=4096: raise ValueError('目录上下文超过核验上限')
                records[value]=documents[value]
                visit({key:documents[value][key] for key in fields if key in documents[value]})
        visit(metadata)
        fingerprints=[]
        for identity,record in sorted(records.items()):
            source=self.read(record)
            digest=None
            if source:
                try:
                    hasher=hashlib.sha256();size=0;limit=32*1024*1024
                    with source[0].open('rb') as stream:
                        while chunk:=stream.read(min(65536,limit+1-size)):
                            size+=len(chunk)
                            if size>limit:break
                            hasher.update(chunk)
                    if 0<size<=limit:digest=hasher.hexdigest()
                except OSError: pass
            fingerprints.append((identity,record,digest))
            if digest!=record.get('sha256'):return None
        return hashlib.sha256(json.dumps([metadata,fingerprints],ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()

    def _public_programs(self, document, unit, year, documents):
        if (not isinstance(documents,dict) or document.get('unit_id')!=unit.get('id') or document.get('year')!=year
                or type(year) is not int or not self._document_allowed(document,unit)
                or not document.get('catalog_active',True)): return None
        source=self.read(document)
        if not source: return None
        try:
            stamp=self._context_stamp(document,documents)
            if stamp is None:return None
            unit_identity=hashlib.sha256(json.dumps(unit,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()
            key=(document.get('catalog_parser'),str(source[0]),document['sha256'],unit_identity,year,stamp)
            with self._lock: cached=self._parsed.get(key)
            if cached is not None: return cached
            from collectors.catalog_public_forms import parse_public_form_html
            from collectors.documents import decode_text
            project=self.root.parent if self.root.name=='data' else self.root
            parser=parse_public_form_html
            if document.get('catalog_parser')=='public_aspx_v1':
                from collectors.catalog_public_aspx import parse_public_aspx_html
                parser=parse_public_aspx_html
            elif document.get('catalog_parser')=='nuaa_public_v1':
                from collectors.catalog_nuaa_public import parse_nuaa_public
                parser=parse_nuaa_public
            elif document.get('catalog_parser')=='ucas_public_v1':
                from collectors.catalog_ucas import parse_ucas_public
                parser=parse_ucas_public
            elif document.get('catalog_parser')=='university_form_v1':
                from collectors.catalog_university_forms import parse_university_form
                parser=parse_university_form
            result=parser(decode_text(source[1],document.get('content_type','')),
                unit,year,document,documents=documents,project_root=project)
        except (ValueError,OSError,ImportError,TypeError,KeyError): return None
        self._remember('_parsed',key,result)
        return result

    def structured_evidence(self, item, program, documents, unit):
        """Verify a typed original field reference without manufacturing a prose quotation."""
        signature = citation_signature(item)
        document = documents.get(item.get('document_id')) if isinstance(item, dict) else None
        if (not signature or item.get('kind') != 'structured_fields' or not document
                or document.get('url') != item.get('url')): return False
        primary=documents.get(program.get('catalog_document_id'),document)
        if primary.get('catalog_parser') == 'chsi_detail_v1':
            parsed=self._chsi_programs(primary,unit,program.get('year'))
        elif primary.get('catalog_parser') in PUBLIC_CATALOG_ADAPTERS:
            parsed=self._source_programs(primary,unit,program.get('year'),{},documents)
        else: return False
        rows = [row for row in (parsed or {}).get('programs', [])
                if row.get('id') == program.get('id') and self._signature(row) == self._signature(program)]
        if len(rows) != 1: return False
        def references(value):
            if isinstance(value, dict):
                if value.get('kind') == 'structured_fields': yield value
                else:
                    for child in value.values(): yield from references(child)
            elif isinstance(value, list):
                for child in value: yield from references(child)
        return any(citation_signature(ref) == signature for ref in references(rows[0]))

    def _source_pages(self, document, source):
        path, raw = source
        key = (str(path), document['sha256'], document.get('content_type', ''), document.get('url'), document.get('catalog_parser'))
        with self._lock: pages = self._texts.get(key)
        if pages is not None: return pages
        try:
            from collectors.documents import decode_text, extract_pdf_text
            if raw.startswith(b'%PDF-'):
                text = extract_pdf_text(path)
                pages = text.rstrip('\f\r\n ').split('\f')
            elif document.get('catalog_parser') in {'office_table_v1','office_national_v1','office_structured_v1'}:
                from collectors.catalog_office import extract_office_html
                pages = [plain_text(extract_office_html(raw))]
            else:
                from public_sources import rendered_source_html
                text = decode_text(raw, document.get('content_type', ''))
                if path.suffix == '.json':
                    text = json.dumps(json.loads(text), ensure_ascii=False)
                else:
                    text = rendered_source_html(text, document['url'])
                pages = [plain_text(text)]
        except (ValueError, OSError, ImportError):
            return []
        self._remember('_texts',key,pages)
        return pages

    def evidence_quotes(self, evidence, program, documents, unit, kinds=('catalog',)):
        items = [evidence] if isinstance(evidence, dict) else evidence
        if not isinstance(items, list): return []
        result, originals = [], {}
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get('quote'), str) or not item['quote'].strip(): continue
            document = documents.get(item.get('document_id'))
            if not document or document.get('unit_id') != program.get('unit_id') or document.get('year') != program.get('year') or document.get('url') != item.get('url'): continue
            identity = item.get('document_id')
            if identity not in originals:
                originals[identity] = self.pages(document, unit, kinds)
            pages = originals[identity]
            page = item.get('page')
            if page is not None:
                if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= len(pages): continue
                pages = pages[page - 1:page + 1]
            quote = plain_text(item['quote'])
            key = tuple(pages)
            with self._lock: normalized = self._normalized.get(key)
            if normalized is None:
                normalized = canonical(' '.join(pages))
                self._remember('_normalized',key,normalized)
            if canonical(quote) and canonical(quote) in normalized:
                result.append(quote)
        return result

    def program(self, program, documents, unit):
        required = ('id', 'unit_id', 'code', 'name', 'study_mode')
        if any(not isinstance(program.get(field), str) or not program[field].strip() for field in required): return False
        items = program.get('evidence')
        items = [items] if isinstance(items, dict) else items
        explicit = {item.get('document_id') for item in items or [] if isinstance(item, dict)
                    and documents.get(item.get('document_id'), {}).get('catalog_parser') in CATALOG_ADAPTERS}
        if any(documents[identity].get('catalog_parser') in {'chsi_detail_v1',*PUBLIC_CATALOG_ADAPTERS} for identity in explicit):
            return self._program_replays(program, documents, unit, explicit)
        unit_scope = program.get('department_scope') == 'unit' and program.get('department') == ''
        if not unit_scope and (not isinstance(program.get('department'), str) or not program['department'].strip()): return False
        if unit_scope and not explicit: return False
        program_scope = program.get('direction_scope') == 'program' and program.get('direction') == ''
        if not program_scope and (not isinstance(program.get('direction'), str) or not program['direction'].strip()): return False
        if program_scope and not explicit: return False
        quotes = self.evidence_quotes(program.get('evidence'), program, documents, unit)
        joined = canonical(' '.join(quotes))
        if not joined: return False
        if not explicit:
            if any(canonical(program[field]) not in joined for field in ('code', 'name', 'study_mode')): return False
            direction = canonical(program['direction'])
            direction_pattern = re.escape(direction[:2]) + r'(?:\((?:1|2|全日制|非全日制)\))?' + re.escape(direction[2:])
            if not re.search(direction_pattern, joined): return False
        subjects = program.get('subjects')
        if not isinstance(subjects, list) or not subjects or any(not isinstance(subject, dict) for subject in subjects): return False
        slots = [subject.get('exam_unit') for subject in subjects]
        if any(not isinstance(slot, int) or isinstance(slot, bool) for slot in slots): return False
        distinct = set(slots)
        if len(distinct) not in {2, 3, 4} or distinct != set(range(1, len(distinct) + 1)): return False
        used_groups = set()
        for slot in distinct:
            alternatives = [subject for subject in subjects if subject['exam_unit'] == slot]
            if len(alternatives) < 2: continue
            groups = [subject.get('choice_group') for subject in alternatives]
            if any(not isinstance(group, str) or not group.strip() for group in groups) or len(set(groups)) != 1 or groups[0] in used_groups: return False
            used_groups.add(groups[0])
        identities, actual = set(), {}
        for subject in subjects:
            if any(not isinstance(subject.get(field), str) or not subject[field].strip() for field in ('code', 'name', 'scope')): return False
            if subject['scope'] not in {'national', program['unit_id']}: return False
            identity = (subject['exam_unit'], subject['code'], subject['scope'])
            if identity in identities: return False
            identities.add(identity)
            actual.setdefault(subject['exam_unit'], set()).add((subject['code'], canonical(subject['name'])))
        if explicit:
            return self._program_replays(program, documents, unit, explicit)
        exam_quotes = self.evidence_quotes(program.get('exam_evidence'), program, documents, unit) or quotes
        expected = {}
        for quote in exam_quotes:
            for slot, values in exam_groups(quote).items(): expected.setdefault(slot, set()).update(values)
        return actual == expected

    def registry(self, registry):
        meta, units = registry['metadata'], registry['units']
        expected = meta.get('expected_pages')
        if not units or meta.get('complete') is not True or not isinstance(expected, int) or isinstance(expected, bool) or expected <= 0: return False
        pages = meta.get('pages')
        if not isinstance(pages, list) or len(pages) != expected or meta.get('failed_pages') or meta.get('invalid_pages') or meta.get('duplicate_ids'): return False
        fetched = meta.get('fetched_pages')
        if not isinstance(fetched, list) or any(not isinstance(page, int) or isinstance(page, bool) for page in fetched) or set(fetched) != set(range(1, expected + 1)): return False
        records, seen_pages = [], set()
        for page in pages:
            if not isinstance(page, dict) or not isinstance(page.get('page'), int) or isinstance(page['page'], bool) or page['page'] in seen_pages or not isinstance(page.get('url'), str) or urlsplit(page['url']).hostname != 'yz.chsi.com.cn': return False
            seen_pages.add(page.get('page'))
            source = self.read(page, 'snapshot_path')
            if not source: return False
            try:
                from collectors.registry import parse_registry_page, decode_registry_html
                found, pagination = parse_registry_page(decode_registry_html(source[1]), page['url'])
            except (ValueError, UnicodeError): return False
            if pagination['invalid_items'] or pagination['current_page'] != page['page'] or pagination['total_pages'] != expected or len(found) != page.get('record_count'): return False
            records.extend(found)
        fields = ('id', 'name', 'province', 'unit_code', 'source_url')
        signature = lambda unit: tuple(unit.get(field) for field in fields)
        ids = [record['id'] for record in records]
        return len(ids) == len(set(ids)) == len(units) == meta.get('unit_count') and Counter(map(signature, records)) == Counter(map(signature, units)) and seen_pages == set(range(1, expected + 1))

    def not_applicable(self, status, documents, unit, year):
        quotes = self.evidence_quotes(status.get('evidence'), {'unit_id': unit.get('id'), 'year': year}, documents, unit,
                                      ('catalog', 'charter', 'notice'))
        # Only an explicit institution-wide, year-bound statement can remove a unit.
        # Department/program restrictions remain missing data, even when quoted accurately.
        subject = re.escape(canonical(unit.get('name'))) + str(year) + '年'
        statements = r'(?:不招收硕士研究生|不开展硕士研究生招生|无硕士研究生招生计划|仅招收博士研究生)'
        return any(re.fullmatch(subject + statements + r'[。.!！]?', canonical(quote)) for quote in quotes)

    @staticmethod
    def _signature(program):
        subjects = program.get('subjects')
        if not isinstance(subjects, list) or any(not isinstance(subject, dict) for subject in subjects): return None
        fields = ('unit_id', 'year', 'department_code', 'department', 'department_scope',
                  'code', 'code_level', 'name', 'direction', 'direction_scope', 'study_mode')
        identities = tuple(sorted((canonical(subject.get('exam_unit')), canonical(subject.get('code')),
                                   canonical(subject.get('name')), canonical(subject.get('scope'))) for subject in subjects))
        return tuple(canonical(program.get(field, '')) for field in fields) + (identities,)

    def _tree_scope(self, unit, year, documents):
        from collectors.catalog_zsml import parse_zsml_tree
        departments, expected, routes, found = {}, set(), {}, 0
        for document in documents:
            if document.get('catalog_parser') in PUBLIC_CATALOG_ADAPTERS: continue
            if document.get('catalog_parser') != 'zsml_tree' and not str(document.get('local_path', '')).endswith('.json'):
                continue
            if not self._document_allowed(document, unit): return None
            source = self.read(document)
            if not source: return None
            found += 1
            if found > 1: return None
            key = (str(source[0]), document['sha256'], document.get('url'), str(unit['id']), year)
            with self._lock: cached = self._trees.get(key)
            if cached is not None:
                departments, expected, routes = cached
                continue
            try:
                tree = json.loads(source[1])
                nodes = parse_zsml_tree(tree)
            except (ValueError, TypeError): return None
            departments = {node['id']: node['name'] for node in tree}
            expected = {(node['department_id'], node.get('route_id', node['code'])) for node in nodes}
            routes = {(node['department_id'], node.get('route_id', node['code'])): node['code'] for node in nodes}
            with self._lock: self._trees = {**self._trees, key: (departments, expected, routes)}
        with self._lock: self._tree_route_codes = {**self._tree_route_codes, (str(unit['id']), year): routes}
        return departments, expected

    def _program_replays(self, program, documents, unit, source_ids):
        if program.get('unit_id') != unit.get('id'): return False
        primary = program.get('catalog_document_id')
        if primary is None:
            if len(source_ids) != 1: return False
            primary = next(iter(source_ids))
        if primary not in source_ids: return False
        document = documents[primary]
        if (document.get('unit_id') != unit.get('id') or document.get('year') != program.get('year')
                or not document.get('catalog_active', True)): return False
        relevant = [value for value in documents.values() if value.get('unit_id') == unit.get('id')
                    and value.get('year') == program.get('year') and value.get('kind') == 'catalog'
                    and value.get('catalog_active', True)]
        tree = self._tree_scope(unit, program.get('year'), relevant) if document.get('catalog_parser') == 'zsml_table' else ({}, set())
        if tree is None: return False
        parsed = self._source_programs(document, unit, program.get('year'), tree[0], documents)
        if parsed is None: return False
        signature = self._signature(program)
        matches = [row for row in parsed['programs'] if row.get('id') == program['id'] and self._signature(row) == signature]
        if len(matches) != 1: return False
        if (document.get('catalog_parser') in PUBLIC_CATALOG_ADAPTERS|STATIC_CATALOG_ADAPTERS
                and any(program.get(field)!=matches[0].get(field) for field in ('degree_type','notes'))): return False
        if document.get('catalog_parser')=='pdf_table_hit_v1' and any(program.get(field)!=matches[0].get(field)
                for field in ('province','campus','training_locations','campus_evidence','source_locator')):return False
        if document.get('catalog_parser')=='ucas_public_v1' and any(program.get(field)!=matches[0].get(field)
                for field in ('catalog_variants','advisors','training_unit_address','province','eligibility_evidence_complete','requires_review')): return False
        from collectors.catalog_semantic_audit import semantic_programs_sha256
        exam_facts = lambda row: {'id': row['id'], 'subjects': row.get('subjects'),
            'exam_combinations': row.get('exam_combinations'), 'exam_matching_supported': row.get('exam_matching_supported'),
            'subject_identity_issues': row.get('subject_identity_issues')}
        try:
            if semantic_programs_sha256([exam_facts(program)]) != semantic_programs_sha256([exam_facts(matches[0])]): return False
        except (ValueError, TypeError): return False
        if document.get('catalog_parser') == 'chsi_detail_v1':
            if program.get('catalog_publishable') is False or program.get('not_publishable_without_detail'): return False
            if any(program.get(field) != matches[0].get(field) for field in (
                    'subjects', 'exam_combinations', 'exam_matching_supported', 'chsi_direction_id',
                    'planned_enrollment', 'degree_type', 'notes')): return False
        # A real quote elsewhere in the same document cannot substantiate this row.
        return all(citation_signature(program.get(field)) is not None
                   and citation_signature(program.get(field)) == citation_signature(matches[0].get(field))
                   for field in ('evidence', 'exam_evidence'))

    def _source_programs(self, document, unit, year, departments, documents=None, _seen=None):
        if document.get('catalog_parser') == 'chsi_detail_v1':
            return self._chsi_programs(document, unit, year)
        if document.get('catalog_parser') in PUBLIC_CATALOG_ADAPTERS:
            return self._public_programs(document,unit,year,documents or {})
        if not self._document_allowed(document, unit): return None
        source = self.read(document)
        if not source: return None
        pages = self._source_pages(document, source)
        if not pages: return None
        adapter = document.get('catalog_parser')
        if adapter is not None and (document.get('unit_id') != unit.get('id') or document.get('year') != year): return None
        context = None
        predecessor = document.get('catalog_previous_id')
        if predecessor is not None:
            seen = set(_seen or ())
            if document.get('id') in seen or len(seen) >= 100 or not isinstance(documents, dict): return None
            previous = documents.get(predecessor)
            if (adapter != 'html_table_v1' or not previous or previous.get('unit_id') != unit.get('id')
                    or previous.get('year') != year or not previous.get('catalog_active', True)): return None
            replay = self._source_programs(previous, unit, year, departments, documents, seen | {document.get('id')})
            context = (replay or {}).get('continuation_context')
            if not context: return None
        context_key = hashlib.sha256(json.dumps(context, sort_keys=True, ensure_ascii=False).encode()).hexdigest() if context else ''
        publication_key = ''
        if adapter in {'pdf_table_v1', 'pdf_table_v2','pdf_table_national_v1','pdf_table_national_v2','pdf_table_hit_v1','office_national_v1','office_structured_v1'} and isinstance(document.get('publication_page'), dict):
            from collectors.catalog_publication import publication_context_signature
            publication_key = publication_context_signature(document, unit, year, project_root=self.root)
        route_code = None
        if adapter == 'zsml_table':
            route = re.search(r'/zsmlYjfx/' + str(year) + r'/([^/]+)/(' + ZSML_ROUTE + r')$', urlsplit(document['url']).path)
            with self._lock: routes = self._tree_route_codes.get((str(unit['id']), year), {})
            if route:
                route_code = routes.get(route.groups())
                if route_code is None and not routes and '_' not in route[2]: route_code = route[2]
        stamp=self._context_stamp(document,documents or {})
        if stamp is None:return None
        key = (str(source[0]), document['sha256'], document.get('id'), document.get('url'), adapter,
               str(unit['id']), unit.get('name'), year, tuple(sorted(departments.items())), predecessor, context_key, publication_key, route_code,
               stamp)
        with self._lock: cached = self._parsed.get(key)
        if cached is not None: return cached
        from collectors.catalogs import parse_nankai_raw, parse_zzu_html
        from collectors.documents import decode_text
        text = '\f'.join(pages)
        binary = source[1].startswith(b'%PDF-') or adapter in {'office_table_v1','office_national_v1','office_structured_v1'}
        raw = decode_text(source[1], document.get('content_type', '')) if not binary else None
        if raw is not None:
            from public_sources import rendered_source_html
            raw = rendered_source_html(raw, document['url'])
        if adapter == 'html_table_v1':
            from collectors.catalog_html import parse_catalog_html, catalog_pagination
            if not html_catalog_year(raw, year) or not ordinary_catalog_scope(raw, html=True): return None
            if context is not None:
                from collectors.catalog_source_set import _DirectoryForm
                form = _DirectoryForm()
                form.feed(raw)
                pagination = catalog_pagination(raw)
                previous_page = (replay or {}).get('pagination', {})
                if (form.selected_departments != {context.get('department_code')}
                        or previous_page.get('current') is None or pagination.get('current') != previous_page['current'] + 1
                        or pagination.get('total') != previous_page.get('total')): return None
            result = (parse_catalog_html(raw, unit, year, document, context=context) if context is not None
                      else parse_catalog_html(raw, unit, year, document))
            from collectors.catalog_enrichment import apply_inline_policies
            result = {**result, 'programs': apply_inline_policies(result['programs'], raw, unit, year, document)}
        elif adapter in STATIC_CATALOG_ADAPTERS:
            from collectors.catalog_batch import parse_document
            project=self.root.parent if self.root.name=='data' else self.root
            try:result=parse_document({**document,'local_path':str(source[0])},unit,year,project,source[1])
            except (ValueError,OSError,ImportError,TypeError,KeyError):return None
        elif adapter == 'pdf_row_v1':
            from collectors.catalog_pdf import parse_catalog_pdf
            if canonical(unit.get('name')) + str(year) not in canonical(text) or not ordinary_catalog_scope(text): return None
            result = parse_catalog_pdf(text, unit, year, document)
        elif adapter == 'pdf_table_v1':
            from collectors.catalog_pdf_tables import extract_pdf_layout, parse_catalog_pdf_tables
            if not ordinary_catalog_scope(text): return None
            result = parse_catalog_pdf_tables(extract_pdf_layout(source[0]), unit, year, document, project_root=self.root)
        elif adapter == 'pdf_table_v2':
            from collectors.catalog_pdf_variants import extract_pdf_variant_layout, parse_catalog_pdf_variants
            if not ordinary_catalog_scope(text): return None
            result = parse_catalog_pdf_variants(extract_pdf_variant_layout(source[0]), unit, year, document, project_root=self.root)
        elif adapter == 'office_table_v1':
            from collectors.catalog_office import parse_catalog_office, extract_office_html
            office_html = extract_office_html(source[1])
            if not html_catalog_year(office_html, year) or not ordinary_catalog_scope(office_html, html=True): return None
            result = parse_catalog_office(source[1], unit, year, document)
        elif adapter in {'zsml_tree', 'catalog_index_v1'}:
            return None
        elif adapter == 'zsml_table':
            from collectors.catalog_zsml import parse_zsml_html
            match = re.search(r'/zsmlYjfx/' + str(year) + r'/([^/]+)/(' + ZSML_ROUTE + r')$', urlsplit(document['url']).path)
            if not match or not route_code or parse_qs(urlsplit(document['url']).query).get('ksfs') != ['21'] or match[1] not in departments: return None
            result = parse_zsml_html(raw, unit, departments[match[1]], year, document)
            if any(row.get('code') != route_code for row in result['programs']): return None
        elif adapter is not None:
            return None
        elif '21全国统考' in text:
            result = parse_nankai_raw(text, unit, year, document)
        else:
            if not re.search(r'class=["\'][^"\']*\bzsml-talbe-yx-zy-yjfx\b', raw): return None
            parsed = urlsplit(document['url'])
            match = re.search(r'/zsmlYjfx/' + str(year) + r'/([^/]+)/([0-9A-Z]{6})$', parsed.path)
            if not match or parse_qs(parsed.query).get('ksfs') != ['21'] or match.group(1) not in departments: return None
            result = parse_zzu_html(raw, unit, departments[match.group(1)], year, document)
        self._remember('_parsed',key,result)
        return result

    def unit_reconciles(self, unit, year, programs, documents):
        if any(program.get('subject_identity_issues') for program in programs): return False
        relevant = [document for document in documents.values() if document.get('unit_id') == unit.get('id') and document.get('year') == year and document.get('kind') == 'catalog' and document.get('catalog_active', True)]
        tree = self._tree_scope(unit, year, relevant)
        if tree is None: return False
        departments, expected_pages = tree
        explicit = any(document.get('catalog_parser') in CATALOG_ADAPTERS for document in relevant)
        if len({(document.get('url'), document.get('sha256')) for document in relevant}) != len(relevant): return False
        public_scope_valid=False
        if explicit and not expected_pages:
            from collectors.catalog_batch import unit_scope_supported
            from collectors.catalog_source_set import source_set_reconciliation
            from collectors.documents import decode_text
            originals = []
            public_forms=[document for document in relevant if document.get('catalog_parser') in PUBLIC_CATALOG_ADAPTERS]
            if public_forms:
                from collectors.catalog_public_forms import public_form_scope
                project=self.root.parent if self.root.name=='data' else self.root
                scope_parser=public_form_scope
                adapters={doc['catalog_parser'] for doc in public_forms}
                if len(adapters)!=1:return False
                if adapters=={'public_aspx_v1'}:
                    from collectors.catalog_public_aspx import public_aspx_scope
                    scope_parser=public_aspx_scope
                elif adapters=={'nuaa_public_v1'}:
                    from collectors.catalog_nuaa_public import nuaa_public_scope
                    scope_parser=nuaa_public_scope
                elif adapters=={'ucas_public_v1'}:
                    from collectors.catalog_ucas import ucas_public_scope
                    scope_parser=ucas_public_scope
                elif adapters=={'university_form_v1'}:
                    from collectors.catalog_university_forms import university_form_scope
                    scope_parser=university_form_scope
                proof=scope_parser(unit,year,documents,project_root=project)
                if (len(public_forms)!=len(relevant) or not proof['complete']
                        or set(proof['expected_document_ids'])!={document['id'] for document in public_forms}
                        or proof['expected_records']!=len(programs)): return False
                public_scope_valid=True
            for document in relevant:
                source = self.read(document)
                if not source or not self.pages(document, unit): return False
                if document.get('catalog_parser') in {'office_table_v1','office_national_v1','office_structured_v1'}:
                    from collectors.catalog_office import extract_office_html
                    text = extract_office_html(source[1])
                else:
                    from public_sources import rendered_source_html
                    text = ('\f'.join(self.pages(document, unit)) if source[1].startswith(b'%PDF-')
                            else rendered_source_html(decode_text(source[1], document.get('content_type', '')), document['url']))
                originals.append((document.get('catalog_parser'), text))
            if public_forms: pass
            elif len(relevant)==1 and relevant[0].get('catalog_parser')=='pdf_table_hit_v1':
                parsed=self._source_programs(relevant[0],unit,year,{},documents)
                scope=(parsed or {}).get('source_scope',{})
                if (scope.get('type')!='full_document' or scope.get('physical_direction_rows')!=len(programs)
                        or scope.get('pages')!=len(self.pages(relevant[0],unit))):return False
            elif len(relevant) > 1 or any(document.get('catalog_parser') == 'catalog_index_v1' for document in relevant):
                if not source_set_reconciliation(unit, year, relevant, self)['complete']: return False
            elif not unit_scope_supported(unit, year, originals): return False
        source_counts, parsed_programs, observed_pages = {}, [], set()
        for document in relevant:
            parsed = self._source_programs(document, unit, year, departments, documents)
            if parsed is None:
                if explicit and document.get('catalog_parser') not in {'zsml_tree', 'catalog_index_v1'}: return False
                continue
            rows = parsed['programs']
            if any(row.get('subject_identity_issues') for row in rows): return False
            excluded_only = (document.get('catalog_parser') in {'html_table_v1', 'zsml_table'} and not rows
                and type(parsed.get('excluded_recommendation_only')) is int and parsed['excluded_recommendation_only'] > 0)
            empty_public=public_scope_valid and not rows and parsed['expected_records']==0
            if parsed['issues'] or (not rows and not excluded_only and not empty_public) or parsed['expected_records'] != len(rows): return False
            source_counts[document['id']] = len(rows)
            parsed_programs.extend(rows)
            match = re.search(r'/zsmlYjfx/' + str(year) + r'/([^/]+)/(' + ZSML_ROUTE + r')$', urlsplit(document['url']).path)
            if match: observed_pages.add(match.groups())
        if not source_counts or expected_pages != observed_pages: return False
        actual_counts = {}
        for program in programs:
            evidence = program.get('evidence')
            evidence = [evidence] if isinstance(evidence, dict) else evidence
            source_ids = {item.get('document_id') for item in evidence or [] if isinstance(item, dict) and item.get('document_id') in source_counts}
            if program.get('catalog_document_id') in source_ids:
                source_ids = {program['catalog_document_id']}
            if len(source_ids) != 1: return False
            source_id = next(iter(source_ids))
            actual_counts[source_id] = actual_counts.get(source_id, 0) + 1
        signatures = [self._signature(program) for program in parsed_programs]
        if len({row.get('id') for row in parsed_programs}) != len(parsed_programs): return False
        if explicit and Counter(row.get('id') for row in parsed_programs) != Counter(row.get('id') for row in programs): return False
        return (actual_counts == {key: count for key, count in source_counts.items() if count}
                and None not in signatures and len(signatures) == len(set(signatures))
                and Counter(signatures) == Counter(self._signature(program) for program in programs))
