# 全国采用数据 · 节点0报告（b001，R2 真实原件核验重交）

状态：`baseline_ready_r2_resubmitted`（工作状态，不等于任何学校目录或资格已完成）
生成时间：2026-11-09（本机时区 UTC+8）
执行者：Claude（数据）；独立验收：Codex
审查对象：`data/national-b001`，base `67d5e24`；被审查 HEAD `598bb09`
R2 门禁修复代码提交：`256327e`；生成提交（含 LF 绑定修复）：`52e99ff`（生成基线时 `tracked_dirty=false`）
证据提交：包含本文件与全部产物的 `data/national-b001` 分支 HEAD（SHA 见交付消息；不把提交自身 SHA 写进提交内容）
手册：[CLAUDE_NATIONAL_DATA_RUNBOOK](../../CLAUDE_NATIONAL_DATA_RUNBOOK.md)（已在本分支仓库内）

## 0. 本轮修正摘要（只补 R2 与文档小修）

| 审查项 | 修正 | 可复核位置 |
| --- | --- | --- |
| R2 入口证据只查非空元数据 | 新增真实原件重放链：年度原件 URL+SHA 精确匹配、`annual_master_charter` 类型、单位身份、落盘原件文件名绑定、文件实际字节 SHA、引文在原件中逐字出现；结果作为持久化 `entry_verification` 证明写入队列，`entry_trusted` 只认指纹一致的证明 | `verify_entry`、`_entry_fingerprint`、`entry_trusted`、`_saved_original`、`_identity_matches`；`EntryProofTests` |
| 零 SHA / `rejected` / `failed` / 删字段仍入选 | 分别落为 `invalid_evidence_sha256`、`unsupported_verification`、`no_exact_annual_document`、`no_entry_evidence`；换原件字节落为 `saved_original_sha_mismatch`；全部为 `rejected`，不再返回 true | `verify_entry` 原因码顺序；`EntryProofTests`、`SelectTests` |
| 只有扩目录池有门禁 | 扩目录池与补字段池都要求 `entry_trusted`；补字段池新增 RED/GREEN（未核验入口不得入选） | `select_batch`；`test_backfill_pool_requires_a_trusted_entry` |
| 文档：`then` 语法不存在 | E1 改为现有引擎语法：`when` 是守卫，后果字段与 `when` 同级，不再写 `then` | 本文件 E1；`grad_engine._condition` |
| 文档：覆盖率证据不可绑定 | 覆盖率报表改为 LF 字节 + Git blob SHA-1 双绑定，并说明 `core.autocrlf` 的 512 字节差异来源 | `coverage-evidence.json`；`coverage-report.txt` 头部 |
| 文档：HEAD 表述混用 | 全文区分“被审查 HEAD `598bb09`”“R2 门禁提交 `256327e`”“生成提交 `52e99ff`”“证据提交（本文件所在提交）” | 本文件头、A、I |

**未重做**：R1 部分覆盖、R3 verified 逐条重放、R4 queue→batch 重放、费用/学制逻辑均未改；三口径数字与上一轮完全一致。选择规则版本因补字段门禁升级为 `b001-node0-v4`；队列内容未变，规范 LF 字节绑定后 queue SHA 为 `12dd9489…`（此前报告记录的是 Windows CRLF 工作字节 SHA `f19cfcf1…`，两者内容相同、换行不同）。

## A. 身份与隔离

