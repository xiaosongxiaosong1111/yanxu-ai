# 交接说明（给下一个 AI）

更新：2026-11-09，包含官网发现及报考事实进入算法的实现与最终验收。
仓库：`https://github.com/xiaosongxiaosong1111/yanxu-ai`。Windows 开发，Python 虚拟环境与前端原生 ES modules。

**当前并行分工：Claude 补全国采用数据，Codex 测官网。** Claude 请先读 [全国数据实施手册](CLAUDE_NATIONAL_DATA_RUNBOOK.md)，按节点0建立独立工作树、核对真实基线，再以每批10校推进；逐批复制 [操作日志模板](work_nodes/NATIONAL_DATA_BATCH_LOG_TEMPLATE.md)。官网发现、共享抓取连接与来源绑定模块由Codex负责，数据分支不要同时修改。全国数据缺口尚未补齐，文档中的任务节点不是完成记录。

最新一轮请先读 [OFFICIAL_DISCOVERY_CLOSURE.md](OFFICIAL_DISCOVERY_CLOSURE.md)：新增可重放的学校官网归属、公开导航发现，以及具体方向的资格/学费/学制原文事实进入现有算法。此前“官方原文→当轮临时候选”见 [ON_DEMAND_CANDIDATES.md](ON_DEMAND_CANDIDATES.md)。其中 B 节的补证闭环现已支持明确范围和有限条件语法，不能据此宣布资格通用解析或全国目录补齐；复杂表格、关联套组及旧的完整语义评测未完成状态继续保留。最终测试数量与评测数以 `VERIFICATION.md` 最新段落和对应原始产物为准。

此前独立验收基线为 Python 121 模块、1,988 项（1,985 通过、3 跳过，ONNX 内存失败的原组 7 项独立复跑通过）、Node 254、隔离浏览器 5；当时 15 个核心及修改模块含分支综合 92.90%。这些是历史基线，不是本轮最终总数。全国库、个人知识库或长期进程的验证不能用隔离结果替代。

本轮最终为 Python 129 模块、2,182 项（2,179 通过、3 跳过）、Node 264、隔离浏览器 11；405 项固定代码回归另行通过，九个相关核心模块含分支综合 90%。72 个单位分别保留官方入口、目标公告、正文和准入保存状态；20 个发布账号不能当作独立官网，3 个人工补正入口不能当作自主发现提升。详见 `VERIFICATION.md` 顶部最新记录。

此前整体审计、深度修复和历史后续顺序见 [SYSTEM_AUDIT_20261109.md](SYSTEM_AUDIT_20261109.md)。其中“原文尚不能形成计算方向/字段尚不能参与算法”的旧能力描述应结合两份闭环文档阅读；审计的验证边界，尤其是内存不足导致未完成的完整语义评测，仍然有效。下文 AD-18、第一次七项修复及真实运行数量属于历史交接，不应替代本轮代码和最新验收状态。

上一轮深度后端修复为 `7c89923`，交互修复为 `18fa99c`；当时后端 114 模块、1,902 项（3 跳过），前端 244 项通过。这是更早的历史基线。下一步优先按真实失败类别补官网/附件链路、扩展有明确证据的资格条件及全文维护队列，不重复把本轮已完成的严格字段路径当作尚未实现。

## 0. 本轮接手入口

- **实现位置：** `official_authority.py`、`public_navigation.py`、`research_official_tools.py` 负责官网归属和有界发现；`admission_facts.py`、`research_admission_tools.py` 负责逐字段原文证明、采用、重放与恢复。`research_tools.py` 注册 `discover_official_entry` / `propose_admission_facts`，研究服务和历史读取均接入失效复核。
- **先查真实流程：** `find_units` → 官网发现/原文读取 → 知识库搜索与完整片段精读 → 事实引用草案 → 确定性校验 → 比较/预算/换科及报告；没有新增同名 REST 接口。参数、运行命令和证据状态见新闭环文档。
- **信任边界：** 新入口归属保存原件与 SHA、路径/端口/账号范围，最长 30 天，可撤销并重放；已缓存或归属通过不表示刚刚联网核实。字段仍需当前规范全文、实际送达的已读凭证、同校同年度和完整适用范围。缺失、失效、冲突及未解析资格不能变成符合。
- **学费与资格：** 新的严格规范原文路径允许 `verified_single_source`，旧 `research_fields.py` 摘要路径仍须两份独立原件；资格有 `partial` / `needs_review`，普通本科条款不证明完整。当前可信但无法执行的适用限制会保存待核证明，防止继续任务时丢掉限制。
- **真实原件回放：** 北京服装学院 2027 章程首个明确 `140300 设计学` 收费行支持 8000 元/年；未展开 rowspan 与含例外的学制继续未知。临时知识库回放和浏览器假 API 验收不等于真实付费 AI 任务成功。
- **全国覆盖未变化：** 静态包仍为 135 校、22,356 个方向，年度确认 931 个独立单位（通用索引 936）；本轮临时方向和补证事实不加入这些统计，也不改正式发布库或用户真实运行库。
- **验收仍在收尾：** 本机产物根为 `D:/CodexRuntime/yanxu-closure-upgrade-20261109/`。先看 `coverage/` 各 `report.json` 的代码 SHA、起止时间、是否运行中改码与逐校失败，不用 32+40 中途汇总当最终覆盖率；`facts/coverage.json` 的约 96% 只指事实校验模块的语句覆盖。主验收将补最终整库与界面结果。
- **最小回归：** `python -X utf8 -m unittest tests.test_official_authority tests.test_public_navigation tests.test_research_official_tools tests.test_admission_facts tests.test_research_admission_tools`；前端 `node --test tests/official-closure.test.mjs` 与 `node tests/browser-official-closure.cjs`。使用已安装依赖的隔离环境；缺 `ijson` 是环境问题，不应为通过测试改冻结数据读取逻辑。更完整命令和不联网录制重放见新文档。

