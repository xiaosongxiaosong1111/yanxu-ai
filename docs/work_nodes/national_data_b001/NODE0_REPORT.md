# 全国采用数据 · 节点0报告（b001，R2 可见原文角色与目标重放）

状态：`baseline_ready_r2_role_replayed_resubmitted`（工作状态，不等于任何学校目录或资格已完成）
生成时间：2026-11-09（本机时区 UTC+8）
执行者：Claude（数据）；独立验收：Codex
审查对象：`data/national-b001`，base `67d5e24`；被审查 R2 结论对应 HEAD `cfc38ce` 之前的实现
R2 角色重放提交：`cfc38ce`（`fix: replay visible entry role and target from saved originals`）
R2 自有标签准入提交 / 生成提交：`1dd44aa`（`fix: admit owned labeled entry roles and reject foreign site labels`）
证据提交：包含本文件与全部产物的 `data/national-b001` 分支 HEAD（SHA 见交付消息；不把提交自身 SHA 写进提交内容）
手册：[CLAUDE_NATIONAL_DATA_RUNBOOK](../../CLAUDE_NATIONAL_DATA_RUNBOOK.md)（已在本分支仓库内）

## 0. 本轮修正摘要（只补 R2 可见原文角色与目标重放）

| Codex R2 指出的问题 | 本轮修正 | 可复核位置 |
| --- | --- | --- |
| `national_data_baseline.py:262,342` 只验证“URL 出现在原件里”，没有证明“原件把该 URL 声明为该校官网” | 新增可见正文提取与角色重放：从落盘原件取出真实文章正文，排除脚本、样式、注释、页面导航/页脚/侧栏、隐藏控件和 `display:none/visibility:hidden` 内容；在 URL/链接旁重放可见角色词（学校官网、研究生院/研招网站、招生信息网、目录/章程等），角色必须由当前单位拥有，并与入口 `relation` 相容 | `_VisibleHTMLParser`、`_article_fragment`、`_visible_original`、`_visible_quote_spans`、`_role_evidence`、`_relation_accepts`、`_replay_entry_role` |
| Codex 用真实未改动原件 `368295`、`368049`，只把内存入口换成 `https://www.chsi.com.cn`，生产链仍返回 verified/trusted/selected | 现在两例都返回 `rejected / platform_not_official_site`：角色窗口内的“学信网/研招网/中国研究生招生信息网”等平台名，若无本校自指，不能作为学校官网声明；生产 `verify_entry → build_queue → select_batch` 两池均不选 | `test_chsi_counterexample_is_not_trusted_or_selected`、`test_chsi_student_status_url_is_not_a_school_site_role`、`test_admissions_page_platform_url_is_not_its_own_site` |
| 合成“URL 只出现在 HTML 注释里”仍通过 | 注释、`script`、`style`、`noscript`、`template`、`svg` 不计入可见正文；只出现在这些位置的 URL 返回 `quote_not_in_original` | `test_url_only_in_a_comment_or_script_is_not_visible_evidence` |
| 无关链接、其他单位网址未拒绝 | 可见 URL 若在锚点内，锚点目标必须与声明 URL 同 host/path/query/fragment；只有链接目标没有角色文字的“这里/点击查看”返回 `role_declaration_missing`；登记表中其他单位的精确名称/官网 host 返回 `foreign_unit_reference` / `other_unit_host`；`labeled` 角色另行检查标签本身，未登记的外校名称也不能借全文出现的本校名通过 | `_enclosing_anchor`、`_replay_entry_role`、`_foreign_name_in`、`_foreign_label_in`、`_official_hosts`；对应用例见 F 节 |
| 不要只加域名黑名单 | 没有新增域名黑名单。判定依据是可见角色语义、单位归属、平台标签、锚点目标与 relation 相容性；`chsi.com.cn` 只是被拒绝样本之一，不是按域名规则拒绝 | 同上 |
| 扩目录、补字段两池都要有反例 | 两池都新增“证明缺少重放角色/角色证据则不得入选”的 RED/GREEN；现有两池都调用 `entry_trusted` | `test_expansion_pool_rejects_a_proof_without_a_replayed_role`、`test_backfill_pool_rejects_a_proof_without_role_evidence` |
| 数字按实报，可低于 405 | 真实重放后 **verified 403 / rejected 528**；相对上一轮 405/526，16 个旧 verified 被拒、14 个旧 rejected 重新通过。没有为保住 405 放宽规则 | C1 节；`baseline.json.entry_proof` |