| 字段 | 实际值 |
| --- | --- |
| 仓库 / 分支 / base SHA | `https://github.com/xiaosongxiaosong1111/yanxu-ai`，`data/national-b001`，`67d5e24` |
| 被审查 HEAD | `598bb09`（Codex 验收不通过的提交） |
| R2 门禁修复代码提交 | `256327e`（`fix: gate backfill selection on replayed entry proof`） |
| 生成提交 / LF 绑定修复 | `52e99ff`（`fix: write generated JSON with canonical LF bytes`） |
| 生成时工作区 | `tracked_dirty=false`；`baseline.json.git.commit=52e99ff` |
| 独立工作树 | `D:/yx-data-b001`（短路径；未复用 `E:/PythonSpace/yanxu-ai`，未碰 `D:/CodexRuntime`） |
| 数据根 / 产物根 | `D:/yx-data-b001` / `D:/yx-data-b001/data/batch_runs/b001` |
| 用户 runtime / Key | 未读取、未复制、未覆盖；本工作树没有 `data/runtime/` 发布库 |
| Python | 3.13.9（工作树 `.venv`；requirements 已装 ijson 3.4.0.post0、pdfplumber 0.11.10、xlrd 2.0.2；coverage 只装在隔离 venv） |
| 原文提取器 | `C:/Program Files/Git/mingw64/bin/pdftotext.exe` → `pdftotext version 4.00` |
| 坐标提取器 | `E:/miniConda/envs/yanxu-pdf/Library/bin/pdftotext.exe` → `pdftotext version 26.07.0`；未加入 PATH |
| 公开包 | `data/bundles/catalog-data.zip` SHA256 `637f1990c02435127cca3c7a308b08087e24db40d8aefba41290fad294936478`；manifest SHA256 `52655a92d671025e9cfb086e3a528d610d52ed53d09fd2c3f1b9847a87bf1394` |
| 恢复结果 | `prepare_data.py` 离线恢复 6,521 个文件，`published=false` |
| 付费搜索 / 模型调用 | 0 次；未读取任何 Key，未访问 4180 |

五个受保护 PDF 适配器字节与 base `67d5e24` 及上一轮一致（SHA 见 `protected-sha256.json`）：

| 文件 | SHA256 |
| --- | --- |
| `collectors/catalog_pdf_tables.py` | `cbaf2b3a78822e6558f939e8df4a8cf0660577223c3f873be2f3bd450a94d384` |
| `collectors/catalog_pdf_variants.py` | `76583e49222980947a061fad641742a07fe85103b20a9759194c811384718971` |
| `collectors/catalog_pdf_national.py` | `19772baec461b81d21ee2bc555193e7248ca1d9a2ec85bf0c4e12e808c97c55e` |
| `collectors/catalog_pdf_national_v2.py` | `40528ad943d2557c7b5855a3afeceb44dffb47f528a90aaccd83c9804daa0d9d` |
| `collectors/catalog_hit_public.py` | `e757752d9a707007288d55417318a00d947bbe30c55f124a82742cd8231f5f1e` |

## B. 基线：raw / effective-present / verified 三种口径

年度分母是 **2027 年度确认的 931 个独立单位**（通用索引 936 条中 5 条 `not_listed` 不计入）。目录、资格、费用、学制分别统计；任何一列都不能替代另一列。

- **raw**：`admissions.json` 原样流式统计（`ijson`，`use_float=True`），只接受结构有效值：学费金额为有限正数，学制为有限正数；资格标志为 true **且** `conditions` 非空。它表示“原始包已有可用字段”，不表示原文已核验。
- **effective-present**：执行既有 `apply_enrichment` → `apply_evidence_safety` 后，同样按结构有效值统计 present。它表示“经过既有规则和冲突暂停后字段仍在”，仍不表示逐条证据已核验。
- **verified**：对 effective 数据逐条调用 `EvidenceIndex._claim(..., verified=True)`。学费/学制要求 claim 状态为 `verified`，且金额有限正数、周期合法、学制正数；完整资格还要求 `conditions` 非空、标志 true、无 `requires_review` 且 claim 为 `verified`。只有这一列用于队列缺口。

| 指标 | raw | effective-present | verified |
| --- | ---: | ---: | ---: |
| 年度确认单位 | 931 | 931 | 931 |
| 有目录记录的年度单位 | 135 | 135 | 135 |
| 专业/方向 | 22,356 | 22,356 | 22,356 |
| 完整资格 | 0 | 0 | 0 |
| 其中有任何条件原句 | 0 | 0 | — |
| `requires_review=true` | 22,356 | 22,356 | — |
| 学费（任一周期） | 650 | 912 | 912 |
| 年费 | 378 | 640 | 640 |
| 明确全程（full/total） | 272 | 272 | 272 |
| 学制 | 1,680 | 1,942 | 1,942 |
| 可算全程费用 | 645 | 907 | 907 |
| evidence 冲突暂停 | — | 0 | 0 |

口径解释：

