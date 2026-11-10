# Claude 全国目录与资格数据实施手册

更新：2026-11-09。负责人分工：**Claude 负责全国采用数据，Codex 负责官网发现实测、共性故障与最终验收**。先完成节点0，随后以10个单位为一个批次推进；每个节点提交成果和问题，不长时间只报“仍在采集”。本手册是执行合同，不是全国数据已完成的声明。

先读 [交接说明](HANDOFF_NEXT_AI.md)、[当前验收](VERIFICATION.md)、[公开包规则](../data/README.md)、[运行说明](RUNNING.md)、[原文到当轮候选](ON_DEMAND_CANDIDATES.md)、[官网与事实闭环](OFFICIAL_DISCOVERY_CLOSURE.md)。每批复制 [批次日志模板](work_nodes/NATIONAL_DATA_BATCH_LOG_TEMPLATE.md)，填写实际结果。

## 1. 当前基线与目标

以下为当前公开分发包，不是正在运行的个人SQLite。2027年度独立招生单位分母为931，通用索引936中的另5条不能混进分母。

| 指标 | 已复核基线 | 需要做的事 |
| --- | ---: | --- |
| 有目录记录的年度单位 | 135/931，14.5005% | 缺796个单位；目录有记录与整校完整分别统计 |
| 2027专业/方向 | 22,356，ID无重复 | 增加真实目录，不制造占位行或只导入首屏 |
| 完整资格 | 0/22,356 | 全部`conditions`空、完整标志false、`requires_review=true`；需要原文条件及例外审校 |
| 原始包学费字段 | 650/22,356，2.9075% | 年费378、明确全程272 |
| 当前代码处理后的有效学费 | 912/22,356，4.0794% | 年费640、明确全程272；既有7条补充规则新增262条，不是本次新成果 |
| 当前有效学制 | 1,942/22,356，8.6867% | 覆盖18个单位，不能据此推定其他专业学制 |
| 可算全程费用 | 907/22,356 | 635条年费×已核学制，272条明确全程；其余不能猜 |

912条学费、1,942条学制已在本次只读复核中通过现有原文`EvidenceIndex._claim`。其中262条来自既有白名单：郑州大学252、南开大学10。节点0必须同时统计原始包、补充处理后、实际发布后的字段；不能只取原始2.9%或把既有262条报作新增长。

当前包`catalog-data.zip`的SHA256为`637f1990c02435127cca3c7a308b08087e24db40d8aefba41290fad294936478`。数据审计原始产物在本机`D:/CodexRuntime/yanxu-live-audit-20261109-round2/data-audit/`，含`coverage-audit.json`和只读脚本。其他机器不依赖此绝对路径，须按本机包重新计算。若上游数据已改变，记录实际新基线和来源提交，不改数据凑上述数字。

目标是建立持续可回放的全国采用数据链，逐批减少缺口。**下载量、学校主页数、正文准入、当轮临时方向、正式采用方向、整校目录完整、资格规则完整是七个不同指标。**

## 2. 所有权、隔离和禁止项

Claude 的工作应在独立分支和短路径工作树中进行，例如`data/national-b001`、`D:/yx-data-b001`；不要复用Codex正在执行官网测试的`D:/CodexRuntime/yanxu-n4-publish`，不要在旧项目`data/runtime/`里嵌套克隆。

- Claude拥有新公开原件、批次清单、必要的新采集/解析/资格采用模块与测试、经审校的数据候选、批次报告；修改现有共享采用接口前说明调用链和兼容影响。
- Codex拥有官网发现算法及最终独立验收。不要修改`official_authority.py`、`official_search.py`、`public_navigation.py`、`public_sources.py`、`research_official_tools.py`、`unit_bindings.py`、`collectors/discovery.py`、`collectors/official_bindings.py`、官网实测工具和相关冻结构造来消除失败。尤其不能在数据分支改共享`collectors/discovery.py`的Host/域名规则或自行扩来源绑定掩盖官网失败。缺归属的单位交给Codex补证，本批继续处理其他单位。
- 不读取、复制、覆盖个人`data/runtime/`、API设置、Key、会话、订阅、用户知识库和真实发布库；仅可在本数据工作树或独立验收副本内创建新的临时`runtime/catalog-index.sqlite`。
- 不改旧金标、阈值、已有原件字节或已有审核摘要来使发布通过。`tests/fixtures/knowledge_gold/`与既有官方原件回放保持原样；新增案例放新批次目录。
- **五个已与审核绑定的文件原始字节不得改变：**`collectors/catalog_pdf_tables.py`、`catalog_pdf_variants.py`、`catalog_pdf_national.py`、`catalog_pdf_national_v2.py`、`catalog_hit_public.py`。连换行、提示文字、格式化都不能改。新格式需要适配时新增模块并经独立审校，不借重写五个文件撤销旧审核。
- 保存原始未知项和冲突，不全库设置`eligibility_evidence_complete=true`、`requires_review=false`、`catalog_complete=true`。只找到章程不证明资格完整。
- 分节点本地提交、向Codex报告SHA和产物；Codex验收后统一合并与发布。不要自己把未验数据推到主分支或替换现有演示服务。