**未重做**：R1 部分覆盖、R3 verified 逐条重放、R4 queue→batch 重放、费用/学制口径均未改；raw/effective/verified 数字仍为 650/912/912 学费、1,680/1,942/1,942 学制、645/907/907 可算全程、完整资格 0。队列内容因入口证明新增角色字段和 7 个自有标签单位准入而变化；batch 的 7+3 单位、角色、理由与 next_step 不变。

## A. 身份与隔离

| 字段 | 实际值 |
| --- | --- |
| 仓库 / 分支 / base SHA | `https://github.com/xiaosongxiaosong1111/yanxu-ai`，`data/national-b001`，`67d5e24` |
| R2 角色/目标重放提交 | `cfc38ce978bd44e3af2c9a8673d02af912ef7de3`（2 文件，+677/−19） |
| R2 自有标签准入提交 / 生成提交 | `1dd44aa5390437331bb403380ed3b8d2be1279d5`（2 文件，+70/−2） |
| 生成时工作区 | `tracked_dirty=false`；`baseline.json.git.commit=1dd44aa` |
| 独立工作树 | `D:/yx-data-b001`（短路径；未复用 `E:/PythonSpace/yanxu-ai`，未碰 `D:/CodexRuntime`） |
| 数据根 / 产物根 | `D:/yx-data-b001` / `D:/yx-data-b001/data/batch_runs/b001` |
| 用户 runtime / Key | 未读取、未复制、未覆盖；本工作树没有 `data/runtime/` 发布库 |
| Python | 3.13.9（工作树 `.venv`；coverage 只装在隔离 venv） |
| 原文提取器 | `C:/Program Files/Git/mingw64/bin/pdftotext.exe` → `pdftotext version 4.00` |
| 坐标提取器 | `E:/miniConda/envs/yanxu-pdf/Library/bin/pdftotext.exe` → `pdftotext version 26.07.0`；未加入 PATH |
| 公开包 | `data/bundles/catalog-data.zip` SHA256 `637f1990c02435127cca3c7a308b08087e24db40d8aefba41290fad294936478`；manifest SHA256 `52655a92d671025e9cfb086e3a528d610d52ed53d09fd2c3f1b9847a87bf1394` |
| 恢复结果 | `prepare_data.py` 离线恢复 6,521 个文件，`published=false` |
| 付费搜索 / 模型调用 | 0 次；未读取任何 Key，未访问 4180 |

五个受保护 PDF 适配器字节与 base `67d5e24` 一致（SHA 见 `protected-sha256.json`）：

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

- `912 − 650 = 262` 全部来自既有白名单补充（郑州大学 252、南开大学 10；7 条规则全部有效，`invalid_rule_ids=[]`）。既有 650 条在有效数据中仍逐条重放为 verified。
- 本次 `verified` 的 claim 状态计数：学费 `{verified: 912}`、学制 `{verified: 1,942}`、资格 claim `{}`。没有 `needs_review`/`conflict` 被算入 verified。
- 完整资格仍为 **0**：22,356 条 `conditions` 均为空。`eligibility_evidence_complete=true` 单独出现不会计数，本轮也未改写任何标志。
- 目录状态：`recorded_verified` 51、`partial` 84、`not_collected` 796。
- 采集状态：`collected` 609、`needs_binding` 217、`truncated` 105。
- 覆盖 CSV 的 `program_count` 与 raw 流式统计逐校一致（931 行，0 个不一致）；effective 计数也 0 个不一致。

**复现注意**：既有补充规则内部调用 `shutil.which("pdftotext")`；CLI 运行时必须让 `pdftotext` 在 PATH 上（本机为 `C:\Program Files\Git\mingw64\bin` 前缀），否则 effective 会退化。`--raw-pdf` 只指定审计记录的路径，不能替代 PATH。