- 本轮代码提交只改了入口证明与选择门禁，三口径逐项与上一轮相同：650/912/912、1,680/1,942/1,942、645/907/907、完整资格 0。
- `912 − 650 = 262` 全部来自既有白名单补充（郑州大学 252、南开大学 10；7 条规则全部有效，`invalid_rule_ids=[]`）。既有 650 条在有效数据中仍逐条重放为 verified，未因补充被降级。
- 本次 `verified` 的 claim 状态计数：学费 `{verified: 912}`、学制 `{verified: 1,942}`、资格 claim `{}`（无任何条件原句）。没有 `needs_review`/`conflict` 被算入 verified。
- 完整资格仍为 **0**：22,356 条 `conditions` 均为空。`eligibility_evidence_complete=true` 单独出现不会计数，本轮也未改写任何标志。
- 目录状态：`recorded_verified` 51、`partial` 84、`not_collected` 796。
- 采集状态：`collected` 609、`needs_binding` 217、`truncated` 105。
- 覆盖 CSV 的 `program_count` 与 raw 流式统计逐校一致（931 行，0 个不一致）；effective 计数也 0 个不一致。

**复现注意**：既有补充规则内部调用 `shutil.which("pdftotext")`；CLI 运行时必须让 `pdftotext` 在 PATH 上（本机为 `C:\Program Files\Git\mingw64\bin` 前缀），否则 effective 会退化为 650/1,932 且出现 6 条 `invalid_rule_ids`。`--raw-pdf` 只指定审计所记录的路径，不能替代 PATH。本轮成功日志已记录该前缀。

## C. 931 单位完整队列（可重放）与入口证明

`queue.json` 是生成器原样输出，931 行，3,054,725 字节，SHA256 `12dd9489c2d55afcc1b38c65627ee956af41bd93be8eea458edeb5855ddc55ba`（LF 规范字节，等于 Git blob）。每行保留身份、原始与采用、入口证据与证明、三层计数、剩余缺口、`gaps/count_mismatch/effective_count_mismatch`。

每行新增持久化字段 `entry_verification`，由 `verify_entry` 产生：

```json
{"status": "verified|rejected", "reason": "...", "unit_id": "...", "year": 2027,
 "official_url": "...", "evidence_url": "...", "evidence_sha256": "...",
 "document_id": "...", "document_sha256": "...", "document_path": "snapshots/national/2027/...",
 "file_sha256": "...", "quote_match": "literal|", "proof_sha256": "<64 hex>"}
```

`proof_sha256` 是对“行上实际保存的入口证据 + 证明字段”的规范化指纹。`entry_trusted` 要求：无 `missing_trusted_entry`、入口 URL 非空且不含“尚未核准”、`status=verified`、verification/relation 在支持集合内、证明的 unit/year/official_url 与行一致、引文 host 与入口 host 一致、`proof_sha256` 为 64 位十六进制且**重新计算的指纹与它完全相等**。任何一处被篡改，选择器都不再信任。

### C1. 整库证明结果

| 状态 | 单位数 |
| --- | ---: |
| `verified`（`saved_original_replayed`） | 405 |
| `rejected` | 526 |
| 合计 | 931 |

拒绝原因（互斥主因，按代码判定顺序取第一个失败）：

| 原因码 | 单位数 | 含义 |
| --- | ---: | --- |
| `no_entry_evidence` | 293 | 单位没有 `official_url_evidence` 字典 |
| `no_exact_annual_document` | 147 | 年度登记中没有与证据 URL+SHA 完全一致的原件 |
| `unsupported_relation` | 45 | relation 不在支持的入站关系集合 |
| `unsupported_verification` | 20 | verification 为 rejected/failed/未知值 |
| `quote_not_in_original` | 19 | 引文未在落盘原件中逐字出现 |
| `ambiguous_saved_original` | 1 | 同目录下多个文件匹配 SHA 前缀 |
| `quote_host_mismatch` | 1 | 引文 host 不是入口 host |
| 合计 | 526 | |

因此 `missing_trusted_entry` 现在是 **526**，不是上一轮报告里的 2。上一轮只检查非空元数据，2 是“没有网址/明写尚未核准”的数量；现在按真实原件重放，405 个单位有可重放入口，其余 526 个必须补证。这是更严格也更诚实的结果，不能当作退步掩盖。

### C2. 扩目录池

`program_count=0`、`collection_status=collected`、原因含“待解析”的单位共 **433** 个：

| 入口证明 | 单位数 |
| --- | ---: |
| `entry_trusted=true` | 136 |
| `entry_trusted=false` | 297 |