第一次建立隔离环境时，先核对路径未被占用。若由Codex建立工作树，使用其工作树工具；若由本机Claude CLI在现有Git克隆中操作，可执行以下等价Git流程。路径存在时先检查已有工作树，不能删除目录重来。

```powershell
git fetch origin
git worktree add -b data/national-b001 D:/yx-data-b001 origin/main
Set-Location D:/yx-data-b001
$dataRepoRoot = (Get-Location).Path
python -m venv .venv
$dataPython = Join-Path $dataRepoRoot '.venv/Scripts/python.exe'
& $dataPython -m pip install -r requirements.txt
& $dataPython tools/prepare_data.py --target-dir $dataRepoRoot
$dataBatchRoot = Join-Path $dataRepoRoot 'data/batch_runs/b001'
New-Item -ItemType Directory -Path $dataBatchRoot -Force | Out-Null
```

`prepare_data.py`只离线恢复公开包，不创建发布索引；已有不同字节会拒绝覆盖。不要复制另一工作树的运行库。原文Xpdf 4.00与坐标Poppler 26.07.0需按`RUNNING.md`分别配置；不要把任意同名`pdftotext`当成兼容工具。基线记录两个`-v`输出及可执行文件路径；不安装或启动向量模型来做这项数据任务。

## 3. 已核对的命令和实际能力

本手册编写时逐个运行以下`--help`并读了调用代码。入参升级后先重新核对帮助；不要自造`--year`、`--root`、`--publish`等不存在选项。

| 真实入口 | 支持的主要入参 | 实际作用与限制 |
| --- | --- | --- |
| `python tools/prepare_data.py` | `--bundle-dir`、`--target-dir` | 离线校验恢复，不覆盖不同内容，不发布 |
| `python -m collectors.national_sources` | `--units`、`--inventory`、`--output`、`--snapshot-dir`、`--year`、`--workers`、`--max-pages`、`--max-depth`、`--refresh` | 公开原件采集；当前只支持2027。公开包不分发全量`source_inventory.json`，需先建立本批真实线索清单 |
| `python -m collectors.national_catalog` | `--root`、`--units`、`--sources`、`--output`、`--cache-dir`、可重复`--unit-id`、`--max-files`、`--refresh` | 离线解析已存原件，输出报告及每校`ready_entry_path`；**不采用进admissions、不发布** |
| `python -m collectors.catalog_batch` | 必须`--manifest`、`--output`；可选`--units`、`--previous`、`--snapshot-dir`、`--offline` | 按学校清单采集/重放、校验后增量采用；当前学校与增量导入只接受2027。`--previous`保留已有数据 |
| `python -m collectors.catalogs` | `--units`、`--output`、`--snapshot-dir`、`--sources`、`--refresh`、`--offline` | 现有专用`--sources`只有`nankai,zzu`，不能把它当全国采集器 |
| `python -m collectors.publish_catalog` | `--data-dir` | 显式重放来源、年度和PDF语义批准，在该目录创建`runtime/catalog-index.sqlite`；不联网，不证明全国完整 |
| `python tools/build_public_bundle.py` | `--project-root` | 从采用数据重建公开包和manifest；会写该工作树`data/bundles/`，不联网，不自动采用新资料 |

以下四个是库模块，**没有CLI**：`collectors.catalog_source_set`、`collectors.catalog_semantic_audit`、`collectors.catalog_enrichment`、`collectors.catalog_facts`。`python -m ... --help`虽然可能0退出，却不输出帮助，也没有执行审校；不能在日志写“审核命令通过”。实际可调用函数分别为：