## C. 931 单位完整队列（可重放）与入口证明

`queue.json` 是生成器原样输出，931 行，3,113,750 字节，SHA256 `da6393ee98bbcbd95a1e0673a8c847909e8af63e0b3d6ee82821c02c61ce3a9f`（LF 规范字节，等于 Git blob）。每行保留身份、原始与采用、入口证据与证明、三层计数、剩余缺口、`gaps/count_mismatch/effective_count_mismatch`。

每行持久化 `entry_verification`，由 `verify_entry` 产生：

```json
{"status": "verified|rejected", "reason": "...", "unit_id": "...", "year": 2027,
 "official_url": "...", "evidence_url": "...", "evidence_sha256": "...",
 "document_id": "...", "document_sha256": "...", "document_path": "snapshots/national/2027/...",
 "file_sha256": "...", "quote_match": "literal|link_target|", 
 "role_category": "home|admissions|catalog|labeled|", "role_evidence": "...",
 "proof_sha256": "<64 hex>"}
```

`proof_sha256` 是对“行上实际保存的入口证据 + 全部证明字段（含角色类别/角色原句）”的规范化指纹。`entry_trusted` 要求：无 `missing_trusted_entry`、入口 URL 非空且不含“尚未核准”、`status=verified`、verification/relation 在支持集合内、`role_category` 属于 `home/admissions/catalog/labeled` 且 `role_evidence` 非空、证明的 unit/year/official_url 与行一致、引文 host 与入口 host 一致、`proof_sha256` 为 64 位十六进制且**重新计算的指纹与它完全相等**。任何一处被篡改，选择器都不再信任。

### C1. 整库证明结果

| 状态 | 单位数 |
| --- | ---: |
| `verified`（`saved_original_replayed`） | 403 |
| `rejected` | 528 |
| 合计 | 931 |

verified 的可见角色分布：

| 角色类别 | 单位数 | 含义 |
| --- | ---: | --- |
| `home` | 295 | 学校/研究生院/研招办网站、网址、主页等本校站点声明 |
| `admissions` | 94 | 招生信息网、研究生招生网、研招在线等招生站点声明 |
| `catalog` | 7 | 招生专业目录/简章/章程等目录类声明 |
| `labeled` | 7 | 标签本身可归属本校的具名站点声明（如“山西大同大学官方网站”） |
| 合计 | 403 | |

拒绝原因（互斥主因，按代码判定顺序取第一个失败）：

| 原因码 | 单位数 | 含义 |
| --- | ---: | --- |
| `no_entry_evidence` | 293 | 单位没有 `official_url_evidence` 字典 |
| `no_exact_annual_document` | 147 | 年度登记中没有与证据 URL+SHA 完全一致的原件 |
| `unsupported_relation` | 45 | relation 不在支持的入站关系集合 |
| `unsupported_verification` | 20 | verification 为 rejected/failed/未知值 |
| `relation_role_mismatch` | 11 | 可见角色类别与持久化 relation 不相容（如目录/文章链接却声明 home 关系） |
| `link_target_mismatch` | 9 | 可见 URL 所在锚点的实际目标与声明 URL 不同 host/path |
| `ambiguous_saved_original` | 1 | 同目录下多个文件匹配 SHA 前缀 |
| `quote_host_mismatch` | 1 | 引文 host 不是入口 host |
| `linked_target_mismatch` | 1 | `linked_url` 与引文 canonical URL 不一致（含 query/fragment） |
| 合计 | 528 | |

与上一轮 405/526 相比：