297 个被排除单位的证明原因：`no_entry_evidence` 234、`no_exact_annual_document` 42、`quote_not_in_original` 8、`unsupported_relation` 7、`unsupported_verification` 6。它们不是静默跳过：每个都在 `queue.json` 有 `entry_verification`，并在 `baseline.json.entry_proof` 计数。

### C3. 缺口频次

| 缺口 | 单位数 | 含义与下一步 |
| --- | ---: | --- |
| `missing_trusted_entry` | 526 | 入口证明未通过重放；交 Codex 补证，本批两池都不选 |
| `missing_catalog` | 796 | 尚未采用任何 2027 方向；不等于未招生 |
| `parser_pending` | 433 | 已保存公开原文、但尚无采用方向；节点1/2 解析并做目录全集对账 |
| `original_pending` | 413 | 来源受限、截断或尚未取得可核验原文 |
| `missing_full_qualification` | 135 | 已有方向但没有任何 verified 完整资格 |
| `missing_tuition` | 122 | 已有方向但 verified 学费为 0 |
| `missing_duration` | 117 | 已有方向但 verified 学制为 0 |
| `partial_catalog` | 84 | 已有部分专业资料，全校目录未对账完整 |
| `review_pending` | 84 | 部分目录待独立审校 |
| `partial_tuition` | 3 | 部分方向已有 verified 学费，仍有剩余方向 |
| `partial_duration` | 3 | 部分方向已有 verified 学制，仍有剩余方向 |

一个单位可以同时有多个缺口，所以上表不能相加得到 931。部分覆盖单位仍保留在队列：

| 单位 | 方向 | verified 学费 | verified 学制 | 剩余方向 |
| --- | ---: | ---: | ---: | --- |
| 南开大学 `367949` | 226 | 10 | 10 | 学费/学制各 216 |
| 东华大学 `368133` | 166 | 156 | 166 | 学费 10 |
| 南昌大学 `368259` | 663 | 0 | 638 | 学费 663 / 学制 25 |
| 郑州大学 `368317` | 438 | 252 | 252 | 学费/学制各 186 |

郑州大学仍是 R3 的直接反例来源：队列分列 `raw_tuition=0`、`effective_tuition=252`、`verified_tuition=252`、`remaining_tuition=186`，缺口为 `partial_tuition/partial_duration`，不是 `missing_*`。

`queue-summary.json` 只供阅读，明确 `replayable=false`，不含 `original_urls/official_url_evidence/entry_verification`；对摘要重放会显式报 `queue row '367882' missing required fields for replay: original_urls, entry_verification`，不会静默产生不同批次。

## D. 首批 10 校（由完整队列确定性选出）

选择规则版本 `b001-node0-v4`：

- **扩目录池**：`program_count=0`、`collection_status=collected`、原因含“待解析”、`entry_trusted=true`、`original_urls` 非空；按“目录型 URL 数 → PDF 数 → URL 总数 → unit_id”排序。
- **补字段池**：已有方向且 `remaining_tuition>0` 或 `remaining_duration>0`，**且 `entry_trusted=true`**；先选费用与学制均未覆盖的单位，再按剩余方向总数降序、unit_id 升序。部分覆盖单位保留在池中。

| 角色 | unit_id | 代码 | 名称 | 省份 | 方向 | source_count | URL 型/PDF/总数 | 入口证明 | 剩余 学费/学制 | 选入理由 / 下一步 |
| --- | --- | --- | --- | --- | ---: | ---: | --- | --- | --- | --- |
| 扩目录 | 368295 | 10434 | 山东农业大学 | 山东 | 0 | 1001 | 38/0/40 | verified | — | 目录型 URL 最多；`source_count` 不是已下载原件数 |
| 扩目录 | 368049 | 10152 | 大连工业大学 | 辽宁 | 0 | 40 | 37/0/40 | verified | — | 确认学院/分页/附件差集 |
| 扩目录 | 368146 | 10280 | 上海大学 | 上海 | 0 | 61 | 36/0/40 | verified | — | 多学院目录全集对账 |
| 扩目录 | 368275 | 10458 | 山东艺术学院 | 山东 | 0 | 66 | 36/0/40 | verified | — | 艺术类方向单独核对 |
| 扩目录 | 368611 | 10758 | 新疆农业大学 | 新疆 | 0 | 46 | 36/0/40 | verified | — | 历史材料不得计入 2027 |
| 扩目录 | 368142 | 10251 | 华东理工大学 | 上海 | 0 | 58 | 35/10/40 | verified | — | 确认是否只取得首屏 |
| 扩目录 | 368436 | 10593 | 广西大学 | 广西 | 0 | 40 | 35/0/40 | verified | — | 顶替未通过重放的阜阳师范大学 |
| 补字段 | 367929 | 14430 | 中国科学院大学 | 北京 | 5,165 | 34 | 4/0/34 | verified | 5,165/5,165 | 方向最多；补费用/学制原文依据，不改完整资格标志 |
| 补字段 | 367859 | 10027 | 北京师范大学 | 北京 | 463 | 20 | 4/1/20 | verified | 463/463 | 保留 `partial_catalog/review_pending`；不勾整校完成 |
| 补字段 | 368201 | 10298 | 南京林业大学 | 江苏 | 305 | 14 | 8/0/14 | verified | 305/305 | 顶替入口证明未通过的同济大学 |