- `source_set_reconciliation(unit, year, documents, verifier)`：真实目录全集对账。
- `explain_semantic_audit(document, parsed_programs, project_root, approved_audits=None)`：PDF语义批准校验；源SHA、适配器SHA和完整记录集SHA必须一致。
- `apply_inline_policies(programs, html, unit, year, document)`：现有有限费用/学制解析，不是通用资格采集器。
- `parse_catalog_facts(raw, unit, year, document)`与`audit_catalog_facts(raw, unit, year, document, facts, snapshot)`：缺关键身份的原表事实，不能直接变为可报名方向。

现有`program_enrichment.json`/`enrichment.apply_enrichment`只接受费用和学制白名单；`research_admission_tools.py`是**当轮任务事实**，不是全国永久资格导入CLI。持久资格采用需要在节点3显式实现、测试和审校，不能假装已有一键导入命令。

## 4. 节点0：环境、基线和可执行队列

只做本地准备和只读盘点，先提交报告，禁止一上来全量抓931校。

1. 记录Git提交、分支、工作树路径、Python/依赖/PDF工具版本；保存包manifest、包SHA和五个保护文件SHA。确认没有指向原项目运行目录的符号链接。
2. 用流式JSON读取统计原始单位/年度/目录/方向/资格/费用/学制，按当前`apply_enrichment`→`apply_evidence_safety`再统计有效口径。不要把值非空等同于原文核验。发布后还要再核一次实际SQLite/API，不能用源JSON计数替代。
3. 为931个年度单位建立工作队列，先保留原始ID、单位代码、省份和年度证据；排除另5条非该年度独立单位。逐项标明`缺目录/目录部分/缺完整资格/缺学费/缺学制/缺可信入口/原文待取/解析待适配/待独立审校`，一个单位可以有多个缺口。
4. 不手写臆测学校名单。按数据排序首批：有真实2027目录来源和可支持格式但未采用的单位；同时保留已收录135校的资格/费用补证子队列。每批10校可先按“约7校扩目录＋3校补已有数据”组织，按真实来源调整，避免长期只扩目录而不补22,356条资格。
5. 先设计资格持久化最小方案：来源/范围/完整条款/可执行AST/未知例外/版本/冲突和采用门禁如何流入发布。没有这条通路时，采集章程只能记“已取得资格材料”。

验收产物：`baseline.json`、`protected-sha256.json`、`queue.json`、首批10校`batch.json`、节点0报告。首批必须有真实单位ID、选入理由、缺口和下一步，不要求预先知道所有最终文章URL。基线与本手册不同时如实解释上游变化，不重写数据凑数字。

## 5. 节点1：10校原件与目录全集清单

机械工作可批量进行：已有来源复用、公开页面/附件下载、MIME与SHA检查、链接去重、年份提取、文件清单和断点记录。模型可以定位线索，不能授予新域名归属或用摘要补字段。官网证据不足时向Codex交一条结构化阻塞记录，不改官网模块；其他学校继续。

每份原件至少记录：`document_id`、`unit_id`、单位代码、发布者角色、请求URL/最终URL、实际标题、真实年度及年度引文、获取时间、MIME、字节数、SHA256、本工作树相对路径、发布父页及其SHA、状态。相同SHA不同URL不算独立佐证。CHSI账号、学院页面和学校主站分别注明；不能把公告页当附件正文。

每校先对账全校目录索引、学院、学硕/专硕、全日制/非全、分页、附件和方向组合。只取得计算机学院或首屏时保持`partial`，不能勾整校完成。推免专用目录、博士、港澳台、专项与普通统考范围分开。

可选全国原件采集命令（`scope-units.json`只能含本批10校；`inventory.json`需先从实际来源建立，结构为`units[]`，每项有`unit_id`及真实`candidates[]`）：

```powershell
& $dataPython -m collectors.national_sources --units "$dataBatchRoot/scope-units.json" --inventory "$dataBatchRoot/inventory.json" --output "$dataBatchRoot/national-sources.json" --snapshot-dir "$dataRepoRoot/data/snapshots/national-b001" --year 2027 --workers 2 --max-pages 80 --max-depth 6
```

