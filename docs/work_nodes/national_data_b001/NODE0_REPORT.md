# 全国采用数据 · 节点0报告（b001，R1—R4 补证后重交）

状态：`baseline_ready_resubmitted`（工作状态，不等于任何学校目录或资格已完成）
生成时间：2026-11-09（本机时区 UTC+8）
执行者：Claude（数据）；独立验收：Codex（上一轮未通过，本轮重交）
审查对象：`data/national-b001`，base `67d5e24`，代码提交 `9b1c945`（全量 `9b1c9457a9dbf4c15cbfa7597e2144ecdfb26f8e`）
手册：[CLAUDE_NATIONAL_DATA_RUNBOOK](../../CLAUDE_NATIONAL_DATA_RUNBOOK.md)（已在本分支仓库内）

## 0. 本轮针对审查意见的修正摘要

| 审查项 | 修正 | 可复核位置 |
| --- | --- | --- |
| R1 部分覆盖消失 | 缺口按 `verified` 条数与 `program_count` 比较，新增 `partial_tuition/partial_duration/partial_full_qualification`，队列保留 `remaining_*` 条数 | `classify_gaps`、`build_queue`；`GapTests`、`QueueTests` |
| R2 未核准入口仍入选 | 扩目录选择必须通过 `entry_trusted`：非空入口、无“尚未核准”、且 `official_url_evidence` 的 verification/引文/链接 host 与入口 host 一致；已判 `missing_trusted_entry` 直接排除 | `entry_trusted`、`select_batch`；`EntryTests`、`SelectTests` |
| R3 有效统计冒充 verified | 新增 `verified_counts`，逐条调用现有 `EvidenceIndex._claim(..., verified=True)`，并校验金额有限正数、计费周期、学制正数、完整资格条件非空且无 review；有效 presence 与 verified 分列 | `verified_counts`、`effective_counts`、`audit`；`VerifiedCountTests`、`EffectiveCountTests` |
| R4 queue 不可复算 batch | 提交生成器原样 `queue.json`（含 `original_urls/official_url_evidence/annual_evidence_count/conditions_nonempty` 等）及其 SHA；batch 绑定 queue SHA 与选择规则版本；摘要另存并明确 `replayable=false`；缺字段/摘要重放显式报错 | `select_batch`、`audit`；`AuditTests` 集成用例 |
| 覆盖率不足 | 新增模块含分支覆盖 **99%**（359 语句，仅 `if __name__` 1 行未执行；116 分支，1 分支部分） | `coverage report -m` |
| 报告口径 | 全文改为 raw / effective-present / verified 三列；修正阜阳师范大学代码说明、测试数、手册在库状态、source_count 与原件数区别 | 本文件 |

## A. 身份与隔离

| 字段 | 实际值 |
| --- | --- |
| 仓库 / 分支 / base SHA | `https://github.com/xiaosongxiaosong1111/yanxu-ai`，`data/national-b001`，`67d5e24` |
| 本次代码提交 | `9b1c945`（生成基线时工作树 tracked 干净，`tracked_dirty=false`） |
| 独立工作树 | `D:/yx-data-b001`（短路径；未复用 `E:/PythonSpace/yanxu-ai`，未碰 `D:/CodexRuntime`） |
| 数据根 / 产物根 | `D:/yx-data-b001` / `D:/yx-data-b001/data/batch_runs/b001` |
| 用户 runtime / Key | 未读取、未复制、未覆盖；本工作树没有 `data/runtime/` 发布库 |
| Python | 3.13.9（工作树 `.venv`，`requirements.txt` 已装：ijson 3.4.0.post0、pdfplumber 0.11.10、xlrd 2.0.2；另仅在隔离 venv 安装 coverage 用于测量） |
| 原文提取器 | `C:/Program Files/Git/mingw64/bin/pdftotext.exe` → `pdftotext version 4.00` |
| 坐标提取器 | `E:/miniConda/envs/yanxu-pdf/Library/bin/pdftotext.exe` → `pdftotext version 26.07.0`；同目录 `pdftocairo.exe` 存在；未加入 PATH |
| 公开包 | `data/bundles/catalog-data.zip` SHA256 `637f1990c02435127cca3c7a308b08087e24db40d8aefba41290fad294936478`；manifest SHA256 `52655a92d671025e9cfb086e3a528d610d52ed53d09fd2c3f1b9847a87bf1394` |
| 恢复结果 | `prepare_data.py` 离线恢复 6,521 个文件，`published=false` |
| 付费搜索 / 模型调用 | 0 次；未读取任何 Key，未访问 4180 |
| 符号链接 | 工作树为普通 Git 工作树；未发现指向原项目运行目录的链接 |

