# 全国采用数据 · 节点0报告（b001）

状态：`baseline_ready`（工作状态，不等于任何学校目录或资格已完成）
生成时间：2026-11-09（本机时区 UTC+8）
执行者：Claude（数据）；独立验收：Codex（待）
手册：[CLAUDE_NATIONAL_DATA_RUNBOOK](../../CLAUDE_NATIONAL_DATA_RUNBOOK.md)（随任务提供，尚未进入仓库）

## A. 身份与隔离

| 字段 | 实际值 |
| --- | --- |
| 仓库 / 分支 / base SHA | `https://github.com/xiaosongxiaosong1111/yanxu-ai`，`data/national-b001`，`67d5e24` |
| 独立工作树 | `D:/yx-data-b001`（短路径；未复用 `E:/PythonSpace/yanxu-ai`，未碰 `D:/CodexRuntime`） |
| 数据根 / 产物根 | `D:/yx-data-b001` / `D:/yx-data-b001/data/batch_runs/b001` |
| 用户 runtime / Key | 未读取、未复制、未覆盖；本工作树没有 `data/runtime/` 发布库 |
| Python | 3.13.9（工作树 `.venv`，`requirements.txt` 已装：ijson 3.4.0.post0、pdfplumber 0.11.10、xlrd 2.0.2） |
| 原文提取器 | `C:/Program Files/Git/mingw64/bin/pdftotext.exe` → `pdftotext version 4.00` |
| 坐标提取器 | `E:/miniConda/envs/yanxu-pdf/Library/bin/pdftotext.exe` → `pdftotext version 26.07.0`；同目录 `pdftocairo.exe` 存在 |
| 公开包 | `data/bundles/catalog-data.zip` SHA256 `637f1990c02435127cca3c7a308b08087e24db40d8aefba41290fad294936478`；manifest SHA256 `52655a92d671025e9cfb086e3a528d610d52ed53d09fd2c3f1b9847a87bf1394` |
| 恢复结果 | `prepare_data.py` 离线恢复 6,521 个文件，`published=false` |
| 付费搜索 / 模型调用 | 0 次；未读取任何 Key，未访问 4180 |
| 符号链接 | 工作树为普通 Git 工作树；未发现指向原项目运行目录的链接 |

五个受保护 PDF 适配器字节在本次前后一致，SHA 见 `protected-sha256.json`：

| 文件 | SHA256 |
| --- | --- |
| `collectors/catalog_pdf_tables.py` | `cbaf2b3a78822e6558f939e8df4a8cf0660577223c3f873be2f3bd450a94d384` |
| `collectors/catalog_pdf_variants.py` | `76583e49222980947a061fad641742a07fe85103b20a9759194c811384718971` |
| `collectors/catalog_pdf_national.py` | `19772baec461b81d21ee2bc555193e7248ca1d9a2ec85bf0c4e12e808c97c55e` |
| `collectors/catalog_pdf_national_v2.py` | `40528ad943d2557c7b5855a3afeceb44dffb47f528a90aaccd83c9804daa0d9d` |
| `collectors/catalog_hit_public.py` | `e757752d9a707007288d55417318a00d947bbe30c55f124a82742cd8231f5f1e` |

## B. 基线（本批前快照，流式重算）

口径：年度分母是**2027 年度确认的 931 个独立单位**，不是通用索引 936；目录、资格、费用、学制分别统计，非空字段不等于原文已核验。

| 指标 | 原始包 | 既有规则补充后（enrichment） | 说明 |
| --- | ---: | ---: | --- |
| 年度确认单位 | 931 | 931 | 另 5 条通用索引记录 `not_listed`，不计入分母 |
| 有目录记录的年度单位 | 135 | 135 | 14.5005%；`catalog_complete=false` 维持 |
| 专业/方向 | 22,356 | 22,356 | ID 无重复；135 个单位 |
| 完整资格 | 0 | 0 | `conditions` 为空 22,356 条，全部 `requires_review=true` |
| `eligibility_evidence_complete=true` | 0 | 0 | 未做任何标志改写 |
| verified 学费（任一口径） | 650 | 912 | 年费 378→640；明确全程 272→272 |
| 既有白名单新增 | — | +262 | 7 条规则全部有效，`invalid_rule_ids=[]`；郑大 252、南开 10 |
| verified 学制 | 1,680 | 1,942 | 有效口径覆盖 18 个单位 |
| 可算全程费用 | 645 | 907 | 年费×同范围学制 635 + 明确全程 272 |
| evidence 冲突暂停 | — | 0 | `suspended_conflicts=0` |