- **16 个旧 verified 被拒**：10 个 `relation_role_mismatch`（`367889, 367876, 302973928, 368108, 368197, 368291, 302961334, 368431, 368430, 368485`）；6 个 `link_target_mismatch`（`367985, 368112, 3416382139, 368515, 368521, 368593`）。前者可见声明是目录/文章链接而非官网主页，后者可见 URL 的锚点实际指向别处（如 `368521` 指向 `utibet` 列表页、`368593` 指向列表页、`368515` 目标含错误百分号编码）。
- **14 个旧 rejected 重新通过**：`367884, 367803, 367936, 368202, 368161, 368207, 368242, 2807891665, 368359, 302976118, 2124439800, 678380, 368512, 368522`。原因是旧检查把合法站点声明误判为平台/他校，新角色重放在真实正文中找到了本校自有声明。
- 7 个 `labeled` 单位（`367902, 2125710548, 368097, 368237, 368494, 3416381895, 368574`）的证明通过后，现在也进入 `entry_trusted`；此前它们显示 verified 却因类别集合缺失而不被选择，属于门禁不一致，本轮已统一。

因此 `missing_trusted_entry` 为 **528**，不是上一轮元数据门禁下的 2。上一轮只检查非空元数据，2 是“没有网址/明写尚未核准”的数量；现在按真实原件角色重放，403 个单位有可重放入口，其余 528 个必须补证。这是更严格也更诚实的结果，不能当作退步掩盖。

### C2. 扩目录池

`program_count=0`、`collection_status=collected`、原因含“待解析”的单位共 **433** 个：

| 入口证明 | 单位数 |
| --- | ---: |
| `entry_trusted=true` | 133 |
| `entry_trusted=false` | 300 |

300 个被排除单位的证明原因：`no_entry_evidence` 234、`no_exact_annual_document` 42、`unsupported_relation` 7、`unsupported_verification` 6、`relation_role_mismatch` 5、`link_target_mismatch` 5、`linked_target_mismatch` 1。没有“verified 但 untrusted”的残留：所有 403 个 verified 都通过同一套 `entry_trusted` 指纹与角色检查。

### C3. 缺口频次

| 缺口 | 单位数 | 含义与下一步 |
| --- | ---: | --- |
| `missing_trusted_entry` | 528 | 入口证明未通过重放；交 Codex 补证，本批两池都不选 |
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

| 角色 | unit_id | 代码 | 名称 | 省份 | 方向 | source_count | URL 型/PDF/总数 | 可见角色 | 剩余 学费/学制 | 选入理由 / 下一步 |
| --- | --- | --- | --- | --- | ---: | ---: | --- | --- | --- | --- |
| 扩目录 | 368295 | 10434 | 山东农业大学 | 山东 | 0 | 1001 | 38/0/40 | home“研究生处网址” | — | 目录型 URL 最多；`source_count` 不是已下载原件数 |
| 扩目录 | 368049 | 10152 | 大连工业大学 | 辽宁 | 0 | 40 | 37/0/40 | home“学院官方网站” | — | 确认学院/分页/附件差集 |
| 扩目录 | 368146 | 10280 | 上海大学 | 上海 | 0 | 61 | 36/0/40 | home“网址” | — | 多学院目录全集对账 |
| 扩目录 | 368275 | 10458 | 山东艺术学院 | 山东 | 0 | 66 | 36/0/40 | home“网址” | — | 艺术类方向单独核对 |
| 扩目录 | 368611 | 10758 | 新疆农业大学 | 新疆 | 0 | 46 | 36/0/40 | home“研究生院网站” | — | 历史材料不得计入 2027 |
| 扩目录 | 368142 | 10251 | 华东理工大学 | 上海 | 0 | 58 | 35/10/40 | home“网址” | — | 确认是否只取得首屏 |
| 扩目录 | 368436 | 10593 | 广西大学 | 广西 | 0 | 40 | 35/0/40 | home“网址” | — | 顶替未通过重放的阜阳师范大学 |
| 补字段 | 367929 | 14430 | 中国科学院大学 | 北京 | 5,165 | 34 | 4/0/34 | admissions“招生信息网” | 5,165/5,165 | 方向最多；补费用/学制原文依据，不改完整资格标志 |
| 补字段 | 367859 | 10027 | 北京师范大学 | 北京 | 463 | 20 | 4/1/20 | admissions“研究生招生网” | 463/463 | 保留 `partial_catalog/review_pending`；不勾整校完成 |
| 补字段 | 368201 | 10298 | 南京林业大学 | 江苏 | 305 | 14 | 8/0/14 | admissions“研究生招生信息网” | 305/305 | 顶替入口证明未通过的同济大学 |