五个受保护 PDF 适配器字节在节点0前后一致（SHA 见 `protected-sha256.json`，与 base `67d5e24` 相同）：

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

- `912 − 650 = 262` 全部来自既有白名单补充（郑州大学 252、南开大学 10；7 条规则全部有效，`invalid_rule_ids=[]`）。既有 650 条在有效数据中仍逐条重放为 verified，未因补充被降级。
- 本次 `verified` 的 claim 状态计数：学费 `{verified: 912}`、学制 `{verified: 1,942}`、资格 claim `{}`（无任何条件原句）。没有 `needs_review`/`conflict` 被算入 verified。
- 完整资格仍为 **0**：22,356 条 `conditions` 均为空。`eligibility_evidence_complete=true` 单独出现不会计数，本轮也未改写任何标志。
- 与手册基线逐项一致：135/931、22,356、0、650（378/272）、912（640/272）、1,942、907；包 SHA 未变。

目录状态：`recorded_verified` 51、`partial` 84、`not_collected` 796。
采集状态：`collected` 609、`needs_binding` 217、`truncated` 105。
覆盖 CSV 的 `program_count` 与 raw 流式统计逐校一致（931 行，0 个不一致）；effective 计数也 0 个不一致。

## C. 931 单位完整队列（可重放）

`queue.json` 是生成器原样输出，931 行，2,429,312 字节，SHA256 `8cfdc263b1bfd917a7462cac783e99dfd54208f0b052e0b5c41590eeaf0dfb86`。每行保留：

- 身份：`unit_id/unit_code/unit_name/province/year/annual_status/annual_evidence_count`
- 原始与采用：`source_count/missing_reason/original_urls/catalog_status/collection_status`
- 入口：`official_url/official_url_evidence/entry_trusted`
- 三层计数：`program_count/raw_*/effective_*/verified_*`
- 剩余缺口：`remaining_tuition/remaining_duration/remaining_full_qualification`
- 判定：`gaps/count_mismatch/effective_count_mismatch`

`queue-summary.json` 只供阅读，明确 `replayable=false`，不含 `original_urls/official_url_evidence` 等选择必需字段；对摘要调用 `select_batch` 会显式报 `missing required fields ... original_urls`，不会静默产生不同批次。

| 缺口 | 单位数 | 含义与下一步 |
| --- | ---: | --- |
| `missing_catalog` | 796 | 尚未采用任何 2027 方向；不等于未招生 |
| `parser_pending` | 433 | 已保存公开原文、但尚无采用方向；节点1/2 解析并做目录全集对账 |
| `original_pending` | 413 | 来源受限、截断或尚未取得可核验原文 |
| `missing_full_qualification` | 135 | 已有方向但没有任何 verified 完整资格 |
| `missing_tuition` | 122 | 已有方向但 verified 学费为 0 |
| `missing_duration` | 117 | 已有方向但 verified 学制为 0 |
| `partial_catalog` | 84 | 已有部分专业资料，全校目录未对账完整 |
| `partial_tuition` | 3 | 部分方向已有 verified 学费，仍有剩余方向 |
| `partial_duration` | 3 | 部分方向已有 verified 学制，仍有剩余方向 |
| `review_pending` | 84 | 部分目录待独立审校 |
| `missing_trusted_entry` | 2 | 官方入口未核准；交 Codex 补证，本批跳过该单位 |

部分覆盖单位（供节点1/2 直接定位，不再从队列消失）：

| 单位 | 方向 | verified 学费 | verified 学制 | 剩余方向 |
| --- | ---: | ---: | ---: | --- |
| 南开大学 `367949` | 226 | 10 | 10 | 学费/学制各 216 |
| 东华大学 `368133` | 166 | 156 | 166 | 学费 10 |
| 南昌大学 `368259` | 663 | 0 | 638 | 学费 663 / 学制 25 |
| 郑州大学 `368317` | 438 | 252 | 252 | 学费/学制各 186 |