这个CLI默认`HostPacer`间隔0.35秒，**没有`--interval`参数**。并行真实联网批次须使用库函数`collect_national(..., project_root=..., fetcher=HostPacer(interval=2), workers=2, ...)`建立可复现的本批脚本，或先以TDD新增明确限速配置；不能写不存在的参数。与Codex协调同一主机并发，优先复用公开原件、同主机至少2秒。受限站点不反复撞门禁或改DNS/代理绕守卫。

B001 已新增 `tools/collect_national_batch.py`：默认离线，只有显式 `--network` 才请求学校官网；批次清单须与当前HEAD已提交字节一致，最多2并发、同主机至少2秒。它复用已校验的HTML/二进制缓存，并阻止新增研招网请求（包括重定向）。首批可用以下入口，后续批次替换实际批次及输入路径；`max-pages`限制成功页数，不是HTTP尝试数，报告中的剩余frontier必须保留。

```powershell
& $dataPython tools/collect_national_batch.py --root $dataRepoRoot --batch "$dataRepoRoot/docs/work_nodes/national_data_b001/batch.json" --inventory "$dataBatchRoot/input-inventory.json" --output "$dataBatchRoot/national-sources.json" --snapshot-dir "$dataRepoRoot/data/snapshots/national-b001" --network --workers 2 --interval 2 --max-pages 24 --max-depth 4
```

2026/2025回退资料按其真实年份留档和展示；当前采集/增量采用链只支持2027，不接受把`year`强改为2027。历史扩展需单独增加并验证真实多年度通路，历史材料不进入本批2027覆盖增长。

验收：10校都有终态或明确阻塞；可用原件均SHA可重放；目录全集入口与已取附件有差集；失败分类型，不能只写“没找到”。不把“已处理10校”说成“10校目录完成”。

## 6. 节点2：专业、考试科目与范围的确定性采用

离线解析可调用：

```powershell
& $dataPython -m collectors.national_catalog --root $dataRepoRoot --units "$dataRepoRoot/data/units.json" --sources "$dataBatchRoot/national-sources.json" --output "$dataBatchRoot/national-catalog.json" --cache-dir "$dataRepoRoot/data/runtime/national_catalog_b001" --max-files 200
```

解析缓存必须位于当前隔离工作树新建的`data/runtime/`下；不要指向个人运行目录。实际批次建议逐校传`--unit-id`分别输出报告，避免总文件上限先被一个单位用完。这里的缓存不是正式采用库。

`ready_entry_path`只是待采用结果。它需要完整来源集合、真实原文行、身份/科目重放与根PDF语义批准，不能直接复制成已核准数据。解析器不支持的格式先留`parser_unsupported`；新适配器先写失败测试，再实现，原文对照并交独立审校。

正式批次manifest使用现有`schools[]`结构。以下仅是字段示例，所有占位内容必须换成真实数据，不能执行占位学校：

```json
{
  "schools": [{
    "unit_id": "原单位索引中的真实ID",
    "year": 2027,
    "sources": [{
      "url": "真实已核准官方原件URL",
      "title": "实际原文标题",
      "parser": "实际支持该格式的适配器名",
      "source_path": "本工作树中已取得原件的绝对路径",
      "sha256": "该原始字节的64位SHA256",
      "content_type": "实际MIME",
      "publication_page": {"url": "真实父页URL", "local_path": "data/snapshots/真实路径", "sha256": "真实SHA", "content_type": "实际MIME"}
    }]
  }]
}
```

从本批原件进行离线增量采用，输出候选文件，不直接覆盖已有`admissions.json`：

```powershell
& $dataPython -m collectors.catalog_batch --manifest "$dataBatchRoot/catalog-manifest.json" --units "$dataRepoRoot/data/units.json" --previous "$dataRepoRoot/data/admissions.json" --output "$dataBatchRoot/admissions-candidate.json" --snapshot-dir "$dataRepoRoot/data/snapshots/catalog-b001" --offline
```

此CLI使用模块所在工作树为`PROJECT_ROOT`，没有`--root`；必须在自己的完整代码工作树运行。需指定其他root时用`collect_manifest(..., project_root=...)`及`ingest_verified_batch(..., project_root=...)`库函数，不改全局常量。不要拿仅10校的`scope-units.json`替换正式936条登记库。

