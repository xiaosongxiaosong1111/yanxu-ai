# 全国采用数据 · 节点0修复与验证

> 本页保留节点0的历史基线和当时状态。节点1已执行；最新采用、测试、已知缺口与下一步请读[本轮报告](node1/REPORT.md)。不要用下文的旧“未启动”状态覆盖新结果。

状态：`node0_entry_boundary_fixed`；只读基线和入口证明修复完成，**节点1未开始采集**。

基于 `0b4d8f6`，代码及基线生成提交 `f4f01e624fea4039fcf25da6ed5317012e3ff581`，生成时 `tracked_dirty=false`。后续文档提交不修改生成器。完整覆盖率统计包含三个生产模块，源码哈希按UTF-8/LF保存；五个保护适配器与base `67d5e24`字节不变。

## A. 本轮实际修复

| 问题 | 共享修复 | 验证 |
| --- | --- | --- |
| 端口与编码路径被合并 | 保留非默认端口，只规范化unreserved百分号；拒绝userinfo与非法端口 | 默认端口/同一8443正例，异端口/编码斜杠负例 |
| 外站URL参数中的网址冒充目标 | 完整ASCII URI和IRI外层范围；查询子串不作为独立目标 | ASCII子分隔符、中英文括号及Unicode查询负例；独立括号正例 |
| 较早本校名盖过最近外校 | 最近明确实体优先；不以共有名称片段核准身份 | 四类角色、共有“师范大学”后缀负例；真实发布指引正例 |
| 隐藏祖先、inline空白漏判 | HTMLParser保留正文容器祖先可见性；严格匹配inline声明与注释 | 容器/祖先/嵌套hidden、空白及注释；可见正文反向对照 |
| hidden void吞掉后文 | void只处理自身，非void斜杠保留HTML容器语义 | input/img/br及父容器正反对照 |

新增48项回归；初始29项中的15个失败断言已转绿，后续真实原文与边界对照一并补入。关键断言覆盖“真实原件→证明→队列指纹→最终入选”。旧111项保留；一条将根URL错误等同编码句号路径的旧断言已纠正为拒绝。真实原文试跑曾出现11条正常入口误拒，已按中文正文边界和普通发布指引修复并补正例。最终931条逐条status/reason相对0b4d8f6无差异：400 verified、531 rejected；未靠学校或域名白名单维持计数。9行证明的轨迹字段有变化：5行由link_target改为literal、4行角色引文边界调整，对应指纹重算；状态与原因无变化。见[逐行差异](proof-delta.json)、[初始RED日志](regressions-red.txt)。

模块：

- [HTML/URL](../../../national_entry_html.py)：可见性、正文选择、完整目标。
- [归属证明](../../../national_entry_proof.py)：角色、最近拥有者、年度原件SHA重放。
- [审计入口](../../../national_data_baseline.py)：统计、队列、CLI，兼容旧调用入口。
- [新增回归](../../../tests/test_national_entry_regressions.py)。三个生产文件均少于800行。

## B. 冻结公开包重算

完整CLI退出码0，用时 **200.894秒**；输入文件前后SHA完全一致。只恢复和读取既有公开包，没有联网采集、模型/元宝调用、采用、发布或个人runtime/Key操作。pdftotext只加到本次子进程PATH。

| 指标 | raw | effective-present | verified |
| --- | ---: | ---: | ---: |
| 年度确认单位 | 931 | 931 | 931 |
| 有目录记录的单位 | 135 | 135 | 135 |
| 方向/唯一ID | 22,356 | 22,356 | 22,356 |
| 学费 | 650 | 912 | 912 |
| 学制 | 1,680 | 1,942 | 1,942 |
| 可算全程 | 645 | 907 | 907 |
| 完整资格 | 0 | 0 | 0 |

费用增加262全部来自既有7条规则（郑州252、南开10），不是本轮采集增长。目录仍为51 recorded_verified、84 partial、796 not_collected；完整资格仍待节点3。原始年费378＋全程272，补充后年费640＋全程272。

公开包SHA `637f1990c02435127cca3c7a308b08087e24db40d8aefba41290fad294936478`，manifest SHA `52655a92d671025e9cfb086e3a528d610d52ed53d09fd2c3f1b9847a87bf1394`。实际执行见 [cli-run.json](cli-run.json)、[原始输出](cli-recompute.log)。effective路径仍完整加载JSON，本轮没有宣称已实现有界内存或全浏览器渲染。