两处替换仍是确定性结果，不是手写学校名：

- **阜阳师范大学 `688908731`（10371）**：扩目录排序本在其位；但入口证据无法在年度登记中找到精确原件（无 `document_id`、无落盘快照），证明为 `rejected / no_exact_annual_document`，因此不入池。按排序顺延为 **广西大学 `368436`**。
- **同济大学 `368128`（10247）**：补字段池中方向数仅次于国科大，但入口证明为 `rejected / no_exact_annual_document`，按 R2 门禁排除，顺延为 **南京林业大学 `368201`**。

首批 10 校的入口原件证明（均为 `saved_original_replayed`；路径相对 `data/`，文件字节 SHA 与证明一致）：

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

首批结论只能写“10 校已入队且入口证明通过可见角色重放”，不能写“10 校目录完成”或“10 校资格完成”。节点1 结束时才判断每校终态或阻塞。`source_count` 是公开包累计来源数；URL 数不等于独立原件数，节点1 必须按最终 URL、MIME、SHA、真实年度和目录内容对账。

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

- 测试：**97 项通过**（上一轮 94 项）。新增 3 项针对本轮门禁：自有 `labeled` 站点可被信任并入选、未登记的“乙大学官方网站”不能借全文出现的本校名通过、通用“研究生招生信息网主页”可由本校文章拥有。
- R2 角色/目标重放的红绿用例（`EntryRoleReplayTests`）覆盖：chsi 入口反例（真实山东农业大学/大连工业大学原件形态）、隐藏 `hid_content` 文章、注释/脚本 URL、无关锚点文字、登记表他校名称、未登记他校标签、锚点目标不一致、`linked_url` path/fragment 不一致、他校 host 归属、平台名邻近但不遮蔽本校角色、目录词早于站点标签、`研招网址` 不被误判为平台、缩写标签由全文单位名拥有、目录标题与 relation 相容性、百分号编码标点、`backface-visibility` 不隐藏正文、两池缺少角色/角色证据时拒绝。
- 含分支覆盖：**93%**，829 语句仅 42 行未执行，354 分支仅 30 个部分分支；按分支计 `(354−30)/354 = 91.53%`，高于手册“核心修改含分支覆盖至少 80%”的要求。缺失行：`261->263, 330, 332, 333->exit, 346, 354, 370-374, 379->381, 400-402, 419, 420->422, 426-431, 447-448, 454, 456, 459-460, 482->486, 515, 532, 536, 599, 616->615, 617->615, 628, 646, 654, 666, 672, 697-698, 753-754, 768-769, 793, 796->791, 1310`。
- 覆盖率证据绑定：`coverage-report.txt` 以 LF 字节写入，SHA256(LF) `a811eef6b2490b282aab8b646b53d434b90af308ba4fcacf5d23b34231c67bc1`，Git blob SHA-1 `b1ae87713ffb0d98b8430ed49934f32b0ad0fdb2`；`coverage-evidence.json` 同时记录两者。仓库 `core.autocrlf=true`，编辑器另存或重新 checkout 可能变成 CRLF；**以 Git blob 为准**，字节级核验用 `git cat-file blob`。
- 完整公开包 CLI：`cli-recompute.log` 记录命令、PATH 前缀、stdout/stderr 与运行元数据；`cli-run.json` 机器可读。结果 **exit 0，83.680 秒**；真实工作进程峰值工作集 **1,386,397,696 字节（约 1.29 GiB）**；直接子进程加全部后代的峰值之和为 1,397,379,072 字节（保守上界）。测量方法为每 200 ms 对直接子进程及其后代调用 `psapi!GetProcessMemoryInfo` 读取 `PeakWorkingSetSize`。两次失败的 `Start-Process` 尝试（参数未引号、仅在 0.5 秒内报参数错误）未用于任何证据；全部产物来自上述 exit 0 的完整运行。
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
| `national_data_baseline.py`（工作字节） | `0bb4d6e6115413fe90cfba9c18d71633af0f90cb0d67c2fa3d339eb1b4189bc1` |
| `national_data_baseline.py`（Git blob `1dd44aa:...`） | `83ab47cef8e35d824be30b853948a612403eb19d`（SHA-1） |