郑州大学是 R3 的直接反例来源：提交队列现在分列 `raw_tuition=0`、`effective_tuition=252`、`verified_tuition=252`、`remaining_tuition=186`；缺口为 `partial_tuition/partial_duration`，不再是 `missing_*`，避免把既有 252 条白名单覆盖重复算成本批增长。

入口门禁：433 个扩目录形态单位中，193 个 `entry_trusted=true`，240 个因缺少可重放归属证据被排除（不是静默跳过，已计数并写入 `baseline.json.expansion_pool`）。本批只从 193 个可信入口中按规则选取。

## D. 首批 10 校（由完整队列确定性选出）

选择规则已写成代码并有测试：

- 扩目录池：`program_count=0`、`collection_status=collected`、原因含“待解析”、通过 `entry_trusted`、`original_urls` 非空；按“目录型 URL 数 → PDF 数 → URL 总数 → unit_id”排序。
- 补字段池：已有方向且 `remaining_tuition>0` 或 `remaining_duration>0`；先选费用与学制均未覆盖的单位，再按剩余方向总数降序、unit_id 升序。**部分覆盖单位保留在池中**。

| 角色 | unit_id | 代码 | 名称 | 省份 | 方向 | source_count | raw 学费/学制 | effective 学费/学制 | verified 学费/学制 | 剩余 学费/学制 | 选入理由 / 下一步 |
| --- | --- | --- | --- | --- | ---: | ---: | --- | --- | --- | --- | --- |
| 扩目录 | 368295 | 10434 | 山东农业大学 | 山东 | 0 | 1001 | 0/0 | 0/0 | 0/0 | — | 本地 `original_urls` 40 条（目录型 38、PDF 0）；source_count 不是已下载原件数 |
| 扩目录 | 368049 | 10152 | 大连工业大学 | 辽宁 | 0 | 40 | 0/0 | 0/0 | 0/0 | — | 本地 40 条 URL（目录型 37）；确认学院/分页/附件差集 |
| 扩目录 | 688908731 | 10371 | 阜阳师范大学 | 安徽 | 0 | 73 | 0/0 | 0/0 | 0/0 | — | unit_code 是 5 位 `10371`；9 位 `688908731` 是研招网 unit_id，两者不同 |
| 扩目录 | 368146 | 10280 | 上海大学 | 上海 | 0 | 61 | 0/0 | 0/0 | 0/0 | — | 本地 40 条 URL（目录型 36）；多学院目录全集对账 |
| 扩目录 | 368275 | 10458 | 山东艺术学院 | 山东 | 0 | 66 | 0/0 | 0/0 | 0/0 | — | 本地 40 条 URL（目录型 36）；艺术类方向单独核对 |
| 扩目录 | 368611 | 10758 | 新疆农业大学 | 新疆 | 0 | 46 | 0/0 | 0/0 | 0/0 | — | 本地 40 条 URL（目录型 36）；历史材料不得计入 2027 |
| 扩目录 | 368142 | 10251 | 华东理工大学 | 上海 | 0 | 58 | 0/0 | 0/0 | 0/0 | — | 本地 40 条 URL（目录型 35、PDF 10）；确认是否只取得首屏 |
| 补字段 | 367929 | 14430 | 中国科学院大学 | 北京 | 5,165 | 34 | 0/0 | 0/0 | 0/0 | 5,165/5,165 | 方向最多且原文已收集；补费用/学制原文依据，不改完整资格标志 |
| 补字段 | 368128 | 10247 | 同济大学 | 上海 | 700 | 73 | 0/0 | 0/0 | 0/0 | 700/700 | 原文已收集；费用/学制均缺 |
| 补字段 | 367859 | 10027 | 北京师范大学 | 北京 | 463 | 20 | 0/0 | 0/0 | 0/0 | 463/463 | 保留 `partial_catalog/review_pending`；不勾整校完成 |