## C. 证明、队列与选批

931条证明：400 verified／531 rejected。扩目录池433：131 trusted／302 untrusted。531个被拒单位保留结构化原因，不等于没有官方网站。完整queue与摘要分开，摘要`replayable=false`；重放比较7+3完整对象，证明指纹931条一致。

完整queue SHA `7af36409e732bdb5c1f4357a345a0e9b63db4f0c5ddaad6ffd62d5541d3ab89e`；batch绑定该SHA与选择规则`b001-node0-v5`。原样产物见 [baseline](baseline.json)、[queue](queue.json)、[摘要](queue-summary.json)、[batch](batch.json)、[重放记录](replay-check.json)。

| unit_id | 单位 | 角色 |
| --- | --- | --- |
| 368295 | 山东农业大学 | expand_catalog |
| 368049 | 大连工业大学 | expand_catalog |
| 368146 | 上海大学 | expand_catalog |
| 368275 | 山东艺术学院 | expand_catalog |
| 368611 | 新疆农业大学 | expand_catalog |
| 368142 | 华东理工大学 | expand_catalog |
| 368436 | 广西大学 | expand_catalog |
| 367929 | 中国科学院大学 | backfill_fields |
| 367859 | 北京师范大学 | backfill_fields |
| 368201 | 南京林业大学 | backfill_fields |

本表只说明已排队，不说明该校目录或资格已采集完成。后续按最终URL、MIME、SHA、年度及目录内容核对，URL数量不当作独立原件数。

## D. 验证与明确限制

- 相关测试：**159通过**；三模块纯分支 **388/416=93.27%**，综合覆盖95.45%。每个模块纯分支均大于80%。[执行日志](targeted-tests.txt)、[覆盖记录](coverage-evidence.json)。
- Node：**264通过**，18个前端测试文件；本轮没有前端代码改动。
- 整库Python：2332项，2316通过、11跳过、2失败＋3错误。5项全部因隔离副本缺少本地BGE模型，在未改动基线逐项复现；embedding源码和测试未改，不为凑通过改变这些测试。最终变更入口逻辑另由最终159项回归覆盖。此项不能报告为整库全绿。[环境基线证明](embedding-environment-baseline.json)。
- requirements及解析出的依赖：pip-audit未发现已知漏洞；项目没有npm清单，未套用npm依赖审计。[审计结果](dependency-audit.json)。
- 独立代码/安全复查通过，计划内范围未发现剩余P1/P2；[审查记录](CODEX_INDEPENDENT_REVIEW.md)、[摘要](independent-review-summary.json)。
- 没有启动或重启线上服务，没有合并main，没有进行用户真实研究、付费模型或元宝端到端联调。

源码指纹：

| 文件 | SHA256（LF） |
| --- | --- |
| `national_data_baseline.py` | `364cca3e8260becda5eb84e79686d4d5df7ce0390e67d9b86a205771fe74510a` |
| `national_entry_html.py` | `c42322d6964623242cff5dcfafe68a70abcffac0b8c0cb4df0e75574cebd09b8` |
| `national_entry_proof.py` | `190010b2969653ebce77ecefa9fd43edbc43063224fa71e2453832fbe5014d07` |

## E. 资格持久化最小方案（节点3 实现，本节点只设计）

目标不是把布尔值改真，而是让“条款 → 原文 → 适用范围 → 可执行条件 → 采用/失效”可回放。建议新增**永久文件** `data/qualification_adoption.json`（schema_version 1），不改 `admissions.json` 的既有字段语义。

### E1. AST 必须复用现有语法

不要引入 `{"op":"and","args":[...]}` 或 `{"when": ..., "then": ...}` 这类当前引擎不认识的语法。`grad_engine._condition` 只接受 `all/any/when/field/op/value/evidence`；`when` 是**守卫**，其后果必须写在同一个对象的其余字段（或同一对象的 `all/any`），不存在 `then` 键：

```json
{"all": [
  {"field": "education", "op": "eq", "value": "bachelor", "evidence": "..."},
  {"any": [{"field": "background", "op": "in", "value": ["计算机科学与技术"], "evidence": "..."}]},
  {"when": {"field": "study_mode", "op": "eq", "value": "part_time", "evidence": "..."},
   "field": "work_years", "op": "gte", "value": 2, "evidence": "..."}
]}
```