两处替换均是确定性结果，不是手写学校名：

- **阜阳师范大学 `688908731`（10371）**：扩目录排序本在其位；但其入口证据无法在年度登记中找到精确原件（无 `document_id`、无落盘快照），证明为 `rejected / no_exact_annual_document`，因此不入池。按排序顺延为 **广西大学 `368436`**。
- **同济大学 `368128`（10247）**：补字段池中方向数仅次于国科大，但入口证明为 `rejected / no_exact_annual_document`（年度登记没有与入口证据 URL+SHA 完全一致的原件），按 R2 门禁排除，顺延为 **南京林业大学 `368201`**。上一轮把它选入是元数据门禁的遗留问题，本轮不再重犯。

首批 10 校的入口原件证明（均为 `saved_original_replayed`；路径相对 `data/`）：

| unit_id | 名称 | document_path | file_sha256 |
| --- | --- | --- | --- |
| 368295 | 山东农业大学 | `snapshots/national/2027/368295/e7f38136a817c2a9-7ee9870ccbdab753.html` | `7ee9870ccbdab7530508506881cced042f2b0eb63481df5e75c68577f548bc27` |
| 368049 | 大连工业大学 | `snapshots/national/2027/368049/20b97f1c37500e2b-3511c0b9ff15c9b9.html` | `3511c0b9ff15c9b9e2a50e033e9ce29a56b70f157daddb4cf8ee60b99945f211` |
| 368146 | 上海大学 | `snapshots/national/2027/368146/325d25a36ef05f1d-dda104ef48adaba4.html` | `dda104ef48adaba4369f9f8ebb97d575fadd4789a885ab2103e673e0389b63d8` |
| 368275 | 山东艺术学院 | `snapshots/national/2027/368275/2cc0cbb49a3a760e-6cb0ceb69c37bce2.html` | `6cb0ceb69c37bce2effbd36458727fb0483c60704d73a5d449f45a8b2a4bff8b` |
| 368611 | 新疆农业大学 | `snapshots/national/2027/368611/348e6624f1fbbb34-0d89f20d6574a556.html` | `0d89f20d6574a5562b3fef5f4792751b72835128c558bbbfd3371fdd30116c44` |
| 368142 | 华东理工大学 | `snapshots/national/2027/368142/2d4252c57961d944-5e69afd193952f41.html` | `5e69afd193952f418d5995efaa3a11820092e857a4313f98184964e54648d9dd` |
| 368436 | 广西大学 | `snapshots/national/2027/368436/a441079f052799c3-deddd313eaf0e662.html` | `deddd313eaf0e662de8dfd0198fcb78bbc213c05928f25bb41eca41f0a3457cd` |
| 367929 | 中国科学院大学 | `snapshots/national/2027/367929/340ded81c9e561a6-5946240ca60a7af0.html` | `5946240ca60a7af0f98e356bc7abe0c577838814083f2faf77a7e7ac1236125e` |
| 367859 | 北京师范大学 | `snapshots/national/2027/367859/4d7ed0aed0255569-3d99aee22c39ee6e.html` | `3d99aee22c39ee6e4df38007b36a46fb49284a4620bf119ef5fafb3b11a70a9f` |
| 368201 | 南京林业大学 | `snapshots/national/2027/368201/8ad81b29b2e84674-8a96ba7409532cd1.html` | `8a96ba7409532cd12074d8dcc94368f6193f015824a32b9bf5a3722b0bccdd57` |