与手册基线核对：目录 135/931、方向 22,356、完整资格 0、原始学费 650（378/272）、补充后 912（640/272）、学制 1,942、可算全程 907 全部一致。包 SHA 与手册一致，未发现上游数据变化。

目录状态：`recorded_verified` 51、`partial` 84、`not_collected` 796。
采集状态：`collected` 609、`needs_binding` 217、`truncated` 105。
覆盖 CSV 的 `program_count` 与本次流式统计逐校一致（931 行，0 个不一致）。

## C. 931 单位缺口队列

`queue.json` 每行保留真实 `unit_id`、单位代码、名称、省份、年度、证据数、目录/采集状态、原始与采用方向数、费用/学制/完整资格计数、缺口和原因原文。一个单位可以有多个缺口；缺口不互斥。

| 缺口 | 单位数 | 含义与下一步 |
| --- | ---: | --- |
| `missing_catalog` | 796 | 尚未采用任何 2027 方向；不等于未招生 |
| `parser_pending` | 433 | 已保存公开原文、但尚无采用方向；节点1/2 解析并做目录全集对账 |
| `original_pending` | 413 | 来源受限、截断或尚未取得可核验原文；按失败类型留档，不反复撞门禁 |
| `missing_full_qualification` | 135 | 已有方向但没有任何完整资格条款；节点3 的语义审校范围 |
| `missing_tuition` | 124 | 已有方向但无 verified 学费 |
| `missing_duration` | 119 | 已有方向但无 verified 学制 |
| `partial_catalog` | 84 | 已有部分专业资料，全校目录未对账完整；不得勾整校完成 |
| `review_pending` | 84 | 部分目录待独立审校 |
| `missing_trusted_entry` | 2 | 官方入口未核准；交 Codex 补证，本批跳过该单位 |

注：`original_pending` 与 `parser_pending` 是按现有覆盖文件的状态归因；节点1 必须以实际原件重新判定，不能把本表当最终结论。

## D. 首批 10 校（真实队列，不手填学校名）

选择规则已写成代码并有测试：扩目录池 = `program_count=0`、`collection_status=collected`、原文原因含“待解析”、有可信入口且有真实原文 URL；按“目录型 URL 数 → PDF 数 → URL 总数 → unit_id”确定性排序。补字段池 = 已有方向但学费与学制均空；原文已收集的优先，再按方向数排序。

| 角色 | unit_id | 代码 | 名称 | 省份 | 方向 | 来源数 | 目录状态 | 选入理由 / 下一步 |
| --- | --- | --- | --- | --- | ---: | ---: | --- | --- |
| 扩目录 | 368295 | 10434 | 山东农业大学 | 山东 | 0 | 1001 | not_collected | 已存 2027 原文 40 个 URL；先建原件清单与目录全集对账 |
| 扩目录 | 368049 | 10152 | 大连工业大学 | 辽宁 | 0 | 40 | not_collected | 同上；确认学院/分页/附件差集 |
| 扩目录 | 688908731 | 10371 | 阜阳师范大学 | 安徽 | 0 | 73 | not_collected | 同上；注意单位代码为 9 位，ID 以年度登记为准 |
| 扩目录 | 368146 | 10280 | 上海大学 | 上海 | 0 | 61 | not_collected | 同上；多学院目录需全集对账 |
| 扩目录 | 368275 | 10458 | 山东艺术学院 | 山东 | 0 | 66 | not_collected | 同上；艺术类方向与考试单元单独核对 |
| 扩目录 | 368611 | 10758 | 新疆农业大学 | 新疆 | 0 | 46 | not_collected | 同上；历史材料不得计入 2027 增量 |
| 扩目录 | 368142 | 10251 | 华东理工大学 | 上海 | 0 | 58 | not_collected | 同上；确认是否只取得首屏 |
| 补字段 | 367929 | 14430 | 中国科学院大学 | 北京 | 5,165 | 34 | recorded_verified | 方向最多且原文已收集；优先补费用/学制原文依据，不改完整资格标志 |
| 补字段 | 368128 | 10247 | 同济大学 | 上海 | 700 | 73 | recorded_verified | 原文已收集；费用/学制均缺 |
| 补字段 | 367859 | 10027 | 北京师范大学 | 北京 | 463 | 20 | partial | 原文已收集；同时保留“目录部分”状态，不勾整校完成 |