首批结论只能写“10 校已入队”，不能写“10 校目录完成”或“10 校资格完成”。节点1 结束时才判断每校终态或阻塞。`source_count` 是公开包累计来源数；`original_urls` 的 40 条也可能包含同一目录的分页/导航变体，URL 数不等于独立原件数，节点1 必须按最终 URL、MIME、SHA、真实年度和目录内容对账。

## E. 资格持久化最小方案（节点3 实现，本节点只设计）

目标不是把布尔值改真，而是让“条款 → 原文 → 适用范围 → 可执行条件 → 采用/失效”可回放。建议新增**永久文件** `data/qualification_adoption.json`（schema_version 1），不改 `admissions.json` 的既有字段语义。

### E1. AST 必须复用现有语法

不要引入 `{"op":"and","args":[...]}` 这类当前引擎不认识的语法。现有 `grad_engine.py:365-395` 只接受：

```json
{"all": [{"field": "...", "op": "...", "value": "...", "evidence": "..."},
         {"any": [...]},
         {"when": {...}, "then": {...}}]}
```

持久化 AST 必须使用 `all/any/when/field/op/value/evidence`。字段白名单以现有 `grad_engine.PROFILE_FIELDS` 为上限（学历、工作年限、有限本科专业等）；当前档案输入不含毕业时间、专项、境外学历、加试，因此这些条件只能保留完整原句并进入 `unknown`，不能仅凭 AST 非空设置完整资格。若确需扩展，必须同步扩展档案输入、校验、解释输出和三态反例，不能只扩 JSON 字段。

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

## F. 测试、覆盖率与保护项

```powershell
# 本轮实际执行（工作树 .venv）
./.venv/Scripts/python.exe -m unittest tests.test_national_data_baseline -v
./.venv/Scripts/python.exe -m coverage run --branch --source=national_data_baseline -m unittest tests.test_national_data_baseline -q
./.venv/Scripts/python.exe -m coverage report -m
./.venv/Scripts/python.exe -X utf8 national_data_baseline.py --data-dir data --out data/batch_runs/b001 --repo-root . --raw-pdf "C:/Program Files/Git/mingw64/bin/pdftotext.exe" --bbox-pdf "E:/miniConda/envs/yanxu-pdf/Library/bin/pdftotext.exe"
```

- 测试：**49 项通过**（先 RED 后 GREEN；上一轮 11 项）。
- 含分支覆盖：**99%**，359 语句仅 `if __name__` 1 行未执行；116 分支仅 1 个部分分支。
- 关键闭环用例：部分覆盖不消失；未核准入口不能入选；`needs_review`/无效数值不算 verified；从落盘 `queue.json` 重放得到与 `batch.json` 相同的 7+3 单位；摘要重放显式报错。
- 本节点未运行全库测试（只改新增模块），未改五个保护文件、未改旧金标/阈值、未改采用数据。
- 保护适配器 SHA 与 base `67d5e24` 及上一轮一致，见 `protected-sha256.json`。

## G. 产物与 SHA 绑定

生成时的输入：

| 输入 | SHA256 |
| --- | --- |
| `data/admissions.json` | `97eca2f6cf8727fa50a46241ee4c95098d308819c4641db9660c3ef758aee8e3` |
| `data/units.json` | `91c8dbd8ccddd193dc4fc2391d4818a3968203d181920ab972b4c86d32548cdb` |
| `data/annual_registry_2027.json` | `06b14458d64b5c28de8ff18bc6b6bf2f23851acc0d98d6c397389043faf31b40` |
| `data/catalog_coverage_2027.csv` | `42060227e15b9b5fbf9731bc47cba12508a73b6616e20ece12fc1da4b064c04e` |
| `national_data_baseline.py` | `0f89275eace0d540472b000f1f9e5bc01fe57a4163c3fd2f92f15ba828dd123e` |

产物（`docs/work_nodes/national_data_b001/`）：