首批结论只能写“10 校已入队且入口证明通过重放”，不能写“10 校目录完成”或“10 校资格完成”。节点1 结束时才判断每校终态或阻塞。`source_count` 是公开包累计来源数；URL 数不等于独立原件数，节点1 必须按最终 URL、MIME、SHA、真实年度和目录内容对账。

## E. 资格持久化最小方案（节点3 实现，本节点只设计）

目标不是把布尔值改真，而是让“条款 → 原文 → 适用范围 → 可执行条件 → 采用/失效”可回放。建议新增**永久文件** `data/qualification_adoption.json`（schema_version 1），不改 `admissions.json` 的既有字段语义。

### E1. AST 必须复用现有语法

不要引入 `{"op":"and","args":[...]}` 或 `{"when": ..., "then": ...}` 这类当前引擎不认识的语法。`grad_engine._condition` 只接受 `all/any/when/field/op/value/evidence`；`when` 是**守卫**，其后果必须写在同一个对象的其余字段（或同一对象的 `all/any`），不存在 `then` 键：

```json
{"all": [
  {"field": "degree_type", "op": "eq", "value": "bachelor", "evidence": "..."},
  {"any": [{"field": "major", "op": "in", "value": ["0812"], "evidence": "..."}]},
  {"when": {"field": "study_mode", "op": "eq", "value": "part_time", "evidence": "..."},
   "field": "work_years", "op": "gte", "value": 2, "evidence": "..."}
]}
```

引擎语义：守卫 `excluded` 时该条件视为“不适用”（matched）；守卫 `unknown` 且后果未 matched 时整体 unknown；守卫 matched 时按同级后果判断。持久化 AST 必须使用上述键；字段白名单以现有 `grad_engine.PROFILE_FIELDS` 为上限（学历、工作年限、有限本科专业等）。当前档案输入不含毕业时间、专项、境外学历、加试，因此这些条件只能保留完整原句并进入 `unknown`，不能仅凭 AST 非空设置完整资格。若确需扩展，必须同步扩展档案输入、校验、解释输出和三态反例，不能只扩 JSON 字段。

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

## F. 测试、覆盖率、CLI 与资源边界

```powershell
# 本轮实际执行（工作树 .venv）
./.venv/Scripts/python.exe -m unittest tests.test_national_data_baseline -v
./.venv/Scripts/python.exe -m coverage run --branch --source=national_data_baseline -m unittest tests.test_national_data_baseline -q
./.venv/Scripts/python.exe -m coverage report -m
# CLI 必须前置 pdftotext 所在目录到 PATH（本机为 Git 的 mingw64/bin）
$env:PATH = 'C:\Program Files\Git\mingw64\bin;' + $env:PATH
./.venv/Scripts/python.exe -X utf8 national_data_baseline.py --data-dir data --out data/batch_runs/b001 --repo-root . --raw-pdf "C:/Program Files/Git/mingw64/bin/pdftotext.exe" --bbox-pdf "E:/miniConda/envs/yanxu-pdf/Library/bin/pdftotext.exe"
```

- 测试：**71 项通过**（上一轮 49 项）。先 RED 后 GREEN；R2 的新失败用例覆盖：零 SHA、`verification=rejected/failed`、删 SHA/URL、替换原件字节、错误单位、引文不在原件、来源 host 冒充、缺/多/未绑定原件、错误 document kind、被篡改的证明指纹、补字段池未核验入口；另含生成 JSON 必须写 LF 字节的回归，保证记录 SHA 与 Git blob 一致。
- 含分支覆盖：**99%**，527 语句仅 5 行未执行，198 分支仅 3 个部分分支；缺失行 `219->221, 320-321, 335-336, 856`。
- 覆盖率证据绑定：`coverage-report.txt` 以 LF 字节写入，SHA256(LF) `2c1d84bb4ab7f776d92066e4f62940e436bdbebd93f41e364ec2d467eec7409a`，Git blob SHA-1 `94afb7c97dd8be52e12c3c5b0b8eed87b3c876a6`；`coverage-evidence.json` 同时记录两者。由于仓库 `core.autocrlf=true`，用编辑器另存或重新 checkout 可能变成 CRLF，导致文件 SHA256 与仓库内记录不同；**以 Git blob 为准**，需要字节级核验时用 `git cat-file blob` 读取。
- 完整公开包 CLI：`cli-recompute.log` 记录命令、PATH 前缀、stdout/stderr 与运行元数据；`cli-run.json` 机器可读。结果 **exit 0，84.381 秒，峰值工作集 1,384,763,392 字节（约 1.29 GiB）**，三口径与 10 校批次均与产物一致。
- **资源边界（明确保留）**：`effective_counts` 仍完整 `json.load` 大 `admissions.json` 并构造内存视图，未做上限或分块；1.29 GiB 只是本机实测峰值，不能据此声称整套重算有界内存，也不能保证在更小内存环境一定成功。这是扩规模前必须解决的实测限制，Codex 可按此边界验收。
- 本节点未运行全库测试（只改新增模块），未改五个保护文件、未改旧金标/阈值、未改采用数据。