机械解析后逐字段核对：学校/年份/学院、专业代码名称、方向、学习方式、学位类型、考试单元和备选组、命题范围、统考/推免、计划口径。自命题相同代码跨校不等价；独立单元OR与成套组合不能压成笛卡尔积；同上/跨页/rowspan不得猜补；未知学位类型不得凭代码首位或名称硬填。

验收：稳定ID不重复；旧无关单位与年度不丢失；原文→采用行→考试单元可逐项回放；新增/修改/冲突/移除分别计数；整校完整须有来源全集对账，不由抓取结束推导。

## 7. 节点3：资格条款与持久化采用——不能只改布尔值

这部分需要语义审校，不能只靠爬虫。按“国家/学校通用条件→学院条件→专业/方向→学习方式/学位→专项/特殊考生→附加说明/更正公告”收齐适用条款及引用关系，保留完整例外、否定、OR与条件触发。只有章程学历一句话不能证明整组资格完整。

每个适用范围至少保留：原句和原文链接、页码或HTML章节/字符位置、来源SHA、真实年度、适用/排除专业清单、可执行条件AST、未解析条件与原因、审校版本、冲突及被替代关系。共用条款可按真实范围引用，不能把一校一院条款广播全国。**完整规则可核验也不等于某个学生符合**；个人信息未知仍返回`unknown`。

目前`SourceVerifier.program`主要重放目录身份与科目，不能把它返回true当成资格条款已核准。持久资格通路尚无现成CLI，须先做以下最小实现（新增模块/格式名称由节点0方案明确，以下不是已经存在的接口）：

1. 显式导入原文引用草案，解析并校验来源、年度、单位/学院/专业/学习方式/学位范围；所有采用值都能从规范原文和审校记录重新生成。
2. 保存`可执行已核条款`、`部分覆盖`、`未解析适用条款`、`缺原件/附件`、`冲突`和`失效`；不能丢掉不能计算的健康、政治、纪律、同等学力、跨考、专项、工作年限口径、境外学历认证或附加加试要求。
3. 仅在整组适用条款和例外都已审清、所有必要引用有效、无未解析/冲突、独立审校通过后，才允许采用完整标志。多数条款可计算而少数不能计算时，保留可用于排除的部分条件，完整资格仍false、`requires_review`仍true。
4. 全库发布前重新校验资格证明；撤信、SHA改变、范围变更或更正公告使旧证明停止采用，旧版本和审计保留。不能把过期的模型解释当采用输入。
5. 写有真实来源的匹配/排除/未知三态样例，含跨年、错校、错专业、未读例外、健康条款未解析、同等学力限制、工作年限及新公告覆盖旧版等反例。先RED→GREEN，再在每批真实学校上回放。

若引入新的永久资格文件，还须同时接入加载/采用、`snapshot_cache.py`依赖与revision、字段原文核验、SQLite发布/读取及公开包依赖闭包。当前`tools/build_public_bundle.py`的`NAMES`只列七份采用元数据，不能新增文件却忘了打包；现有`program_enrichment.json`又只支持费用和学制，不能未经实现就塞入资格字段。无论选择扩展既有格式还是新增格式，都要验证资格源改变后旧发布/缓存失效、全新恢复后资格规则与原文仍在。格式方案写进节点0并保留旧费用规则行为。

新`admission_facts.py`的逐字引用、范围和条件校验可复用，但现有工具依赖当轮真实已读凭证；不得伪造`read_receipts`来批量导入。不要把整个任务报告JSON当正式资格库。资格采用若改变PDF审校绑定的完整记录集，其`programs_sha256`也要重新审校，不能套用旧批准。

验收：每校有条款覆盖表；每类未解析要求都有原句、链接和真实原因；支持部分条件仍能排除明显不符的用户，但不会显示资格完全通过；无合法持久化/发布路径的条款只能算“已审读材料”，不能计为正式资格增长。完整资格增量可以为0，必须如实解释，不能为完成批次伪造通过。

## 8. 节点4：费用、学制与计划等字段

费用明确区分年费、全程、学年/学期、人民币或其他币种、上限/区间、奖助与应缴学费；年费×学制只在二者同校同年同专业同学习方式/学位适用时推导，保留双份依据，已有明确全程金额不覆盖。区间、另行公布或收费上限不能当确定实缴金额。