首批结论只能写“10 校已入队”，不能写“10 校目录完成”。节点1 结束时才判断每校的终态或阻塞。

## E. 资格持久化最小方案（节点3 实现，本节点只设计）

目标不是把布尔值改真，而是让“条款 → 原文 → 适用范围 → 可执行条件 → 采用/失效”可回放。建议新增**永久文件** `data/qualification_adoption.json`（schema_version 1），不改 `admissions.json` 的既有字段语义：

```json
{
  "schema_version": 1,
  "reference_year": 2027,
  "clauses": [{
    "clause_id": "q-<sha256前缀>",
    "unit_id": "真实单位ID", "year": 2027,
    "document_id": "已读原文ID", "sha256": "原件SHA256",
    "quote": "完整原句", "locator": {"page": 1, "char_start": 0, "char_end": 0},
    "scope": {"department_codes": [], "program_codes": [], "study_modes": [],
              "degree_types": [], "exam_types": [], "plan_types": []},
    "ast": {"op": "and", "args": []},
    "status": "verified|partial|unsupported|unknown|conflict|superseded",
    "unknown": [{"quote": "未解析原句", "reason": "原因码"}],
    "exceptions": [], "supersedes": [], "conflicts_with": [],
    "review": {"reviewer": "独立复核者", "reviewed_at": "ISO8601", "version": 1}
  }],
  "adoptions": [{
    "program_id": "已采用方向ID", "unit_id": "…", "year": 2027,
    "clause_ids": ["…"], "eligibility_evidence_complete": false,
    "requires_review": true, "version": 1,
    "adopted_at": "ISO8601"
  }]
}
```

采用门禁（任一不满足即 `complete=false`、`requires_review=true`，并保留已核部分）：

1. 方向身份必须来自已采用目录（单位/年度/专业代码/学习方式一致），不能凭空生成 `program_id`。
2. 条款的 `document_id`、`sha256`、`quote`、`locator` 必须能由当前原文重放；`read_receipts` 只能来自真实已读凭证，禁止批量伪造。
3. 适用范围非空且由原文证明；共用条款按真实学院/专业范围引用，不能一校条款广播全国。
4. `ast` 只接受白名单算子（学历、毕业时间、工作年限、专业限制、学习方式、专项、境外学历、加试等），未知条件必须进 `unknown` 并保留原句，不得丢弃。
5. 完整资格还要求：该校当年度适用条款**全集**已对账、无 `unknown`/`conflict`/`superseded`、独立审校通过、所有引用当前有效。多数条款可算但少数不能算时，完整标志仍为 false。
6. 原文 SHA 改变、撤信、更正公告或范围变化 → 相关 adoption 停止参与算法，旧版本与审计保留。

接入点（缺一不可）：