引擎语义：守卫 `excluded` 时该条件视为“不适用”（matched）；守卫 `unknown` 且后果未 matched 时整体 unknown；守卫 matched 时按同级后果判断。持久化 AST 必须使用上述键；字段白名单以现有 `grad_engine.PROFILE_FIELDS` 为上限。当前档案输入不含毕业时间、专项、境外学历、加试，因此这些条件只能保留完整原句并进入 `unknown`，不能仅凭 AST 非空设置完整资格。

### E2. 目录证明与资格证明分层

不能先把 `eligibility_evidence_complete/requires_review` 覆写进目录 payload 再走现有目录回放：`service_verification.py:548-549` 对 `ucas_public_v1` 会同时比较这两个值与原目录解析结果，overlay 会让原本有效的目录回放变成 false；`collectors.publish_catalog.py:136-137` 会把方向标为 invalid。正确做法是保留两个证明边界：

- 目录回放只验证已证明的目录字段，原始目录事实不变。
- 独立资格 verifier 重放作用范围、完整条款集、AST、采用版本和原件 SHA，再决定资格视图。
- 国科大必须增加“原目录仍有效 + 独立资格有效”的回归；资格撤销时必须“资格退回 unknown、目录保留”。
- 现有 SQLite `programs.payload` 已存程序字段，算法/证据界面读取 `conditions` 及其 evidence。若新增 `program_qualification` 表，必须明确 schema 升级、sync 事务、读取 join/视图、旧行清理和旧库兼容；没有真实消费者的新表不能当作已接入。

### E3. 原文依赖闭包必须可执行

条款不能只写 `document_id/sha256`：

- 资格仓库必须有独立 `documents` 列表并合入快照 `documents`（参照 enrichment），或共用明确的 document resolver；原件、提取文本、发布父页和适用性依赖必须递归闭合。
- `snapshot_cache.py:42-58` 只扫描 `local_path/snapshot_path/text_path`；把 JSON 加入 `names` 只能跟踪采用文件字节，不会自动解析 `document_id` 到新原件。
- `tools/build_public_bundle.py` 的 `NAMES` 只打包采用文件；仅以 ID 引用的正文/父页/提取文本需要额外登记，否则全新恢复会缺依赖。
- 缺失、SHA 不符、文件撤销、父页替换必须 fail closed，阻止完整采用并改变 revision。
- 正式发布要在同一已核快照中验证并写入 `checked` 内容，再写同一事务的 SQLite；不能事后往已发布库补表。
- 必须实测“空目录恢复 → 同版本重放 → 查询/算法/证明一致”，以及篡改依赖后不能继续显示已核资格。

### E4. 采用门禁（任一不满足即 `complete=false`、`requires_review=true`）

1. 方向身份必须来自已采用目录（单位/年度/专业代码/学习方式一致），不能凭空生成 `program_id`。
2. `document_id/sha256/quote/locator` 必须能由当前原文重放；`read_receipts` 只能来自真实已读凭证。
3. 适用范围非空且有原句/父标题依据；禁止一校条款广播全国，禁止把空数组当“全部”。
4. 条款全集必须对账：该校当年度适用章节、学院、专业备注、例外均纳入；单条可核不等于整专业资格完整。
5. 存在 `unknown/conflict/superseded`、原文 SHA 变化、撤信、更正公告或范围变化时，停止完整采用；冲突双方均暂停，旧版本和审计保留。
6. 规则可核验 ≠ 个人符合；输出保持匹配/不符/未知三态，未知不得变成“符合”。

在节点3 实现前，本方案只是设计，不产生任何资格增长；本轮未写 `admissions.json`、未改任何完整资格标志或 `requires_review`。


## F. 下一步边界

节点0本轮修复完成；节点1仍未启动，可按既有手册另行执行公开原件清单与目录差集。所有未取到/未核准项保持可见原因。资格持久化、采用和发布继续按E节独立验收，不能将159项入口回归等同全国目录已补齐或资格系统完成。


## 后续状态（2026-11-10）

节点1后续实现、真实首批采集、独立审校及公共包验收见[本轮报告](node1/REPORT.md)。本报告与基线、queue、batch的原始指纹保留为节点0历史；不要用新公共包统计覆盖该历史基线。