现有`program_enrichment.json`可添加真实逐项白名单规则，字段只支持`tuition`与`duration_years`，selector须包含学习方式及学位或专业/学院身份，目标ID明确。使用现有`apply_enrichment`和`EvidenceIndex._claim`复核；有值不等于verified，旧值冲突不能偷偷覆盖。当前固定规则仅支持年费，不能把全程学费写成`period=year`以通过；需要其他口径时先新增明确支持和反例测试。

招生总计划、推免数、统考计划、学院总额与方向名额分别存，不互相代填；历史学校线/专业线/录取统计也分开。本任务不为了美化图表采集或分发逐人录取名单。缺额不推导调剂概率。

验收：采用前后原始与有效费用/学制计数清楚，重复规则不双计；冲突、未知和范围不适用逐项保留，未核费用不当免费。

## 9. 节点5：确定性校验、独立语义审校

本批每个采用方向跑原件重放和字段范围校验；目录统计、分页/附件对账与原文支持的实际记录数一致。PDF表格尤其核对页眉/表头、合并单元格、单位、行列归属、脚注、扫描/OCR和跨页续行，不能只随机看一行就批准完整来源。

`data/catalog_semantic_audits.json`是根独立审校清单；采集/解析方不得自行写`status=approved`、伪造reviewer或把文件自带的批准字段传进`approved_audits`绕门禁。向Codex提供实际原PDF、渲染页/定位表、完整解析记录集、三个SHA（原件/适配器/完整记录集）及逐项复查结果，等待独立审校。未批准的PDF表格来源留候选，不强行采用。

相关已有回归可按修改范围选择：

```powershell
& $dataPython -X utf8 -m unittest tests.test_catalog_batch tests.test_catalog_batch_scope tests.test_national_catalog tests.test_catalog_source_set tests.test_catalog_semantic_audit -v
& $dataPython -X utf8 -m unittest tests.test_catalog_enrichment tests.test_enrichment tests.test_admission_facts tests.test_research_admission_tools -v
& $dataPython -X utf8 -m unittest tests.test_publish_catalog tests.test_published_service tests.test_data_bundle -v
```

实际新增的资格采用模块必须有自己的单元、整合及关键流程测试，核心修改含分支覆盖至少80%。不要改旧测试/金标来容纳错误字段。内存不足按模块新进程复跑并保留首次失败；跳过、环境失败和通过分开报告。

验收产物：逐校差异、来源与引文映射、测试及覆盖报告、独立审校结果/待审列表。程序重放通过与语义审校通过分别记录。

## 10. 节点6：临时发布、公开包恢复和Codex合并验收

审核过的候选只在本数据工作树内形成新的采用文件；先保留本批前文件SHA和不可变副本，再进行显式替换。保留历史、其他单位与未知状态。一次只发布冻结快照，发布期间不能继续写采集/采用文件。

```powershell
& $dataPython -X utf8 -m collectors.publish_catalog --data-dir "$dataRepoRoot/data"
```

这是本工作树的临时SQLite，禁止传旧应用目录。记录发布revision、`invalid_program_count`、`suspended_field_count`、`annual_total`、`verified_catalog_units`和flags；全局`catalog_partial`仍可真实存在，不能为消除此flag伪造全国完整。逐校核对本批有效数据和证明，新增无原文记录不得进入发布结果。

在临时库/API验证本批方向查询、科目OR和跨校自命题作用域、完整/部分/未知资格、年费与全程预算、原文跳转、冲突/撤信后失效。使用随机回环端口或临时服务，不重启用户4180，不读取连接设置，不启动付费Agent研究任务。仅创建SQLite或HTTP200不能代表业务链通过。

确认采用候选正确后，在数据工作树构建新的分发包：

```powershell
& $dataPython tools/build_public_bundle.py --project-root $dataRepoRoot
```

再建立全新的短路径验收副本，复制/检出同版本**代码**，用新包恢复；不能只建空数据目录，因为PDF审核还绑定代码字节：

```powershell
# $dataVerifyRoot 指已准备好的独立同版本代码副本，不能是用户原项目。
if (-not (Test-Path -LiteralPath (Join-Path $dataVerifyRoot 'collectors/catalog_pdf_tables.py'))) { throw '先准备同版本代码验收副本，再恢复数据。' }
& $dataPython tools/prepare_data.py --bundle-dir "$dataRepoRoot/data/bundles" --target-dir $dataVerifyRoot
& $dataPython -X utf8 -m collectors.publish_catalog --data-dir "$dataVerifyRoot/data"
```