| 文件 | 字节 | SHA256 | 说明 |
| --- | ---: | --- | --- |
| `baseline.json` | 5,171 | `d1c356f4c2f70e52374187eb692bb41c19069ae862cb8122140d2f23967e26df` | 三口径汇总、输入/产物 SHA、入口池、缺口频次 |
| `queue.json` | 2,429,312 | `8cfdc263b1bfd917a7462cac783e99dfd54208f0b052e0b5c41590eeaf0dfb86` | 生成器原样 931 行，可重放 |
| `queue-summary.json` | 1,077,969 | `d126d6b3ff01ee6e84aff6c4ff5d5ca634fdb0845e1f0d389a6d74eec875069c` | 只读摘要，`replayable=false` |
| `batch.json` | 48,415 | `9ebcc3238d59289f113017d937b4089b6f2f55e18a9d4f699a32245db5e1aca9` | 7+3 首批；绑定 `queue_sha256` 与 `selection_rule_version=b001-node0-v2` |
| `coverage-report.txt` | 512 | `ad2d976bd6a6ac0a6d99f123b8f5081621e74d603a52eb0ad0061b1934599b4d` | 覆盖率命令与原始报表 |
| `replay-check.json` | 6,780 | `182131d062f1a6221a36dd39cd37d5089f6ddb60df5296c47f6e2b9bdab5d888` | 从落盘 queue 重放 batch 的机器可读证据（含理由/next_step） |
| `protected-sha256.json` | 813 | `03e68433e45c60c532d48340b56aed727c59f280ba06c11404345c10c20c199f` | 五个保护适配器 SHA |

批次重放验证：读取落盘 `queue.json`，用同一 `queue_sha256` 调 `select_batch`，扩目录/补字段的 ID、角色、理由和 next_step 均与 `batch.json` 一致；摘要重放被显式拒绝。机器可读结果见 `replay-check.json`。

## H. 失败、边界与下一步

- 未联网采集、未解析任何新原件、未采用任何方向、未发布 SQLite、未构建新包；本报告不得被当作目录或资格增长。
- `effective_counts` 仍完整 `json.load` 大 admissions 并构造内存视图；只有 raw `stream_counts` 是流式。峰值内存本轮未测，不能说整套重算有界内存；这是扩规模前需补的实测限制。
- 240 个扩目录形态单位缺可重放归属证据，已计数并排除，不是静默丢弃；若其中确为合法入口，需要 Codex 补证或后续结构化阻塞，本批不自行扩绑定。
- 明确归为 `missing_trusted_entry` 的仍是 **河北建筑工程学院（688908263 / 10084）** 与 **复旦大学（368127 / 10246）**：覆盖文件写“尚未核准该单位的官方招生网站入口”，即使字符串里有网址也不当作已核准入口。
- `source_count` 与本地 `original_urls` 数量不同（如山东农业大学 1001 vs 40）；本地 40 条也可能含分页/导航变体，节点1 必须按最终原件对账。
- 下一步（节点1）：对首批 10 校建立原件清单与目录全集对账；每校保留 `document_id / URL / 标题 / 年份 / SHA / 路径 / 状态`，失败按互斥主因分类。做完一批即报，不静默长跑。

## I. 发给 Codex 的节点简报

```text
批次/节点/分支/base：b001 / 节点0 重交 / data/national-b001 / 67d5e24
代码提交：9b1c945（生成基线时 tracked_dirty=false）
实际处理：0/10 采集；931/931 队列已建立；首批 10 校入队（7 扩目录 + 3 补字段）
三口径：raw 650 学费（378 年费/272 全程）、1,680 学制、645 可算全程
        effective-present 912（640/272）、1,942、907
        verified 912、1,942、907；完整资格 0；冲突暂停 0
入口门禁：433 扩目录形态 → 193 entry_trusted，240 排除；missing_trusted_entry 2
R1—R4：partial 缺口与 remaining_*、入口证据门禁、verified 逐条重放、完整 queue+SHA 重放均已实现
测试：49 项通过；新增模块含分支覆盖 99%（359 语句/116 分支）
产物：baseline.json d1c356f4…、queue.json 8cfdc263…（2,429,312 B）、
      queue-summary.json d126d6b3…（replayable=false）、batch.json 9ebcc323…
保护：五个 PDF 适配器 SHA 与 base 一致；未改采用数据/发布库/runtime/Key
未解项：未采集/解析/采用/发布；effective 重算非有界内存；240 个入口待补证
下一节点：节点1 首批 10 校原件与目录全集清单（不碰完整资格标志）
```
