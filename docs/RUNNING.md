# 研序 · 本地开发与数据核验

在本项目目录运行，使用 Python 3.11 及以上版本。前端使用原生浏览器模块，无需构建。首次建立查询版本还需要原文解析工具；不能只启动服务并等待它自动采用资料 JSON。

### N3 知识精读与维护

进入“研究知识库”，打开一份已通过准入的官方原文，可在文件内查找关键词、定位片段，再补读章节、表头和相邻条款。研究报告中带有当前有效片段的来源按钮可直接打开对应位置。真实 PDF 页号链接到官方文件；HTML 使用章节和字符位置。

已有旧摘要不会自动变成完整原文。若显示“尚未建立本地索引”，在“维护与版本”展开后显式重建：只读取 SHA 仍一致的本机原件，不联网、不调用模型。原件无法解析或年份不明时保留真实待核状态；失败任务可查看并重试。扫描 PDF 保持待 OCR，旧 DOC 保持不支持；DOCX/XLSX 的未解析结构或隐藏内容显示提取不完整，XLS 需要已安装的 `xlrd`。

撤信、恢复和版本采用均须填写理由并确认。同一来源、学校、实际年度有不同文本时先保留冲突，核对官方原文后再采用；不能用“恢复”按钮跳过来源与质量检查。历史报告会复核来源，但持久审计记录保留。知识库当前仍是个人本机 SQLite，向量库与 Embedding 尚未接入。

离线真实问题评测使用临时数据库，见 [知识库评测](KNOWLEDGE_EVALUATION.md)。需要先恢复公开数据包并配置原文提取器；不要求研究连接的 Key。

首次克隆请使用有写权限的较短目录，例如 `C:\src\yanxu-ai` 或 `D:\src\yanxu-ai`。Windows 原文工具在过长的完整 PDF 路径下可能无法打开文件；不要在已有项目的 `data/runtime/...` 下层层嵌套新的检出目录。若验证副本需要临时目录，可使用系统临时目录下的一个短子目录。

先离线恢复公共数据包，再安装 Python 依赖：

```powershell
python tools/prepare_data.py
python -m pip install -r requirements.txt
```

恢复工具只使用标准库，不联网、不启动模型、不读取 Key；已有不同资料会拒绝覆盖。分发内容和逐文件 SHA 见[data/README.md](../data/README.md)及`data/bundles/manifest.json`。大于32MiB的资料文件由 `ijson` 分块读取，避免同时驻留巨大的原始字符串。