复验逐文件SHA、原件依赖闭包、恢复幂等性、发布结果、关键查询与算法。`build_public_bundle`从原始采用元数据计数，并另带补充规则；继续同时报告原始/有效/发布三种口径。不能手改ZIP manifest的计数，不能只提交ZIP而不提供原件、审校及差异说明。

交给Codex：本节点本地commit、批次报告、结构化日志、采用文件差异、包manifest及SHA、测试/临时API证据、未解清单、五个保护文件SHA比对。Codex独立复核通过后，统一合并主分支并同步分发包；Claude此时继续准备下批来源，不自行覆盖演示站。另需修解析器时独立提交代码与测试，数据提交分开，便于定位与回滚。

## 11. 批次完成、失败分类与节奏

每批10个单位必须分别报告：尝试数、官方入口数、目标年原件数、目录有记录数、整校完整数、采用方向新增/修改/撤回数、资格条款审读/可执行/未解析/完整数、费用年费/全程/学制verified数、冲突数、发布数。所有增长带真实分子分母；同SHA不双算、同方向不双算、历史年度不算本年增长。当轮临时事实不算正式采用增长。

失败至少使用这些互斥主因（可带次因）：`entry_unverified`、`target_year_not_found`、`access_restricted`、`transport_failed`、`attachment_missing`、`response_incomplete`、`parser_unsupported`、`source_set_partial`、`identity_or_exam_unresolved`、`qualification_partial`、`qualification_unsupported`、`field_scope_conflict`、`original_changed`、`semantic_review_pending`、`publication_failed`、`bundle_recovery_failed`。每项保留URL、最后成功阶段、原始错误、已尝试办法和下一步，不用“学校没有官网/资料不存在”作为默认解释。

- 每个节点结束发送一次简报；真实长采集至少每10校或30分钟提供可量化进展，含产物路径、成功/失败/待核和下一步。一个单位连续受限/超预算就记阻塞，换其他单位；不无限重试同一入口。
- 一个批次可以“10校已处理、6校目录采用、4校待补”，不能写“10校完成”。没有完整资格增长时，仍要交具体条款覆盖与未解析原因；只有下载量增长不能标资格节点完成。
- 每次采用前做不可变快照、hash和本地commit；失败发布保留上一版本，不手工清空真实索引。新工作树回滚优先恢复本批前候选文件或通过`git revert`撤销本批自己的提交，不对共享目录`reset --hard`或递归删除，不撤回他人的变更。
- 其他分支推进时由Codex统一合并。重放前记录新base并重验本批SHA与程序行为，不能把旧快照测试结果套到新代码。

## 12. 可复制给下一位Claude的启动指令

> 你负责研序全国采用数据，Codex同时负责官网发现测试。请先读`docs/CLAUDE_NATIONAL_DATA_RUNBOOK.md`、`docs/HANDOFF_NEXT_AI.md`与`docs/work_nodes/NATIONAL_DATA_BATCH_LOG_TEMPLATE.md`，不要沿用历史“已全部完成”的说法。
>
> 先只执行节点0：核对当前Git和公开包，建立独立短路径分支/工作树及数据目录，验证PDF工具，流式重算原始/有效基线，固定五个保护适配器SHA，并生成首批10校的真实队列和资格持久化最小方案。随后按节点1—6推进，必要代码先写失败测试，机械采集与语义审校分开，每节点提交本地commit并报告数量、证据路径、失败与下一步。
>
> 基线是135/931目录、22,356完整资格仍待核；原始学费650、既有规则处理后912，不能把两种口径混写。2028是规划入学年，2027是参考资料；历史回退分年份。禁止修改官网模块、五个保护PDF适配器字节、旧金标/阈值、用户runtime/Key；不能给全库改完整标志。目录采用、资格采用、PDF根审校、临时SQLite发布、新包全新恢复必须分别验证。
>
> 遇到缺官网归属，交Codex结构化阻塞，继续其他单位；遇到资格例外或不支持条件，保留完整原句、原文链接及unknown。不要静默工作一整批甚至几天：节点0完成就发结果，后续每节点和长采集按本手册给进度。由Codex最终验收并合并主分支，你不直接覆盖演示服务或主分支。
