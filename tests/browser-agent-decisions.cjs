/* Isolated browser fixture for N7 decision surfaces: plan ledger, turn changes, field
 * provenance/conflicts and original links. The page and all APIs are served by this
 * script from a temporary local server; no real DB, provider or port 4180 is touched.
 * Run: node tests/browser-agent-decisions.cjs
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require(process.env.PLAYWRIGHT_PATH || 'D:/work_app/nodejs/node_cache/_npx/e41f203b7505f1fb/node_modules/playwright');
const root = path.resolve(__dirname, '../static');
const out = path.resolve(__dirname, '../data/runtime/node5-7/browser');
fs.mkdirSync(out, {recursive: true});
const results = [], errors = [];
let browser, page, server, base;

const taskId = 'r_' + 'e'.repeat(32);
const program = {
  id: 'p_fixture', unit_id: 'u_fixture', unit_name: '[夹具] 样本大学', year: 2027,
  name: '计算机科学与技术', code: '081200', department: '计算机学院',
  direction: '00 不区分方向', study_mode: '全日制', province: '天津',
  subjects: [{code: '408', name: '计算机学科专业基础', scope: 'national', exam_unit: 4}],
  tuition: {amount: 12000, period: 'year', verified_by: 'two_source_agreement'},
  eligibility_evidence_complete: false, requires_review: true,
};
const task = {
  id: taskId, research_id: taskId, query: '[夹具] 计算机择校', status: 'completed',
  turn_id: 2, previous_turn_id: 1, turn_changes: ['max_annual_tuition'],
  target_year: 2028, reference_year: 2027,
  created_at: '2026-11-02T00:00:00Z', updated_at: '2026-11-02T00:00:00Z',
  events: [{id: 1, turn_id: 2, stage: 'inherit', title: '[夹具] 复用上轮成果', message: '模拟事件。'}],
  usage: {model_calls: 1, searches: 0, documents: 0, tool_calls: 2},
  report: {
    summary: '[夹具] 本轮继承与字段草案', assistant_summary: '模拟报告，仅用于界面验证。',
    candidates: [program],
    evidence: [
      {id: 's1', title: '[夹具] 2027 目录', url: 'https://example.edu.cn/catalog.html',
       status: 'official_original_read', year: 2027, excerpt: '模拟目录原文。'},
      {id: 's2', title: '[夹具] 2027 收费公示', url: 'https://example.edu.cn/fees.html',
       status: 'official_original_read', year: 2027, excerpt: '模拟收费原文。'},
    ],
    gaps: ['[夹具] 报考资格仍未知'],
    inheritance: {
      reused: true, candidate_ids: ['p_fixture'], source_ids: ['s1', 's2'],
      stale_candidate_ids: ['old_fixture'], profile_changed: ['max_annual_tuition'],
      previous_turn_id: 1,
    },
    field_candidates: [
      {id: 'fc_verified', field: 'tuition', label: '学费与收费口径', status: 'verified',
       value: 12000, period: 'year', paused: false, notice: '两个独立原文一致。',
       values: [{quote: '各专业学费均为12000元/年。', title: '[夹具] 2027 收费公示', year: 2027,
                 url: 'https://example.edu.cn/fees.html'}]},
      {id: 'fc_pending', field: 'eligibility', label: '报考资格与适用范围', status: 'needs_review',
       paused: true, value: null, notice: '资格条款仅作为待核线索。',
       values: [{quote: '报考条件：具有国家承认的本科毕业学历。', title: '[夹具] 2027 招生简章',
                 year: 2027, url: 'https://example.edu.cn/charter.html'}]},
      {id: 'fc_conflict', field: 'tuition', label: '学费与收费口径', status: 'conflict',
       paused: true, value: null, notice: '来源对同一收费口径给出不同数值，字段暂停。',
       values: [{quote: '另一个来源给出15000元/年。', title: '[夹具] 冲突来源', year: 2027,
                 url: 'javascript:alert(1)'}]},
    ],
    decisions: [{
      candidate_id: 'p_fixture', deterministic: true,
      original_links: [{url: 'https://example.edu.cn/catalog.html'},
                       {url: 'https://example.edu.cn/fees.html'},
                       {url: 'javascript:alert(2)'}],
      reasons: [{message: '资格状态未知，不能写成符合。',
                 evidence: [{url: 'https://example.edu.cn/catalog.html'}]}],
    }],
    plan: {
      version: 1,
      goals: [
        {id: 'scope', label: '确定可研究的合理范围', status: 'done', completion: '已取得有限候选。'},
        {id: 'evidence', label: '补齐关键事实', status: 'partial', completion: '资格仍未知。'},
        {id: 'algorithm', label: '用确定性算法比较', status: 'done', completion: '匹配算法已执行。'},
        {id: 'report', label: '形成可追溯成果', status: 'partial', completion: '仍有未知项。'},
      ],
      facts: [{status: 'known', fact: 'tuition', candidate_id: 'p_fixture'},
              {status: 'unknown', fact: 'eligibility', candidate_id: 'p_fixture'},
              {status: 'unknown', fact: 'subjects', candidate_id: 'p_fixture'}],
      missing_fact_count: 2,
      next_actions: [{priority: 1, tool: 'search_official_sources',
                      reason: '资格未知时优先核对当年招生简章与报考条件，保留 unknown。',
                      args: {query: '招生简章 报考条件 学历 专业范围', unit_id: 'u_fixture', year: 2027}}],
      gaps: ['[夹具] 报考资格仍未知'],
      budget: {remaining: {searches: 2, documents: 3, tool_calls: 9}},
      completion: {ready: false, statement: '当前成果可以查看，但仍有开放目标；不能把模型说完成当作研究完成。',
                   remaining_goals: ['evidence', 'report']},
      inheritance: {reused: true, profile_changed: ['max_annual_tuition']},
    },
    algorithm: {
      matching: {
        summary: {input_count: 1, matched_count: 0, unknown_count: 1},
        matches: [], excluded: [],
        needs_confirmation: [{program, subject_changes: {count: 0, uncertain: false},
                              reasons: [{message: '资格状态未知，不能写成符合。',
                                         evidence: [{url: 'https://example.edu.cn/catalog.html'}]}]}],
      },
    },
  },
};

const envelope = data => JSON.stringify({ok: true, data, error: null});
function serve(route, data) {
  route.fulfill({status: 200, contentType: 'application/json; charset=utf-8', body: envelope(data)});
}
const html = `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>N7 决策界面夹具</title></head>
<body><div id="e2e-fixture-label">浏览器测试夹具 · 模拟接口响应 · 非真实 AI / 元宝运行成果</div>
<main id="fixture"></main>
<script type="module">
import {mountAgent} from '/agent.mjs';
import {createApi} from '/data.mjs';
window.__cleanup = mountAgent(document.querySelector('#fixture'), {
  api: createApi(),
  initialTaskId: ${JSON.stringify(taskId)},
  onTaskChange(){}, onSettings(){}, onOpenProgram(){}, onCoverage(){}, onDiscover(){}, onKnowledge(){},
});
</script></body></html>`;

async function run() {
  server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://fixture');
    if (url.pathname === '/api/settings') {
      response.writeHead(200, {'content-type': 'application/json; charset=utf-8'});
      response.end(envelope({ai: {provider: 'fixture', model: 'fixture', has_key: false, status: 'not_configured'},
                             yuanbao: {provider: 'yuanbao', has_key: false, status: 'not_configured'}}));
      return;
    }
    if (url.pathname === '/api/research' && url.searchParams.has('limit')) {
      response.writeHead(200, {'content-type': 'application/json; charset=utf-8'});
      response.end(envelope({tasks: []}));
      return;
    }
    if (url.pathname === '/api/research' && url.searchParams.has('research_id')) {
      response.writeHead(200, {'content-type': 'application/json; charset=utf-8'});
      response.end(envelope(task));
      return;
    }
    if (url.pathname === '/api/research') {
      response.writeHead(200, {'content-type': 'application/json; charset=utf-8'});
      response.end(envelope(task));
      return;
    }
    if (url.pathname === '/' || url.pathname === '/index.html') {
      response.writeHead(200, {'content-type': 'text/html; charset=utf-8'});
      response.end(html);
      return;
    }
    const target = path.join(root, path.basename(url.pathname));
    if (!/\.(mjs|css|svg|png)$/.test(target) || !fs.existsSync(target)) {
      response.writeHead(404); response.end(); return;
    }
    const type = target.endsWith('.mjs') ? 'text/javascript; charset=utf-8'
      : target.endsWith('.css') ? 'text/css; charset=utf-8'
      : target.endsWith('.svg') ? 'image/svg+xml' : 'image/png';
    response.writeHead(200, {'content-type': type});
    response.end(fs.readFileSync(target));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = 'http://127.0.0.1:' + server.address().port;
  browser = await chromium.launch({channel: 'msedge', headless: true});
  page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  page.setDefaultTimeout(8000);
  page.on('pageerror', error => errors.push(error.message));
  await check('overview shows inherited evidence, changed conditions and stale candidates', async () => {
    await page.goto(base + '/');
    await page.locator('.ya-turn-changes').waitFor();
    const text = await page.locator('.ya-turn-changes').innerText();
    assert.match(text, /沿用上轮 1 个候选、2 份已核对来源/);
    assert.match(text, /条件变化：每年学费上限/);
    assert.match(text, /1 个上轮候选因年份或来源不再有效/);
    assert.match(text, /复用并重新核对/);
    assert.equal(await page.locator('.ya-field-count.is-verified').innerText(), '已核对 1');
    assert.equal(await page.locator('.ya-field-count.is-pending').innerText(), '待核 1');
    assert.equal(await page.locator('.ya-field-count.is-conflict').innerText(), '冲突暂停 1');
  });
  await check('field drafts keep quote, year, source and safe original links', async () => {
    await page.locator('.ya-field-candidates summary').click();
    const rows = page.locator('.ya-field-row');
    assert.equal(await rows.count(), 3);
    const verified = rows.filter({hasText: '两个独立原文一致'});
    assert.match(await verified.innerText(), /12000 元/);
    assert.match(await verified.innerText(), /各专业学费均为12000元\/年/);
    assert.equal(await verified.locator('a').getAttribute('href'), 'https://example.edu.cn/fees.html');
    const pending = rows.filter({hasText: '待逐项核对'});
    assert.match(await pending.innerText(), /报考条件：具有国家承认的本科毕业学历/);
    const conflict = rows.filter({hasText: '来源冲突，已暂停'});
    assert.match(await conflict.innerText(), /另一个来源给出15000元\/年/);
    assert.equal(await conflict.locator('a').count(), 0);
    assert.equal(await page.locator('a[href^="javascript:"]').count(), 0);
  });
  await check('candidate and methods evidence return to the saved original', async () => {
    const candidate = page.locator('.ya-candidate').filter({hasText: '样本大学'});
    assert.equal(await candidate.locator('.ya-candidate-links a[href="https://example.edu.cn/catalog.html"]').count(), 1);
    assert.equal(await candidate.locator('.ya-candidate-links a[href^="javascript:"]').count(), 0);
    await page.locator('[data-agent-tab="methods"]').click();
    await page.locator('.ya-plan').waitFor();
    assert.equal(await page.locator('.ya-algorithm-records .ya-original-links').count(), 1);
    assert.ok((await page.locator('.ya-algorithm-records').innerText()).includes('回到原文'));
    assert.equal(await page.locator('a[href^="javascript:"]').count(), 0);
  });
  await check('plan ledger shows checked facts, unknown gaps, next actions and remaining budget', async () => {
    const plan = page.locator('.ya-plan');
    assert.match(await plan.innerText(), /研究计划与缺口/);
    assert.match(await plan.locator('.ya-plan-facts').innerText(), /事实已明确 1/);
    assert.match(await plan.locator('.ya-plan-facts').innerText(), /仍未知 2/);
    assert.match(await plan.innerText(), /仍未知 2/);
    assert.match(await plan.innerText(), /资格未知时优先核对当年招生简章/);
    assert.match(await plan.innerText(), /检索词：招生简章 报考条件 学历 专业范围/);
    assert.match(await plan.innerText(), /剩余检索 2 次 · 文档 3 次 · 工具调用 9 次/);
    assert.match(await plan.innerText(), /仍有开放目标/);
    assert.match(await plan.innerText(), /不能把模型说完成当作研究完成/);
    const goals = page.locator('.ya-plan-goal');
    assert.equal(await goals.count(), 4);
    assert.match(await goals.filter({hasText: '补齐关键事实'}).innerText(), /部分完成/);
  });
  await check('decision surfaces stay usable at 390px without horizontal scroll', async () => {
    await page.setViewportSize({width: 390, height: 900});
    await page.locator('[data-agent-tab="overview"]').click();
    const dimensions = await page.evaluate(() => ({width: innerWidth, scroll: document.documentElement.scrollWidth}));
    assert.ok(dimensions.scroll <= dimensions.width + 1, JSON.stringify(dimensions));
    await page.screenshot({path: path.join(out, 'n7-decisions-390.png'), fullPage: true});
    await page.setViewportSize({width: 1440, height: 1000});
    await page.screenshot({path: path.join(out, 'n7-decisions-1440.png'), fullPage: true});
  });
  await check('no JavaScript errors escaped the decision surfaces', async () => {
    assert.deepEqual(errors, []);
  });
}

async function check(name, work) {
  const started = Date.now();
  try {
    await work();
    results.push({name, passed: true, ms: Date.now() - started});
    console.log('PASS ' + name);
  } catch (error) {
    results.push({name, passed: false, ms: Date.now() - started, error: error.stack});
    console.log('FAIL ' + name + ' — ' + error.message);
    if (page) await page.screenshot({path: path.join(out, 'failure-' + results.length + '.png'), fullPage: true}).catch(() => {});
  }
}

run().catch(error => {
  results.push({name: 'harness', passed: false, error: error.stack});
  console.error(error);
}).finally(async () => {
  if (browser) await browser.close();
  if (server) await new Promise(resolve => server.close(resolve));
  const report = {
    base, mode: 'isolated_fixture', at: new Date().toISOString(),
    success: results.length > 0 && results.every(item => item.passed),
    results, errors,
    notes: ['The page and every API response are fixtures served by this script; no real DB, Key or provider was used.',
            'No fixed port is used; the server binds 127.0.0.1:0.'],
  };
  const reportPath = path.join(out, 'n7-browser-results.json');
  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
  console.log(JSON.stringify({success: report.success, passed: results.filter(item => item.passed).length,
    failed: results.filter(item => !item.passed).length, report: reportPath}));
  process.exitCode = report.success ? 0 : 1;
});