产物（`docs/work_nodes/national_data_b001/`）：

| 文件 | 字节 | SHA256 | 说明 |
| --- | ---: | --- | --- |
| `baseline.json` | 5,806 | `848ce7c6ade4e0f52e6a1d6249cf1dc62e186bfab4eca29d743021c40cc02ec0` | 三口径、输入/产物 SHA、入口池、证明状态与原因、缺口频次 |
| `queue.json` | 3,113,750 | `da6393ee98bbcbd95a1e0673a8c847909e8af63e0b3d6ee82821c02c61ce3a9f` | 生成器原样 931 行，含角色证明，可重放 |
| `queue-summary.json` | 1,063,548 | `db848661b33afdbbabfa84aeba696cf8769e4c5bf01c904169ea97e84d6f6815` | 只读摘要，`replayable=false` |
| `batch.json` | 56,329 | `531ce9a4029c0734b280a5cced4eb93dc3a99c5feee0ea6fc80c43f82378a3ce` | 7+3 首批；绑定 `queue_sha256` 与 `selection_rule_version=b001-node0-v4` |
| `replay-check.json` | 13,555 | `493b9f510720341c178653a6b46a19bf1b6036203491b5ab1a6a2bea2f7df703` | 从落盘 queue 重放 batch；含 10 校证明路径/文件 SHA/角色 |
| `coverage-report.txt` | 1,408 | `a811eef6b2490b282aab8b646b53d434b90af308ba4fcacf5d23b34231c67bc1`（LF；blob `b1ae8771...`） | 覆盖率命令、测试尾部与原始报表 |
| `coverage-evidence.json` | 1,395 | `5aaf9a6007e67b281924f99a008455df90a0dcdc1ba21a1bce664275a2c54453` | 覆盖率机器可读证据与换行绑定 |
| `cli-recompute.log` | 1,709 | `0e2a464bdd13ebaa02ea188bd9e78da277c46d1e33703d1a099656522082a0ff` | 完整 CLI 成功日志与运行元数据（LF） |
| `cli-run.json` | 1,158 | `c84a2d468e509ef7b01708f63d9104e2c5bc0ad0e143f393c39c12760c0ece98` | exit 0、83.680s、峰值 1.29GiB |
| `protected-sha256.json` | 824 | `fd1f6020da4c2f774182bcd18e82cecb089fe97d97b2cc6aa5792109bf5bde95` | 五个保护适配器 SHA（LF） |

`baseline.json.artifacts` 与上表前三个生成产物一致：queue `da6393ee…`、summary `db848661…`、batch `531ce9a4…`。批次重放验证：读取落盘 `queue.json`，用同一 `queue_sha256` 和 `b001-node0-v4` 调 `select_batch`，7 扩目录 + 3 补字段的 ID、角色、理由、next_step 全部与 `batch.json` 一致；摘要重放被显式拒绝。机器可读结果见 `replay-check.json`（`replay_matches_batch=true`，`summary_rejected=true`）。

## H. 失败、边界与下一步

