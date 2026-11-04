/* Isolated browser fixture: temporary static server and mocked APIs; no real DB or provider. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require(process.env.PLAYWRIGHT_PATH || 'D:/work_app/nodejs/node_cache/_npx/e41f203b7505f1fb/node_modules/playwright');
const root = path.resolve(__dirname, '../static');
const out = path.resolve(__dirname, '../data/runtime/node4/claude-c2/browser');
fs.mkdirSync(out, {recursive: true});
const entryId = 'k_' + 'a'.repeat(24);
const segmentId = 'sg_' + 'b'.repeat(32);
const quality = {version: 1, state: 'accepted', reusable: true, material_type: 'catalog', reasons: [{message: '[夹具] 官方目录准入。'}]};
const entry = {id: entryId, title: '[夹具] 2027 年计算机学院招生目录', unit_id: 'u_fixture', unit_name: '[夹具] 样本大学',
  year: 2027, requested_year: 2028, url: 'https://example.edu.cn/catalog.pdf', status: 'official_original_read',
  original_available: true, usable: true, validity: 'active', quality, index_state: 'succeeded',
  extraction: {id: 'ex_fixture', generation_id: 'kg_fixture', format: 'pdf', page_kind: 'physical', extraction_status: 'complete', segment_count: 2},
  text: 'LEGACY_CANARY_SHOULD_NOT_OVERRIDE_SEGMENTS', sha256: 'a'.repeat(64), provenance: ['r_fixture'], versions: [],
  excerpt: '计算机学院招生目录夹具。', last_seen_at: '2026-11-02T00:00:00Z'};
const rawSegment = {id: segmentId, entry_id: entryId, extraction_id: 'ex_fixture', generation_id: 'kg_fixture',
  text: '081200 计算机科学与技术 初试科目101、201、301、408。\n<script>window.hybridUnsafe=true</script>',
  char_start: 32001, char_end: 32090, page_start: 2, page_end: 2, page_kind: 'physical', format: 'pdf',
  kind: 'table_row', section_path: ['计算机学院', '报考要求']};
const segment = () => ({...rawSegment, source: entry});
const readValue = selected => ({segment: selected,
  contexts: [{role: 'table_header', text: '专业代码\t专业名称\t初试科目', char_start: 30001, char_end: 30022, page_start: 1, page_end: 1}],
  neighbors: [], returned_chars: 180, truncated: false});
const results = [], errors = [], requests = [];
let browser, page, server, base, world;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const clone = value => JSON.parse(JSON.stringify(value));
async function check(name, run) {
  try { await run(); results.push({name, passed: true}); console.log('PASS ' + name); }
  catch (error) { results.push({name, passed: false, error: error.stack}); console.log('FAIL ' + name + ': ' + error.message);
    if (page) await page.screenshot({path: path.join(out, 'failure-' + results.length + '.png'), fullPage: false}).catch(() => {}); }
}
function vectorState() {
  const spaces = [{id: 'vs_serving', state: 'ready', provider: 'local_bge', model: 'BAAI/bge-small-zh-v1.5',
    dimension: 512, profile_id: 'ep_serving', sources: 3, segments: 9, windows: 12, completed_count: 12, expected_count: 12}];
  if (world.staging) spaces.push({id: 'vs_staging', state: 'staging', provider: 'local_bge', model: 'BAAI/bge-small-zh-v1.5',
    dimension: 512, profile_id: 'ep_desired', sources: 1, segments: 2, windows: 2, completed_count: 1, expected_count: 2});
  return {status: 'ready', backend: 'milvus_lite', active_space_id: 'vs_serving', active_profile_id: 'ep_serving',
    active_provider: 'local_bge', spaces, jobs: {pending: 0, running: world.building ? 1 : 0, succeeded: 12, failed: 0, cancelled: 0, uncertain: 0},
    dirty: world.stale, building: world.building, error_code: '', current: {sources: 3, segments: 9, windows: null},
    last_build: {embedded_inputs: 12, cache_hits: 4, provider_calls: 1, input_tokens: 1200, usage_known: true},
    embedding_model: {state: world.modelState, prepared: world.modelState === 'ready', files_verified: world.modelState === 'ready' ? 5 : 0, total_files: 5}};
}
async function releaseHeld() { for (const item of world.held) item.resolve(); world.held = []; }
async function run() {
  server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://fixture');
    if (url.pathname === '/knowledge' || url.pathname === '/settings') {
      const mount = url.pathname === '/knowledge' ? 'mountKnowledge' : 'mountSettings';
      const options = url.pathname === '/knowledge' ? "initialQuery:'计算机',initialQuality:'accepted'" : "onBack:()=>window.__back++";
      response.writeHead(200, {'content-type': 'text/html;charset=utf-8'});
      response.end(`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/styles.css"><body><main id="fixture"></main><script type="module">import {${mount}} from '/${url.pathname === '/knowledge' ? 'knowledge' : 'settings'}.mjs';import {createApi} from '/data.mjs';window.__cleanup=${mount}(document.querySelector('#fixture'),{${options},api:createApi()});</script></body></html>`);
      return;
    }
    const target = path.join(root, path.basename(url.pathname));
    if (!/\.(mjs|css|svg)$/.test(target) || !fs.existsSync(target)) { response.writeHead(404); response.end(); return; }
    response.writeHead(200, {'content-type': target.endsWith('.mjs') ? 'text/javascript;charset=utf-8' : target.endsWith('.svg') ? 'image/svg+xml' : 'text/css;charset=utf-8'});
    response.end(fs.readFileSync(target));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = 'http://127.0.0.1:' + server.address().port;
  browser = await chromium.launch({headless: true, channel: 'msedge'});
  page = await browser.newPage({viewport: {width: 1440, height: 1050}});
  page.setDefaultTimeout(8000);
  page.on('pageerror', error => errors.push(error.message));
  world = {held: [], modelState: 'not_prepared', building: false, staging: false, stale: true, degraded: false, holdHybrid: false};
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== base) return route.abort();
    if (!url.pathname.startsWith('/api/')) return route.continue();
    const body = request.postDataJSON?.();
    requests.push({path: url.pathname, method: request.method(), query: Object.fromEntries(url.searchParams), body});
    const good = async data => route.fulfill({json: {ok: true, data, error: null}}).catch(error => {
      if (!/closed|handled|cancel|abort|Invalid Interception/i.test(error.message)) throw error;
    });
    if (url.pathname === '/api/settings/providers') return good({providers: [{id: 'deepseek', label: 'DeepSeek'}],
      embedding_providers: [
        {id: 'local_bge', label: '本机中文模型 · BGE', default_model: 'BAAI/bge-small-zh-v1.5', base_url: '', requires_key: false, local: true},
        {id: 'siliconflow', label: '硅基流动', default_model: 'BAAI/bge-m3', base_url: 'https://api.siliconflow.cn/v1', requires_key: true, local: false, models_path: '/models?sub_type=embedding'}]});
    if (url.pathname === '/api/settings') return good({ai: {status: 'connected', provider: 'deepseek', model: 'chat', has_key: true},
      yuanbao: {status: 'connected', has_key: true}, storage: {mode: 'encrypted', persistent: true},
      embedding: {provider: 'local_bge', model: 'BAAI/bge-small-zh-v1.5', dimension: 512, status: 'connected', has_key: false, profile_id: 'ep_desired'}});
    if (url.pathname === '/api/knowledge/vectors' && request.method() === 'GET') {
      if (world.prepareTicks > 0) { world.prepareTicks--; if (!world.prepareTicks) world.modelState = 'ready'; }
      return good(vectorState());
    }
    if (url.pathname === '/api/knowledge/embedding/prepare' && request.method() === 'POST') {
      world.modelState = 'preparing'; world.prepareTicks = 2; return good({state: 'preparing', started: true});
    }
    if (url.pathname === '/api/knowledge/vectors/build' && request.method() === 'POST') {
      world.building = true; world.staging = true; return good({...vectorState(), started: true, building: true});
    }
    if (url.pathname === '/api/knowledge/vectors/cancel' && request.method() === 'POST') {
      world.building = false; return good({state: 'cancelling', started: true, build_id: 'vb_fixture'});
    }
    if (url.pathname === '/api/knowledge' && request.method() === 'GET') {
      return good(url.searchParams.has('entry_id') ? clone(entry) : {entries: [entry], total: 1, page: 1, page_size: 12,
        summary: {total: 1, quality_counts: {accepted: 1, pending: 0, excluded: 0}, notice: '夹具'},
        facets: {units: [{unit_id: entry.unit_id, unit_name: entry.unit_name}], years: [2027]}});
    }
    if (url.pathname === '/api/knowledge/retrieve' && request.method() === 'POST') {
      if (world.holdHybrid) { world.holdHybrid = false; await new Promise(resolve => world.held.push({key: 'hybrid', resolve})); }
      const hits = world.degraded ? [] : [{...segment(), retrieval_methods: ['keyword', 'semantic'],
        rerank: {version: 'rule_v1'}, retrieval_ranks: {keyword: 1, semantic: 1}}];
      return good({segments: hits, returned_count: hits.length, candidate_count: hits.length ? 2 : 0,
        usage: world.degraded ? {embedding_calls: 1, successful_embedding_calls: 0, input_tokens: null}
                              : {embedding_calls: 1, successful_embedding_calls: 1, input_tokens: 12},
        usage_known: !world.degraded,
        retrieval: {mode: world.degraded ? 'keyword' : 'hybrid', methods: world.degraded ? ['keyword'] : ['keyword', 'semantic'],
          degraded: world.degraded, degraded_reason: world.degraded ? 'vector_not_ready' : '', partial_scope: true,
          reranker: 'rule_v1', notice: '语义相近不等于报考条件相同。',
          latency: {keyword_ms: 2, semantic_ms: world.degraded ? 0 : 8, rerank_ms: 1, total_ms: world.degraded ? 3 : 11}}});
    }
    if (url.pathname === '/api/knowledge/segments') return good({segments: [segment()], total: 1, page: 1});
    if (url.pathname === '/api/knowledge/segment') {
      const selected = {...segment(), id: url.searchParams.get('segment_id')};
      return good(readValue(selected));
    }
    if (url.pathname === '/api/knowledge/jobs') return good({entry_id: entryId, jobs: [{state: 'succeeded', attempts: 1, error_code: ''}], backend: 'local_segments'});
    return route.fulfill({status: 404, json: {ok: false, data: null, error: {code: 'fixture_missing', message: '未知夹具API'}}});
  });

  await check('hybrid is an explicit POST and its result opens the exact N3 segment while keyword paging stays intact', async () => {
    await page.goto(base + '/knowledge');
    await page.locator('.yk-entry').first().waitFor();
    assert.equal(requests.filter(item => item.path === '/api/knowledge/retrieve').length, 0, 'mount must not retrieve implicitly');
    const before = requests.filter(item => item.path === '/api/knowledge').length;
    await page.locator('[data-knowledge-action="hybrid-search"]').click();
    await page.locator('.yk-hybrid-results').waitFor();
    const post = requests.find(item => item.path === '/api/knowledge/retrieve');
    assert.equal(post.method, 'POST'); assert.equal(post.body.q, '计算机'); assert.equal(post.body.limit, 8);
    assert.equal(requests.filter(item => item.path === '/api/knowledge').length, before, 'keyword list request was not reissued');
    assert.match(await page.locator('.yk-hybrid-results').innerText(), /返回 1 条/);
    assert.match(await page.locator('.yk-hybrid-results').innerText(), /关键词与语义同时命中/);
    await page.locator('.yk-hybrid-hit').click();
    await page.locator('.yk-focused-quote').waitFor();
    const read = requests.filter(item => item.path === '/api/knowledge/segment').at(-1);
    assert.equal(read.query.segment_id, segmentId); assert.equal(read.query.before, '1'); assert.equal(read.query.max_chars, '9000');
    assert.match(await page.locator('.yk-focused-quote').innerText(), /第 2 页/);
    assert.equal(await page.locator('.yk-reader script').count(), 0);
    assert.equal(await page.evaluate(() => window.hybridUnsafe), undefined);
    await page.screenshot({path: path.join(out, 'knowledge-hybrid-desktop.png'), fullPage: false});
  });

  await check('degraded hybrid names the keyword fallback and leaves tokens unknown rather than zero', async () => {
    world.degraded = true;
    await page.locator('[data-knowledge-action="hybrid-search"]').click();
    await page.locator('.yk-hybrid-results').filter({hasText: '关键词降级'}).waitFor();
    const text = await page.locator('.yk-hybrid-results').innerText();
    assert.match(text, /语义索引尚未建立/); assert.match(text, /输入 token 未知/);
    assert.ok(!text.includes('输入 0 token'));
    world.degraded = false;
  });

  await check('a superseded hybrid response cannot replace results for newer input', async () => {
    world.holdHybrid = true;
    await page.locator('#yk-query').fill('旧问题');
    await page.locator('[data-knowledge-action="hybrid-search"]').click();
    await page.waitForFunction(() => document.querySelector('.yk-hybrid-results.is-loading'));
    await page.locator('#yk-query').fill('新的计算机问题');
    await page.waitForFunction(() => !document.querySelector('.yk-hybrid-results'));
    await releaseHeld(); await delay(150);
    assert.equal(await page.locator('.yk-hybrid-results').count(), 0, 'late hybrid response must stay discarded');
    assert.equal(await page.evaluate(() => window.hybridUnsafe), undefined);
  });

  await check('native Chinese composition survives an asynchronous keyword list response', async () => {
    await page.goto(base + '/knowledge');
    await page.locator('.yk-entry').first().waitFor();
    const cdp = await page.context().newCDPSession(page);
    await page.locator('#yk-query').focus();
    await page.locator('#yk-query').evaluate(node => {
      window.imeAnchor = node; window.imeDetached = false;
      window.imeObserver = new MutationObserver(records => { for (const record of records) for (const removed of record.removedNodes)
        if (removed === node || removed.contains?.(node)) window.imeDetached = true; });
      window.imeObserver.observe(document.querySelector('#fixture'), {subtree: true, childList: true});
    });
    await page.locator('#yk-query').fill('');
    await cdp.send('Input.insertText', {text: '计算机'});
    await cdp.send('Input.imeSetComposition', {text: 'zhaosheng', selectionStart: 8, selectionEnd: 8});
    await delay(400);
    await page.waitForFunction(() => document.querySelectorAll('.yk-entry').length === 1);
    const state = await page.locator('#yk-query').evaluate(node => ({same: node === window.imeAnchor,
      connected: window.imeAnchor.isConnected, focused: document.activeElement === node, detached: window.imeDetached, value: node.value}));
    assert.deepEqual(state, {same: true, connected: true, focused: true, detached: false, value: '计算机zhaosheng'});
    await cdp.send('Input.insertText', {text: '招生'});
    assert.equal(await page.locator('#yk-query').inputValue(), '计算机招生');
  });

  await check('settings list only embedding-capable providers, local BGE default, explicit prepare/build/cancel', async () => {
    await page.goto(base + '/settings');
    await page.locator('[data-settings-tab="embedding"]').click();
    await page.locator('.ye-vector-settings').waitFor();
    const providerText = await page.locator('#ye-provider').innerText();
    assert.match(providerText, /本机中文模型/); assert.match(providerText, /硅基流动/);
    assert.ok(!providerText.includes('DeepSeek'));
    assert.equal(await page.locator('[name="embedding_api_key"]').count(), 0, 'local BGE needs no Key');
    assert.equal(requests.filter(item => item.path === '/api/knowledge/embedding/prepare').length, 0, 'no implicit download');
    assert.equal(requests.filter(item => item.path === '/api/knowledge/vectors/build').length, 0, 'no implicit build');
    const vectorText = await page.locator('.ye-vector-settings').innerText();
    assert.match(vectorText, /目标模型或资料范围已变化/);
    assert.match(vectorText, /当前服务索引（serving）/);
    await page.locator('[data-embedding-action="vectors-prepare"]').click();
    await page.waitForFunction(() => document.body.innerText.includes('正在准备本机模型'));
    assert.equal(requests.filter(item => item.path === '/api/knowledge/embedding/prepare').length, 1);
    await page.waitForFunction(() => document.body.innerText.includes('已校验 5 / 5 个模型文件'), null, {timeout: 9000});
    await page.locator('[data-embedding-action="vectors-build"]').click();
    await page.locator('[data-embedding-action="vectors-confirm"]').click();
    await page.waitForFunction(() => document.body.innerText.includes('停止构建'));
    assert.equal(requests.filter(item => item.path === '/api/knowledge/vectors/build').length, 1);
    assert.match(await page.locator('.ye-vector-settings').innerText(), /正在建立（staging）/);
    await page.locator('[data-embedding-action="vectors-cancel"]').click();
    await page.waitForFunction(() => document.body.innerText.includes('已请求服务端停止构建'));
    assert.equal(requests.filter(item => item.path === '/api/knowledge/vectors/cancel').length, 1);
    await page.screenshot({path: path.join(out, 'settings-vector-desktop.png'), fullPage: true});
  });

  await check('knowledge and settings remain readable without horizontal overflow at 320, 390, 1440 and 2552', async () => {
    for (const width of [320, 390, 1440, 2552]) {
      await page.setViewportSize({width, height: 1000});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, width + 'px settings overflow');
    }
    await page.setViewportSize({width: 390, height: 900});
    await page.screenshot({path: path.join(out, 'settings-vector-mobile.png'), fullPage: true});
    await page.goto(base + '/knowledge'); await page.locator('.yk-entry').first().waitFor();
    for (const width of [320, 390, 1440, 2552]) {
      await page.setViewportSize({width, height: 1000});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, width + 'px knowledge overflow');
    }
    await page.locator('[data-knowledge-action="hybrid-search"]').click();
    await page.locator('.yk-hybrid-results').waitFor();
    await page.setViewportSize({width: 390, height: 900});
    await page.screenshot({path: path.join(out, 'knowledge-hybrid-mobile.png'), fullPage: true});
    assert.deepEqual(errors, []);
  });
}
run().catch(error => { console.error(error); results.push({name: 'harness', passed: false, error: error.stack}); })
  .finally(async () => {
    await releaseHeld();
    if (browser) await browser.close();
    if (server) await new Promise(resolve => server.close(resolve));
    const report = {passed: results.length > 0 && results.every(item => item.passed), results, errors, requests,
      scope: 'Temporary static server on random loopback port, all APIs mocked; no real DB, model or provider calls.'};
    fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify({passed: report.passed, checks: results.length, report: path.join(out, 'report.json')}));
    process.exitCode = report.passed ? 0 : 1;
  });