## 1. Git 状态（先看这里）

- AD-14～AD-18 已在 `b204abc`、`c9166d4`、`a8730a4`、`c393bb9` 四个提交中发布到 `main`。原说明中的“4f62ee8、所有改动未提交”属于提交前状态，已经失效。
- 随后的独立验收发现 7 类边界问题，本次修复覆盖这些问题及资料补查转入 Agent 时的意图继承。当前检出的准确提交与同步状态请运行 `git log -5 --oneline`、`git status --short --branch`，不要把文档中的历史 SHA 当作当前 HEAD。
- 本次在独立工作树修复和测试。Claude 原开发路径为 `E:\PythonSpace\yanxu-ai`，其他电脑应使用自己的实际检出路径，不复制用户设置库或凭据。
- 真实知识库的全文分段补建、别名证据扩充和目录索引重新发布仍是后续工作，见第 5 节。

## 1.1 本次独立验收修复

1. 页面搜索与 Agent 使用同一个 `official_source_url`，错校研招网 `schId` / `schCode` 和未核准子域不再标为本校官方命中。
2. 校名推断按命中位置消除嵌套名称，多个独立学校名称不按全局长度擅自缩成一校。北京大学医学部等嵌套正例继续保留。
3. 缓存原文区分未知链接与已经提取的空链接列表。未知状态允许在预算及学校范围内补读，已知空列表避免重复读取。
4. 简称零命中展开适用于整个分页序列，直接访问第二页同样保留正确总数、范围和展开提示。原查询已有命中时不展开。
5. 前端分析过滤包含学习方式、学位类型及与目录查询一致的专硕/学硕/非全同义词，不再丢弃后端已经命中的候选。
6. 每次实际搜索响应的拒绝及非法项累计统计，后续空结果和 Site 回退不再覆盖早先计数。同一 URL 在不同请求中分别被拒绝时分别计数，展示结果仍去重。
7. 官网扩展绑定逐行验证 `domains` / `aliases` 集合类型，坏行不会清空其他有效行。
8. 资料补查点击“用研究 Agent 继续查”时，继承学校、实际查询年度和所选学费/复试/调剂等资料类型。

验证结果与边界见 `docs/VERIFICATION.md` 中的“2026-11-09 独立验收修复”。

## 2. 最近这批改动（AD-18，用户视角）

### Skill
- `skills/research-official-sources/SKILL.md`、`skills/verify-evidence/SKILL.md`
  - 写明 `binding_missing`、`attribution_unverified`、`third_party_only`、
    `domain_mismatch`、`index_no_hit` 的含义，并明确**都不等于“官网没有资料”**；
  - 候选域名/第三方页面只作线索，不进证据台账。
- `research_plan.py:126-144`
  - `research-official-sources.checked` 只认带实际年度的
    `official_original_read` / `published_snapshot`；
  - `search_lead` 再有链接和年度也不能把 Skill 标成 checked。

### 知识库
- `knowledge_store.py:454` `stored_years()`
  - 返回库中实际存在的年度，排除已撤销状态；
  - 只作库存提示，不代表查询在那些年度有命中。
- `research_tools.py:698` `search_knowledge`
  - 首次零命中且查询含已复核简称时，用规范校名重试一次；
  - 响应带 `requested_query` / `effective_query` / `alias_expanded`；
  - 零命中不返回其他年度资料，只给 `stored_years` 提示。
- `research_tools.py:764` `search_segments`：同样的简称零命中回退。
- `research_service.py:290` `_reuse_knowledge`
  - 系统预取不再把学校名丢掉：查询唯一指向一所学校时带 `unit_id` 预取；
  - 不越过用户已选范围；真实运行中修掉了“国科大任务混入 6 所其他学校证据”。
- `research_tools.py:297` `infer_unit`
  - 正式校名 + 已复核简称匹配；嵌套校名取最长（北京大学医学部 > 北京大学）；
  - 两个不同学校同级命中视为歧义，保持全局预取。
- `service.py:368-399` `knowledge_get`
  - 页面 `/api/knowledge` 与 Agent 同规则：零命中才重试一次（最初仅第 1 页，本次已修复为整个分页序列），
    返回 `query_expansion` 供页面显示。