- 未联网采集、未解析任何新原件、未采用任何方向、未发布 SQLite、未构建新包；本报告不得被当作目录或资格增长。
- **528 个单位入口未通过可见原文重放**，其中 293 个单位根本没有入口证据字典；这些单位已写入队列和 `baseline.json`，不是静默丢弃。Codex 若确认其中某入口合法，需要补一份可重放的年度原件/归属声明；在补证前本批两池都不选。
- 两个具名结构化阻塞：**阜阳师范大学 `688908731 / 10371`** 和 **同济大学 `368128 / 10247`**，原因均为 `no_exact_annual_document`（年度登记没有与入口证据 URL+SHA 完全一致的原件）。阜阳被广西大学顶替，同济被南京林业大学顶替；不是把未核验单位留在批次里。
- 角色归属仍是启发式：`home/admissions/catalog` 的归属允许本校名出现在正文；`labeled` 必须由标签本身或紧邻上下文拥有，并拒绝未登记的外校实体名（如“乙大学官方网站”）。中文没有分词，实体名判定只作用于 `labeled` 标签，不会扫描任意前置文字，避免把“考生关注学校”误判成校名。未登记的“乙大学网站”（无“官网”字样）如走通用 `home` 标签仍可能借正文本校名通过；这是已知边界，节点1 若遇到同名外校条目需人工复核。
- `effective_counts` 非有界内存，实测峰值约 1.29 GiB；不是“已验证有界”，扩规模前需改为分块/流式或明确上限。
- `source_count` 与本地 `original_urls` 数量不同（如山东农业大学 1001 vs 40）；本地 40 条也可能含分页/导航变体，节点1 必须按最终原件对账。
- 下一步（节点1，尚未开始）：对首批 10 校建立原件清单与目录全集对账；每校保留 `document_id / URL / 标题 / 年份 / SHA / 路径 / 状态`，失败按互斥主因分类。做完一批即报，不静默长跑；不碰任何完整资格标志。

## I. 发给 Codex 的节点简报

```text
批次/节点/分支/base：b001 / 节点0 R2 角色重放重交 / data/national-b001 / 67d5e24
被审查 R2 实现：cfc38ce 之前；R2 角色/目标重放提交：cfc38ce
自有标签准入/生成提交：1dd44aa（生成时 tracked_dirty=false）；证据提交=本文件所在分支 HEAD
实际处理：0/10 采集；931/931 队列已建立；首批 10 校入队（7 扩目录+3 补字段）
三口径：raw 650 学费（378 年费/272 全程）、1,680 学制、645 可算全程
        effective-present 912（640/272）、1,942、907
        verified 912、1,942、907；完整资格 0；冲突暂停 0
入口证明：931 个单位全部持久化 proof；verified 403 / rejected 528
          verified 角色：home 295、admissions 94、catalog 7、labeled 7
          reasons: no_entry_evidence 293、no_exact_annual_document 147、
          unsupported_relation 45、unsupported_verification 20、
          relation_role_mismatch 11、link_target_mismatch 9、
          ambiguous_saved_original 1、quote_host_mismatch 1、linked_target_mismatch 1
扩目录池：433 → trusted 133 / untrusted 300
R2：可见正文角色+锚点目标重放；排除注释/脚本/隐藏内容；平台名、他校名/他校 host、
    无关链接、role/relation 不相容均拒绝；未加域名黑名单；两池都有缺角色反例
    368295/368049 的 chsi 反例均 rejected/platform_not_official_site，生产链不选
    verified 从 405 变为 403：16 旧 verified 被拒（10 relation_role_mismatch、6 link_target_mismatch），
    14 旧 rejected 重新通过；未放宽规则
R1/R3/R4：未重做，数字与上一轮一致；batch 7+3 单位与角色不变
测试：97 项通过；含分支覆盖 93%（829 语句/354 分支/30 部分；分支计 91.53%）
覆盖率绑定：coverage-report.txt SHA256(LF) a811eef6…，git blob b1ae8771…；autocrlf 只影响 checkout
CLI：exit 0，83.680s，工作进程峰值 1,386,397,696 B（约 1.29GiB），后代峰值和 1,397,379,072 B
     成功日志 cli-recompute.log 0e2a464b…，元数据 cli-run.json c84a2d46…
产物：baseline.json 848ce7c6…、queue.json da6393ee…（3,113,750 B）、
      queue-summary.json db848661…（replayable=false）、batch.json 531ce9a4…（v4）、
      replay-check.json 493b9f51…（replay_matches_batch=true，summary_rejected=true）
保护：五个 PDF 适配器 SHA 与 base 一致；未改采用数据/发布库/runtime/Key
未解项：未采集/解析/采用/发布；528 个单位入口需补证；effective 重算非有界内存
下一节点：节点1 首批 10 校原件与目录全集清单（不碰完整资格标志）
```