## G. 产物与 SHA 绑定

生成时的输入：

| 输入 | SHA256 |
| --- | --- |
| `data/admissions.json` | `97eca2f6cf8727fa50a46241ee4c95098d308819c4641db9660c3ef758aee8e3` |
| `data/units.json` | `91c8dbd8ccddd193dc4fc2391d4818a3968203d181920ab972b4c86d32548cdb` |
| `data/annual_registry_2027.json` | `06b14458d64b5c28de8ff18bc6b6bf2f23851acc0d98d6c397389043faf31b40` |
| `data/catalog_coverage_2027.csv` | `42060227e15b9b5fbf9731bc47cba12508a73b6616e20ece12fc1da4b064c04e` |
| `national_data_baseline.py`（工作字节） | `ce5c3fb130b8dc4075d6f01e0476860f7f00e014ba2aa1df8b5445d038035bb5` |
| `national_data_baseline.py`（Git blob `52e99ff:...`） | `25d02145743e42f8270aa65ab456559d08311434`（SHA-1） |

产物（`docs/work_nodes/national_data_b001/`）：

| 文件 | 字节 | SHA256 | 说明 |
| --- | ---: | --- | --- |
| `baseline.json` | 5,668 | `2698ea68be0083a45025eeda97c5bd8d072e1f52e557c9ff13e7ba349509afff` | 三口径、输入/产物 SHA、入口池、证明状态与原因、缺口频次 |
| `queue.json` | 3,054,725 | `12dd9489c2d55afcc1b38c65627ee956af41bd93be8eea458edeb5855ddc55ba` | 生成器原样 931 行，含 `entry_verification`，可重放 |
| `queue-summary.json` | 1,063,480 | `b8ef0d34e9556bea9f469b1c19fbcde357a1fc084df30fd671089087152d388f` | 只读摘要，`replayable=false` |
| `batch.json` | 55,556 | `d46809a40bdced7f35f4bccf6230370f9016b462e17f319af40089fa7141a3fb` | 7+3 首批；绑定 `queue_sha256` 与 `selection_rule_version=b001-node0-v4` |
| `replay-check.json` | 8,898 | `871f3d159dbcdfdeca247ea5a520771bba5ef03caa5e97e122877ca5a40f7d06` | 从落盘 queue 重放 batch；含 10 校证明路径/文件 SHA |
| `coverage-report.txt` | 2,882 | `2c1d84bb4ab7f776d92066e4f62940e436bdbebd93f41e364ec2d467eec7409a`（LF；blob `94afb7c9...`） | 覆盖率命令、测试尾部与原始报表 |
| `coverage-evidence.json` | 834 | `be6d2d528e5ec82ba8d21dc747d5aeb4370b66e1fdcf86c9bb8cd668a879b6c3` | 覆盖率机器可读证据与换行绑定 |
| `cli-recompute.log` | 1,846 | `18748e2e3ab2a7644913d0bbb02d0b798e5f6008c3eab671bb8240257eb443b3` | 完整 CLI 成功日志与运行元数据（LF） |
| `cli-run.json` | 755 | `8223b62fa409924d34abf489487a9cf97694a7c55448c1c8f35661cff08e2411` | exit 0、84.381s、峰值 1.29GiB |
| `protected-sha256.json` | 802 | `28ad3468bf2648d7678dd06d148a3dffe4b84f615621b2549f2a9c53f7c2b2dd` | 五个保护适配器 SHA（LF） |