| 环节 | 需要的改动 |
| --- | --- |
| 加载 | 新增 `qualification_repository.load/apply`，在 `service._load_snapshot` 中与 `apply_enrichment`、`apply_evidence_safety` 同序调用；输出到方向 payload 的只读视图 |
| 缓存/revision | `snapshot_cache.SnapshotCache` 的 `names` 增加该文件；文件字节变化必须触发 revision 变化 |
| 发布 | `collectors.publish_catalog` 发布前重放条款证明；失效/未解析条款不得进入“已核验资格”，SQLite 增加 `program_qualification` 表（program_id、clause_ids、complete、requires_review、version） |
| 读取/API | 方向详情返回三态：明确匹配/明确不符/未知，并返回条款原句、链接、页码；不得把“规则可核验”写成“个人符合” |
| 公开包 | `tools/build_public_bundle.py` 的 `NAMES` 增加该文件，写入 manifest 与逐文件 SHA；`prepare_data.py` 恢复时校验 |
| 全新恢复 | 在独立同版本代码副本恢复新包后重放资格 adoption，确认规则、原文依赖闭包、revision 与 SQLite 查询一致 |
| 反例测试 | 匹配/排除/未知三态；跨年、错校、错专业、未读例外、健康条款未解析、同等学力限制、工作年限、新公告覆盖旧版；失效后停止采用 |

在节点3 真正实现前，本方案只是设计，不产生任何资格增长；节点0 未写 `admissions.json`、未改任何 `eligibility_evidence_complete` 或 `requires_review`。

## F. 本节点执行的命令与测试

```powershell
# 已实际执行
git worktree add -b data/national-b001 D:/yx-data-b001 origin/main
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt
./.venv/Scripts/python.exe tools/prepare_data.py --target-dir D:/yx-data-b001
./.venv/Scripts/python.exe -m unittest tests.test_national_data_baseline -v
./.venv/Scripts/python.exe -X utf8 national_data_baseline.py --data-dir data --out data/batch_runs/b001 --repo-root . --raw-pdf "C:/Program Files/Git/mingw64/bin/pdftotext.exe" --bbox-pdf "E:/miniConda/envs/yanxu-pdf/Library/bin/pdftotext.exe"
```

- 新逻辑先 RED（`ModuleNotFoundError: national_data_baseline`），实现后 GREEN：11 项通过（含“有网址但未核准入口”反例）。
- 本节点未运行全库测试（只改新增模块），未改五个保护文件、未改旧金标/阈值。
- 产物：`data/batch_runs/b001/{baseline.json,queue.json,batch.json}`（本机工作产物）；
  可提交副本在 `docs/work_nodes/national_data_b001/`。

## G. 失败、边界与下一步

- 未联网采集、未解析任何新原件、未采用任何方向、未发布 SQLite、未构建新包；本报告不得被当作目录或资格增长。
- 首批 10 校的“来源数 / 原文 URL”来自公开包覆盖文件；节点1 必须以实际原件 URL、MIME、SHA、年份重新核对，URL 数不等于原件数。
- `original_pending` 与 `needs_binding` 的单位不在首批扩目录池；其中缺官网归属的，按手册交 Codex 结构化阻塞，不自行扩绑定。本次明确归为 `missing_trusted_entry` 的是 **河北建筑工程学院（688908263 / 10084）** 与 **复旦大学（368127 / 10246）**：两者覆盖文件仍写“尚未核准该单位的官方招生网站入口”，即使字符串里有网址也不能当作已核准入口。
- 本机未安装/未启动向量模型；本任务不需要。
- 下一步（节点1）：对首批 10 校建立原件清单与目录全集对账；每校保留 `document_id / URL / 标题 / 年份 / SHA / 路径 / 状态`，失败的按互斥主因分类。做完一批再报，不静默长跑。

## H. 发给 Codex 的节点简报

```text
批次/节点/分支/base：b001 / 节点0 / data/national-b001 / 67d5e24
实际处理：0/10 采集；队列 931/931 已建立；首批 10 校已入队（7 扩目录 + 3 补字段）
基线：135/931 目录、22,356 方向、完整资格 0；原始学费 650（378 年费/272 全程），
      既有规则后 912（640/272）；学制 1,680→1,942；可算全程 907；包 SHA 637f1990…
测试：新增模块 10 项通过（先 RED 后 GREEN）；未跑全库；五个保护文件 SHA 见 protected-sha256.json
产物：docs/work_nodes/national_data_b001/{baseline.json,queue.json,batch.json,protected-sha256.json,NODE0_REPORT.md}
未解项：尚未采集/解析/采用/发布；首批来源需节点1 实际核对；“missing_trusted_entry”单位交 Codex
下一节点：节点1 首批 10 校原件与目录全集清单
```