- `static/knowledge.mjs`
  - 新增 `state.queryExpansion`（第 138 行）；
  - `loadList` 保存服务端 `query_expansion`（第 185 行），新请求开始时清除（第 176 行）；
  - `filtersView`（第 80-84 行）显示“只是检索词变化”和 `requested → effective`，全程转义。

### 文档与测试
- `docs/OPTIMIZATION_PROGRAM.md`：AD-18（含系统预取第 5 条）与 2026-11-06 真实运行记录。
- 新增/修改测试：
  - `tests/test_research_plan.py`：搜索线索不能把 Skill 标成 checked。
  - `tests/test_research_service.py`：Skill 文件必须含全部失败阶段且 ≤20,000 字节；预取按校名加范围。
  - `tests/test_knowledge_agent_tools.py`：分段简称回退；`infer_unit` 简称/嵌套/歧义。
  - `tests/test_knowledge_retrieval_regressions.py`：2027 查询不泄漏 2026 资料；简称只在零命中展开。
  - `tests/test_knowledge_http.py`：页面零命中重试一次、非简称不重复；本次追加完整翻页、直达第二页及学校/年度范围回归。
  - `tests/knowledge.test.mjs`：展开提示渲染与转义。

## 3. 真实运行记录（2026-11-06）

服务：`127.0.0.1:4180`，本机已保存的模型与元宝连接。

- 任务 `r_1baa279366964d42a80ff42ab4c6caef`（国科大 计算机 2027 招生目录 考试科目）
  - 12 候选、13 证据、状态 `awaiting_input`，1 次元宝检索，15,155 tokens；
  - 暴露系统预取混入其他 6 所学校证据 → 已修复。
- 任务 `r_7c05b4bc4bfe4407bb83123b4763d6e6`（南开大学 计算机 408 复试 调剂规则）
  - 第五份 Skill `assess-transfer-options` 正确加载；证据只剩南开大学；
  - 43,464 tokens 达到预算上限，状态 `partial`，保留 12 候选；
  - 本轮 `searches=0`，未再消耗元宝。
- 两次运行按应用正常流程向**真实知识库**新增了资料条目（搜索线索与已发布目录依据）。
  未手工改写目录库、未改冻结资料/金标/采集器。
- 真实库现状：`knowledge_entries` 44+，`knowledge_segments=0`。

## 4. 验证结果

- Python 整库：`./.venv/Scripts/python.exe -X utf8 -m unittest discover -s tests`
  → **1,839 项通过（跳过 2）**。
- Node 整库：`node --test tests/*.test.mjs` → **241 项通过**。
- 无头 Chrome（`--headless=new --dump-dom`）：
  - 知识库列表、`#/knowledge?q=国科大` 的展开提示（“国科大 → 中国科学院大学”）、
    研究报告页均正常渲染，未发现脚本错误。

## 5. 未验项与已知缺口（不要当成已解决）

下面的真实库数量与运行状态来自 2026-11-06 的实施记录，本次隔离验收没有重查或维护用户真实库。不要把临时测试库的成功写回这些实际数量。

1. **混合检索空结果**：真实库没有全文分段（`knowledge_segments=0`），
   `/api/knowledge/retrieve` 只能降级关键词并返回 0 片段。需要先把原文完整入库并建立分段。
2. **别名表只有两校**（国科大、社科大）。其他简称仍按原名检索，不能据此猜学校。
3. **`study_mode` / `degree_type`** 已写入新搜索文本，但**当前已发布索引是旧文本**；
   要等下一次按发布流程重建并复核 936 个单位后才支持“非全日制”等词。
4. 未做真实浏览器逐页点选（只做了无头渲染）；未复测移动端/键盘/对比度。
5. `stored_years` 只是库存提示，不代表查询命中；不要把历史年度当目标年度。
6. 冻结金标、已绑定采集器、真实 Key、4180 之外的付费调用维持原约束：
   不改冻结资料、不打印 Key、服务只监听回环地址、Poppler 不加入 PATH。

## 6. 历史提交拆分（已在 c393bb9 前完成）

1. `feat(official): share discovery ladder and evidence-backed bindings`
   - `official_search.py`、`unit_bindings.py`、`ai_transport.py`、`public_sources.py`、
     `catalog_index.py`、相关测试与 `tools/audit_search_coverage.py`。
2. `feat(research): align agent and page search, budgets and failure stages`
   - `research_service.py` 的预取范围、`research_tools.py` 的简称/年份、`service.py`、
     `skills/*`、`research_plan.py`、`knowledge_store.py`。
3. `feat(ui): expose query expansion and official source boundaries`
   - `static/knowledge.mjs`、`static/agent.mjs`、`static/application.mjs`、
     `static/data.mjs`、`static/*.css`、`tests/knowledge.test.mjs`、`tests/agent.test.mjs`、
     `tests/frontend.test.mjs`。
4. `docs: record AD-18 and real-run verification`
   - `docs/OPTIMIZATION_PROGRAM.md`、`docs/RUNNING.md`。

以上四个提交已完成。后续修改仍需重新运行相应测试，提交前检查差异及未跟踪文件。