批次重放验证：读取落盘 `queue.json`，用同一 `queue_sha256` 和 `b001-node0-v4` 调 `select_batch`，7 扩目录 + 3 补字段的 ID、角色、理由、next_step 全部与 `batch.json` 一致；摘要重放被显式拒绝。机器可读结果见 `replay-check.json`（`replay_matches_batch=true`，`summary_rejected=true`）。

## H. 失败、边界与下一步

- 未联网采集、未解析任何新原件、未采用任何方向、未发布 SQLite、未构建新包；本报告不得被当作目录或资格增长。
- **526 个单位入口未通过真实原件重放**，其中 293 个单位根本没有入口证据字典；这些单位已写入队列和 `baseline.json`，不是静默丢弃。Codex 若确认其中某入口合法，需要补一份可重放的年度原件/归属声明；在补证前本批两池都不选。
- 两个具名结构化阻塞：**阜阳师范大学 `688908731 / 10371`** 和 **同济大学 `368128 / 10247`**，原因均为 `no_exact_annual_document`（年度登记没有与入口证据 URL+SHA 完全一致的原件）。阜阳被广西大学顶替，同济被南京林业大学顶替；不是把未核验单位留在批次里。
- `effective_counts` 非有界内存，实测峰值 1.29 GiB；不是“已验证有界”，扩规模前需改为分块/流式或明确上限。
- `source_count` 与本地 `original_urls` 数量不同（如山东农业大学 1001 vs 40）；本地 40 条也可能含分页/导航变体，节点1 必须按最终原件对账。
- 下一步（节点1，尚未开始）：对首批 10 校建立原件清单与目录全集对账；每校保留 `document_id / URL / 标题 / 年份 / SHA / 路径 / 状态`，失败按互斥主因分类。做完一批即报，不静默长跑；不碰任何完整资格标志。

## I. 发给 Codex 的节点简报

```text
批次/节点/分支/base：b001 / 节点0 R2 重交 / data/national-b001 / 67d5e24
被审查 HEAD：598bb09
R2 门禁提交：256327e；生成提交：52e99ff（生成时 tracked_dirty=false）；证据提交=本文件所在分支 HEAD
实际处理：0/10 采集；931/931 队列已建立；首批 10 校入队（7 扩目录+3 补字段）
三口径：raw 650 学费（378 年费/272 全程）、1,680 学制、645 可算全程
        effective-present 912（640/272）、1,942、907
        verified 912、1,942、907；完整资格 0；冲突暂停 0
入口证明：931 个单位全部持久化 proof；verified 405 / rejected 526
          reasons: no_entry_evidence 293、no_exact_annual_document 147、
          unsupported_relation 45、unsupported_verification 20、
          quote_not_in_original 19、ambiguous_saved_original 1、quote_host_mismatch 1
扩目录池：433 → trusted 136 / untrusted 297
R2：真实原件 URL+SHA、kind、单位身份、文件名绑定、文件字节 SHA、引文逐字出现、证明指纹全部通过才 entry_trusted；
    扩目录池与补字段池都门禁；零 SHA/rejected/failed/删字段/换字节均 rejected；RED/GREEN 已补
R1/R3/R4：未重做，数字与上一轮一致；队列内容未变，规范 LF 绑定后 queue SHA=12dd9489…，batch 按 v4 重算
测试：71 项通过；分支覆盖 99%（527 语句/198 分支/3 部分）
覆盖率绑定：coverage-report.txt SHA256(LF) 2c1d84bb…，git blob 94afb7c9…；autocrlf 只影响 checkout
CLI：exit 0，84.381s，峰值 1,384,763,392 B（约 1.29GiB）；PATH 需前置 Git mingw64/bin
     成功日志 cli-recompute.log 18748e2e…，元数据 cli-run.json 8223b62f…
产物：baseline.json 2698ea68…、queue.json 12dd9489…（3,054,725 B）、
      queue-summary.json b8ef0d34…（replayable=false）、batch.json d46809a4…（v4）、
      replay-check.json 871f3d15…（replay_matches_batch=true，summary_rejected=true）
保护：五个 PDF 适配器 SHA 与 base 一致；未改采用数据/发布库/runtime/Key
未解项：未采集/解析/采用/发布；526 个单位入口需补证；effective 重算非有界内存
下一节点：节点1 首批 10 校原件与目录全集清单（不碰完整资格标志）
```