安装版本以`requirements.txt`为准，其中PDF解析依赖使用已修复安全问题的`pdfplumber==0.11.10`，见[官方变更记录](https://github.com/jsvine/pdfplumber/blob/stable/CHANGELOG.md)。不要从历史报错字符串复制旧版本；适配器源码与语义审核通过SHA绑定，改动提示文字也会撤销相应审核，须按原文重放规则重新验收，不能只替换审核摘要。

### 分开配置原文与坐标工具

这份公共资料包的原文逐字引文使用 **Xpdf `pdftotext 4.00`** 的 `-raw -enc UTF-8` 输出；PDF 坐标表格另用 **Poppler 26.07.0** 的 `-bbox-layout`，并需要同目录的 `pdftocairo`。两条调用链独立：原文提取由 PATH 上的 `pdftotext` 决定，坐标解析由 `GRAD_PDFTOTEXT` 指定。不要把现代 Poppler 目录加到 PATH 而替换已留档引文的原文引擎。

当前 Windows 验收环境的 Xpdf 来自 Git for Windows 的 `mingw64/bin`。安装目录不同时替换实际路径；下面只影响当前 PowerShell：

```powershell
$rawPdfTool = 'C:\Program Files\Git\mingw64\bin\pdftotext.exe'
$bboxPdfTool = '<Poppler 26.07.0 所在目录>\pdftotext.exe'
if (-not (Test-Path -LiteralPath $rawPdfTool)) { throw '请先确认原文提取器路径。' }
if (-not (Test-Path -LiteralPath $bboxPdfTool)) { throw '请先填写坐标提取器的实际路径。' }
$bboxCairo = Join-Path (Split-Path -Path $bboxPdfTool -Parent) 'pdftocairo.exe'
if (-not (Test-Path -LiteralPath $bboxCairo)) { throw '坐标工具同目录缺少 pdftocairo.exe。' }
$env:PATH = (Split-Path -Path $rawPdfTool -Parent) + ';' + $env:PATH
$env:GRAD_PDFTOTEXT = $bboxPdfTool
(Get-Command pdftotext).Source
pdftotext -v
& $env:GRAD_PDFTOTEXT -v
```

应分别确认原文引擎为 `4.00`、坐标引擎为 `26.07.0`；仅有某个同名可执行文件不表示版本兼容。本机历史坐标工具位于 `tools/poppler-26.07.0/`，该二进制目录与 `.runtime-deps/` 均不提交仓库。用户需自行准备对应工具，`GRAD_PDFTOTEXT` 应填写实际完整路径。

Git for Windows 的安装版本与目录可能不同，不假定每次安装都附带相同的原文提取器；以实际路径与 `-v` 输出为准。

#### 现代坐标工具的来源与安装示例

本机完整坐标工具目录记录的 Windows 构建信息如下。来源是可公开访问的 conda-forge `win-64` 包，包归档与现有 `pdftotext.exe` 分别使用独立摘要核对；仓库不提交这套二进制或完整 DLL 清单。

| 项目 | 本机验收记录 |
| --- | --- |
| 包来源 | [poppler-26.07.0-h6618ce5_3.conda](https://conda.anaconda.org/conda-forge/win-64/poppler-26.07.0-h6618ce5_3.conda) |
| 精确包约束 | `poppler=26.07.0=h6618ce5_3` |
| 包归档 SHA-256 | `3fdbb4caf50ecae4fa447cb7a7d6d5b521a39ae8d4de4f6565e0ce9cd6d7f903` |
| `pdftotext.exe` SHA-256 | `1d1007cd28967c616282a6b29c2daac7902d68ba4563f8c57fcc8e153bb0466f` |

该包需要其运行时依赖。不要仅从归档中取出一个 EXE；缺少相邻 DLL 或 `pdftocairo.exe` 时，版本命令可用也不表示坐标表格可解析。若已安装 Conda 或 Mamba，可在独立工具环境中让包管理器解析完整依赖，例如：

```powershell
# 安装示例；本次没有执行此环境创建命令。
conda create --name yanxu-pdf --channel conda-forge --strict-channel-priority "poppler=26.07.0=h6618ce5_3"
# 也可将 conda 换为已有的 mamba。
conda run --name yanxu-pdf pdftotext -v
```

不要为了调用坐标工具而在应用当前终端激活这个工具环境，否则其 `Library\bin` 可能替换 PATH 上的原文引擎。将 `GRAD_PDFTOTEXT` 单独指向该环境的实际 EXE，并检查相邻工具：

```powershell
$env:GRAD_PDFTOTEXT = '<Conda 或 Mamba 环境根目录>\envs\yanxu-pdf\Library\bin\pdftotext.exe'
& $env:GRAD_PDFTOTEXT -v
Test-Path -LiteralPath (Join-Path (Split-Path -Path $env:GRAD_PDFTOTEXT -Parent) 'pdftocairo.exe')
Get-FileHash -Algorithm SHA256 -LiteralPath $env:GRAD_PDFTOTEXT
```

环境位置由安装方式决定，不能照搬占位路径。本次实际使用并核验的是已有的完整 `tools/poppler-26.07.0/` 目录，未宣称上面的安装示例已执行或跨机器发布已通过。上述 EXE 摘要对应表中的特定 Windows 构建，不能用于判定其他版本、其他构建或其他平台的文件。

恢复后可先用包内一份 PDF 检查工具能否打开当前完整路径，再做整库发布：

```powershell
$pdfProbe = Join-Path (Get-Location).Path 'data\snapshots\catalogs\final-trusted\3274440551-2027\eb5cba2fa029d08b-81c93e3d41114a17.pdf'
& $rawPdfTool -raw -enc UTF-8 $pdfProbe - > $null
if ($LASTEXITCODE -ne 0) { throw 'PDF 读取失败：请检查工具，并将项目移到更短的检出路径后重试。' }
```

如果源码适配器、原文、工具版本或提取文本导致重放不一致，应保留资料排查，不能改写留档文本或审核摘要让其通过。Linux/macOS 的双工具配置及同份固定资料的完整重放需要另行验证；不能把安装任意版本 `poppler-utils` 当作已经复现本次 Windows 环境。

先发布本机查询版本，再启动：

```powershell
python -X utf8 -m collectors.publish_catalog --data-dir data
python -X utf8 server.py --port 4180
```

访问 http://127.0.0.1:4180/ 。本机默认只监听127.0.0.1；本轮提供本地研究版本，未进行公开部署、账户鉴权与多人隔离改造。

发布命令离线重放原文、年度名单及 PDF 语义批准，事务写入 `data/runtime/catalog-index.sqlite`；失败保留上次索引。`data/runtime/` 被 Git 忽略，迁移后须重建。直接修改 `admissions.json` 不会自动发布到轻量查询接口。响应中的 `published_snapshot` 表示上次明确发布的核验快照，不表示每次查询都重新联网核验全国来源；版本变化时旧分页要求刷新。

字段依据抽屉只现场核对所选方向相关原文，不把结果扩称整校重新核验。来源与方向的关联由发布时已核验的字段引文生成，不能仅凭同校同年把全部专业挂靠到一份材料，也不改写原文摘要来添加关联。

## Agent 与模型设置

首页为研究入口，也可打开 `#/research`；院校资料、我的方案、来源核验继续可用。模型与元宝配置位于 `#/settings`，互相独立。这里的16个配置入口包含14个厂商/服务入口、本地Ollama与自定义OpenAI兼容接口，不表示已经验证市面所有厂商或所有模型。

1. 在「AI模型」选择服务商，填写API Key；已有保存配置时留空可复用当前厂商的Key，切换厂商需要相应的新Key。
2. 点击「读取模型」并选择模型，或按厂商要求填写已开通的模型ID。只返回当前接口本次提供的目录，不承诺列出所有可用模型。
3. 点击「测试并保存」。后台先真实发起最小生成，再以有效测试收据保存该配置；生成测试可能消耗所选服务额度。读取模型成功、地址可达、Key格式正确均不等于生成验证成功。
4. 在「元宝搜索」选择接入方式。腾讯云标准方式填写SecretId和SecretKey；服务API Key方式填写元宝服务Key，再测试保存。此测试会执行一个固定公开查询，不测试或修改AI模型配置。
5. 返回研究页输入需求。未配置或模型未验证时，需求仍保存为待配置任务；完成配置后可继续，不生成模拟回答。

公开状态为 `not_configured`、`not_tested` 或 `connected`。`connected`表示当前厂商、地址、模型与Key的组合完成过生成测试，元宝则完成过检索测试；它不表示所有模型已授权，也不代表模型工具调用已经端到端验收。接口允许保存未验证配置，但其状态为 `not_tested`，研究运行时拒绝使用。测试收据有效期10分钟，修改上述任一项不能沿用旧收据；只有测试且保存成功，才影响后续研究。

目前预设为DeepSeek、OpenAI、Anthropic Claude、Google Gemini、百炼通义、Moonshot Kimi、智谱GLM、火山方舟、硅基流动、腾讯混元、腾讯TokenHub、OpenRouter、MiniMax、xAI、Ollama及自定义兼容接口。需要注意：

- 百炼保留旧兼容默认地址；新工作空间可能必须在高级设置填控制台提供的专属兼容地址。国际Key需匹配其地区地址，不能从Key推断工作空间。
- 火山方舟需要已开通模型ID或接入点ID；智谱、旧混元和MiniMax在本版本走手填模型ID，不伪造模型列表。
- TokenHub与旧混元是不同入口；地区、Key和已开通模型要对应。Moonshot、硅基流动、MiniMax等国际地址也需与账号匹配。
- OpenRouter等模型目录不等于当前账号全部可调用；目录可能分页，`has_more`为真时当前列表并非全集，仍可填写具体模型ID后测试。
- Ollama只接受固定本机 `http://127.0.0.1:11434/v1`，读取已安装模型，不要求Key；自定义接口仅接受公共HTTPS地址，不提供任意内网代理入口。

Key只通过本项目同源POST正文传到后台，不回显、不进入URL、浏览器localStorage或研究对话。Windows使用当前用户的DPAPI加密，保存于 `data/runtime/settings.sqlite`；该文件不能当作跨账号/跨机器的可移植凭据备份。非Windows当前明确使用会话模式，配置不持久落盘，重启后重新填写。运行时目录被Git忽略；程序不读取Claude、权益雷达或NoticeAction的凭据。

研究记录和事件保存在 `data/runtime/research.sqlite`。每轮单worker、有界排队，默认最多8轮模型调用、4次搜索、4份原文、20次工具调用、12个候选、180秒及40,000个累计模型用量token（依据厂商返回usage统计）；这是停止策略，不是费用上限或实际耗时承诺。取消阻止后续调用并保留成果；进程重启将旧运行/排队任务标为 `interrupted`，不会自动恢复付费调用。条件识别通过 `profile_proposal`给出待确认项，用户确认后才参与新一轮计算。

研究工具只能读取既有单位/候选、元宝来源、受控官网原文、规则索引，并调用现有算法。新发现页面仅作为检索线索或已读原文，不能直接写入正式招生库或当作已审校字段参与比较。研究报告保留本轮范围、实际工具结果与缺口；任意模型自由文本不充当招生事实，有限候选不代表全国全集。

研究页已改为控制区与结果区：输入、关键条件、当前动作优先，候选按学校分组，初始最多三所学校；来源每页八条，完整过程按轮次折叠。运行中可编辑草稿，“停止并更新”先取消、等待`can_continue`再单次提交。提交后取消入口不再假装能够撤销服务端已接受的任务；离页停止待提交意图，不自动续跑付费任务。旧任务轮次为0，保留历史结果，不在刷新时触发新研究。

后台轮询或连接状态返回时，正在编辑的原生输入节点留在原位，保留中文组合输入、焦点和选区；周围进度照常更新。组词期间不执行快捷提交。研究标题显示本次实际工具协同状态，固定范围警告已移除；全国官方来源检索的能力、实际发现的材料与核验范围分别说明，来源入口保留核验记录。

得到候选后程序自动运行匹配算法；只有完整备考科目才自动计算路线，未知不当作零换科。宽泛请求先分散学校、再列方向，扫描仍有限。缺少至少两项关键条件且已经取得候选及一次检索后，先暂停请用户补充，避免继续堆无必要搜索。相对年份先澄清；用户显式更新的字段写入`confirmed_fields`，默认未知值不当作确认。

## 研究知识库

`#/knowledge`是独立来源知识库，从研究页进入。`data/runtime/knowledge.sqlite`保存来源、版本、引用与全文索引，原文blob保留在`data/runtime/research-sources/`；都不提交仓库。`search_knowledge`工具读取本机留档，`read_knowledge`继续提供静态规则。动态知识库不替代`admissions.json`或已发布目录，不导入学生档案/私人对话/凭据。

搜索线索、已读取原文、已发布资料三态分别保存，来源真实年份与请求年份分开；原文SHA和归属经检查后才能标读取成功。另设质量准入：accepted可复用、pending待核、excluded拦截。默认页面和`search_knowledge`只用accepted；搜索摘要、未知年度及只有附件入口的发布页待核，门禁/导航/噪声/名单等拦截。取得原文不等于通过准入，通过准入也不等于字段已核验。

复用保留留档时间，不计作本次新联网读取。原文丢失或篡改撤销可引用正文与复用资格；新拦截内容不进入知识库，旧拦截记录保留来源、版本和审计但不展示或索引正文。历史研究通常只有摘录，不能标成完整正文。写库失败在报告保留缺口，按规则过滤则展示拦截原因，不冒充保存失败。

已有来源可离线导入，不构造研究运行时、不调用模型或搜索：

```powershell
python -X utf8 -c "from pathlib import Path; from knowledge_store import KnowledgeStore; d=Path('data'); print(KnowledgeStore(d).import_reports(d/'runtime/research.sqlite'))"
```

该操作只读原研究库，幂等保存`report.evidence`与研究ID引用，不复制query/profile。具体状态和数量以知识库当前页面为准；留档不是字段已核验或全国目录已齐。

旧知识库没有质量记录时一律待核。升级时先备份`data/runtime/knowledge.sqlite`，再显式执行`KnowledgeStore(Path('data')).reassess()`，仅使用本地留档、单位登记和规则重新分类；不联网、不调用模型。原研究库、正式目录与连接配置不参与重写。新增保存与重分类不会直接相信来源传入的quality或“官方已验证”标志。

本次代码支持这些接入路径，真实Key验证必须以实际成功请求为准。离线单元测试、HTTP夹具或浏览器测试通过不能证明16个入口全部真实可用；没有有效Key时不能宣称元宝研究闭环或厂商连接已完成。

## 新增本地API

所有接口继续使用 `{ok,data,error}` 信封。配置操作只接受JSON POST正文，不接受Key或配置写入参数出现在查询串中；同源、Host、大小与限频检查仍生效。

| 接口 | 实际用途 |
| --- | --- |
| `GET /api/settings/providers` | 返回 `{providers:[...]}`，字段以 `provider_catalog.py` 为准 |
| `GET /api/knowledge?q=&status=&year=&unit_id=&page=1&page_size=12&quality=accepted` | 默认仅可复用资料；quality另可pending/excluded/all，支持空年份，不触发网络或全库目录重验 |
| `GET /api/knowledge?entry_id=…` | 来源详情、摘录、留档与版本信息；与列表筛选参数互斥 |
| `GET /api/settings` | 返回 `ai`、`yuanbao`、`storage`、`providers`，不含Key |
| `POST /api/settings/models` | 使用临时或当前保存的Key读取模型，不自动保存、不验证生成 |
| `POST /api/settings/test` | 真生成/真检索测试，成功返回 `validation_token` |
| `POST /api/settings/save` | 保存配置；只有与有效收据匹配才标 `connected` |
| `POST /api/settings/delete` | 正文仅 `{kind:"ai"}` 或 `{kind:"yuanbao"}`，清除选定配置 |
| `POST /api/research` | `{query,profile?,unit_ids?}`，返回202与已保存任务；无需已入库 `program_id` |
| `GET /api/research?limit=20` | 返回任务列表 `{tasks,total}`，limit为1至100 |
| `GET /api/research?research_id=…&after_event=0` | 读取完整任务与增量事件，不调用模型；不能同时指定limit |
| `POST /api/research/cancel` | `{research_id}`，返回取消后的任务 |
| `POST /api/research/continue` | `{research_id,message?,profile?,unit_ids?}`，返回202；继续时重新检查配置与预算 |

配置POST字段统一使用 `kind`、`provider`、`api_key`、`base_url`、`model`、`use_saved_key`、`validation_token`；元宝另支持 `auth_mode`、`secret_id`、`secret_key`。不使用 `provider_id`、`model_id`、`region`、`test_id` 或 `expected_revision`。研究事件每页最多200条，`last_event_id`是本页游标，`latest_event_id`是当前任务最新位置；`has_more_events=true`时需要继续读取。状态、报告及错误的完整字段见[已实现的Agent契约](AGENT_UPGRADE_PLAN.md)。旧 `/api/chat` 仍明确返回 `not_configured`，新Agent由研究任务API提供。

## 真实资料与边界

- `data/units.json`：研招网招生单位索引，保存逐页原始快照与校验值。
- `data/annual_registry_2027.json`：逐省、逐单位核准的2027年度名单，保存原始请求、响应与校验值；名单完整不等于专业目录完整。
- `data/source_inventory.json`：全国单位公开招生页面及资料线索，发现链接不等于已核验目录。
- `data/national_sources.json`：实际读取与下载状态、已保存原文、失败与尚未完成的队列；章程等资料不能当作完整专业目录。
- `data/national_catalog.json`：已保存原文的离线解析报告，解析候选须另行核验入库。
- `data/admissions.json`：按校方原文提取的参考年度专业、方向、考试单元及备选组合。
- `data/catalog_semantic_audits.json`：独立PDF表格语义批准，绑定原PDF、适配器代码与该来源完整记录集摘要。
- `data/history.json`：历史官方公示的匿名统计和规则，不向浏览器提供姓名、考号或原始逐人记录。
- `data/program_enrichment.json`：人工核对的字段补齐规则及独立官方证据。仅同校、同年、明确方向范围生效，原文校验失败时保留未知，不随目录重采被覆盖。
- `data/snapshots/`：采集留档，不在静态服务目录中。
- `data/runtime/`：本机雷达订阅、原文版本和补证候选记录，不提交仓库。

2028正式目录未公布时，2027仅作历史参考。目录完整性与资格条件完整性单独检查；全国发布门槛未达标时，只能浏览当前收录范围和进行明确学校范围的研究试算。没有资料表示未收录或待核实，不表示该专业不存在。历史样本仅代表对应公示，不等于完整最终录取名单。

算法代码在 `grad_engine.py` 和 `planning_engine.py`：资格三态核验、按考试单元求最小换科、三种偏好排序、Pareto非支配筛选、备考路线、授权范围内的最小调整、分年度复试与调剂规则匹配。未知学费、科目或资格不按零成本或已符合处理，也不输出未经校准的录取概率。

从「我的方案」可进入路线规划、变化提醒和资料补证。雷达首次关注只建立来源基线，服务运行时每小时检查已关注来源；关闭服务后停止。手动检查结果仅代表本次实际成功读取的来源，不代表扫描了全部学校公告，也不发送邮件或短信。跨年度或年度无法确认的语义变化需要复核，不直接认定停招。

关键字段旁的「依据」打开原文抽屉；来源总览位于`#/sources`，可按学校、年度、类型与标题筛选。同一原始文件的多个链接归为同源；章程引用收费表属于适用性引用链。抽屉展示原文摘录、页码、官方链接、发布日及留档日，留档核对成功不表示刚刚联网访问成功。

历史页使用公示样本初试区间/中位数图，并将学校门类参考线与专业确定线分开。当前增加2024、2025、2026南开学术工学及专业工程共6条学校参考线，来源为3份官方PDF。学校线允许学院上调；目录学位类型未知时，仍标明适用关系待核，不自动当成本专业条件。图表仅绘制已核对或已重放的统计值，原文失效或数值不符会保留缺位与待核验说明。

前端首页改用 `GET /api/catalog-bootstrap` 的轻量单位、覆盖与版本信息；目录、科目和来源分别由 `/api/catalog-programs`、`/api/catalog-subjects`、`/api/catalog-sources` 服务端分页，单条详情为 `/api/catalog-program?program_id=&revision=`。目录列表每页6条，来源每页12条；分页不改变算法计算的明确学校范围。

`GET /api/collection-status?unit_id=&q=&status=&page=&page_size=` 返回实际采集进度、解析缺口与官方链接。`GET /api/evidence?program_id=&revision=` 返回字段原文。旧 `/api/bootstrap`、`/api/programs`、`/api/subjects`、`/api/sources` 保留兼容。所有公开响应移除本机路径和历史逐人记录。

旧 `/api/bootstrap` 仍会载入并重验全量资料，不能用作当前22,356条资料库的常规启动入口。显式全库发布需要数分钟；正式前端使用轻量已发布索引。采集进度对大文件仅投影读取metadata、unit_id和year，不把全部引文驻留内存。最后一次实际冷HTTP中轻量启动约140毫秒，采集进度含来源清单与逐源核验约11.5秒；这是本机单次测量，仍有继续改善空间。

## 全国资料的分层核验

2027年度独立硕士招生单位核准为931个，通用索引保留936条；其中5条未列入该年度独立单位名单，不据此推断停招。实际入库数量以 README 和已发布版本的覆盖信息为准。名单、下载原文、结构化方向和全校目录对账分别统计，全国专业完整性门槛仍关闭。

「院校专业」中的采集进度按学校展示年度身份、目录状态、原文读取状态、解析缺口和官方链接。原文可能是章程、部分学院资料或尚需适配的附件；没有目录表示尚未收录，不能解释为该校没有专业或考生不能报考。

`collectors/catalog_html.py`处理官方HTML表格，保留rowspan/colspan及备选科目；`catalog_pdf.py`处理已适配的行式PDF；`catalog_zsml.py`从官方院系树枚举并核对每个专业页；`catalog_enrichment.py`仅补同一原文明确的费用和学制。官方没有单列院系或方向时保留为空并注明层级，不编造学院、00方向或六位代码。

`data/national_import_manifest.json` 保存本轮新增静态目录来源，树形目录另有各自采集任务。仅解析已保存原文、不联网、不修改正式招生库：

```powershell
python -X utf8 -m collectors.national_catalog
```

针对清单离线生成预览：

```powershell
python -X utf8 -m collectors.catalog_batch --manifest data/national_import_manifest.json --previous data/admissions.json --output data/admissions.preview.json --offline
```

该命令不会自动替换正式库；同校partial刷新采用保守合并，保留旧版本并提示待复核，不能把预览结果当成新增数据已应用。南昌大学另通过`collect_zsml`保存的36院系原始树及246个专业页回放，663条方向逐项核对，638条明确学制。

PDF 坐标表格的解析成功不代表语义准确。`pdf_table_v1/v2` 与 `pdf_table_national_v1/v2` 须有独立审校，批准文件须与当前原文、适配器和完整记录集同时一致；批准撤销或任何一项变化都会阻断采用。已发现跨院系、跨页误读的候选隔离，不计入入库数量。原文中科目代码与名称矛盾或标识待核的记录保留原值和依据，排除换科与推荐计算；不悄悄改成猜测的代码。推免专用、单独考试、强军等记录不能混作普通统考方向。

研招网已触发频控并停止新请求，共享状态在 `data/runtime/chsi-host-gate.json`。不清除此状态来强行续采，不绕过登录、验证码或受限分页。新公共目录采集器默认禁用网络；已核验的单个真实详情不代表全国目录已采完。结构化详情保留原始字段、字段路径和真实详情链接，明确标注结构化原文，不将程序拼接说明冒充逐字引文。

来源原文、页面总数、专业身份、学习方式、完整考试单元和OR组合分别验收；搜索返回的页面数不作为全国覆盖率。是否已配置并通过真实调用，查看当前模型设置状态；不能把接口实现当作付费检索已运行。

## 元宝检索

公开资料浏览与既有算法筛选不需要用户Key。元宝检索优先读取本项目设置页保存的当前模式配置，没有保存配置时才回退本项目环境变量 `GRAD_SEARCH_API_KEY`；不读取权益雷达、NoticeAction或全局腾讯云凭据。清除设置页配置不会修改启动环境变量；环境Key存在时仍可作为API Key方式回退，但不会自动变成设置页中已测试的连接。

「腾讯云标准方式」使用`auth_mode=standard`、`secret_id`、`secret_key`，固定请求`https://wsa.tencentcloudapi.com/`；TC3-HMAC-SHA256签名由后台生成，Action为SearchPro、Version为2025-05-08，不需要Region。「服务API Key」使用`auth_mode=api_key`、`api_key`，固定请求`https://api.wsa.cloud.tencent.com/SearchPro`并使用Bearer鉴权。[官方接入区别](https://cloud.tencent.com/document/product/1806/130616)、[标准接口参数](https://cloud.tencent.com/document/api/1806/121811)。

旧请求与旧保存数据未写`auth_mode`时仍按API Key方式处理。SecretId与SecretKey均在服务端加密保存，不向前端回显；同模式已保存的两项可同时留空复用，新填任一项时须重新填写完整一对。切换模式、修改任一凭据会使旧测试结果失效。连接测试、Agent检索、院校资料补查与补证统一采用当前模式，切换仅在测试并保存后对后续请求生效。

当前标准方式接入长期云API密钥对，不接收STS临时Token。为兼容不同搜索套餐，检索只发送Query及按需Site，不强制附加极速版不支持的Mode/Cnt；返回仍按有界结果处理，不宣称完整召回。

```powershell
$env:GRAD_SEARCH_API_KEY = '<在此配置平台新Key，不写入文件或仓库>'
python -X utf8 server.py --port 4180
```

未配置时，界面明确显示不可用，资料浏览、缺口清单和本地规则计算仍可使用。检索用于发现官方来源，返回的TopK不作为完整招生目录。原有专业补证最多读取3份通过官方来源边界检查的原文；新Agent每轮原文预算为4份。两者均保留范围与来源，候选值不会自动写回正式招生资料。公共检索仅发送公开查询条件，不发送个人档案或Key；Key只用于服务端请求鉴权。研究模型会接收用户主动输入的问题、确认条件及本轮工具结果，所以应避免在研究输入中粘贴Key、身份号码或个人材料。

## 验证

```powershell
python -X utf8 -m unittest discover -s tests -v
python -X utf8 -m coverage run --branch --source=grad_engine,planning_engine,service,service_verification,server,snapshot_cache,radar,source_proof,public_sources,enrichment,evidence,collection_status,catalog_index,catalog_replay,published_catalog,provider_catalog,ai_transport,secret_store,settings_store,research_service,research_store,research_tools,knowledge_store,knowledge_quality,source_privacy,collectors -m unittest discover -s tests
python -X utf8 -m coverage report -m
node --test tests/*.test.mjs
python -X utf8 -m unittest tests.test_agent_http tests.test_provider_settings tests.test_research_service tests.test_research_store tests.test_research_tools
node --test tests/agent.test.mjs tests/settings.test.mjs
$env:FORMAL_BROWSER_CHANNEL = 'msedge'
node tests/browser-formal.cjs
node tests/browser-agent.cjs
node tests/browser-settings-integration.cjs
node tests/browser-model-picker.cjs
node tests/browser-yuanbao-auth.cjs
node tests/browser-workbench.cjs
node tests/browser-agent-ime.cjs
node tests/browser-knowledge.cjs
```

`coverage`与Playwright为测试依赖，不是应用运行时依赖。浏览器脚本优先读取已安装的`playwright`；可用`FORMAL_PLAYWRIGHT_PATH`指定模块路径，`FORMAL_BROWSER_PATH`指定可执行文件，或用`FORMAL_BROWSER_CHANNEL`选择本机Edge等浏览器。本开发机保留已有安装路径作为回退。迁移机器需准备对应浏览器环境，不能把未运行的浏览器测试视为通过。`FORMAL_FILTER`仅用于故障定位；最终验收须不设置该过滤器。

PDF重新解析依赖见文首。已有真实快照时，可离线重放原有两校专业目录解析：

```powershell
python -X utf8 -m collectors.catalogs --sources nankai,zzu --offline
python -X utf8 -m collectors.history --sources data/history_sources.json
```

历史命令会访问配置中的官方网页，任何不完整解析将保留已有有效库并报错。全国来源发现的刷新和断点续跑请先查看 `python -m collectors.discovery --help` 与 `python -m collectors.national_sources --help`，续用有效缓存并遵守频控。无登录绕过，不尝试验证码或受限接口。
