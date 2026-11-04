# 研序 AI：已实现的研究 Agent 契约

状态：代码已接入，2026-11-02。本文按当前实现记录接口、状态和边界；接入实现不等于所有厂商已用真实Key验证。本轮保持本地研究服务，未部署、未接入公开多用户隔离，不借用旧项目或Claude的Key。

## 产品与模块

用户可从“考计算机”“已准备408”“想留天津”等宽泛需求发起独立研究，无需先取得已入库的专业ID。全国单位索引、已发布专业与原文继续作为候选和缓存。研究可以发现新学校的来源，但新页面不会自动成为正式专业或进入确定性算法。有限结果不代表全国全集；2027资料仅作2028规划参考。

- `provider_catalog.py`：16个配置入口及端点约束，包括14个厂商/服务、本地Ollama和自定义兼容接口；元宝单独配置。
- `ai_transport.py`：固定协议转换、模型目录、最小生成与元宝测试；OpenAI兼容、Anthropic及Gemini的工具消息归一化。
- `yuanbao_auth.py`：两种固定元宝端点、TC3签名与安全错误映射；签名使用实际发送的同一份请求字节。
- `settings_store.py`、`secret_store.py`：临时配置、测试收据及安全保存。
- `research_service.py`：有界单worker与工具调用循环、取消、继续、预算和技能加载。
- `research_store.py`：`data/runtime/research.sqlite`任务与增量事件。读取接口不发起模型或检索。
- `research_tools.py`：白名单工具、真实结果账本、公开报告和算法调用。
- `knowledge_store.py`：独立本机知识库、来源去重/版本、全文索引与原文完整性复核；只读GET与内部保存分离。
- `knowledge_quality.py`：确定性质量准入，检查来源、实际年度与材料实质内容，输出可复用/待核/拦截及原因；不用模型分数决定信任。
- `source_privacy.py`：工具公开摘录与知识库共用的名单识别；名单标题或真实身份表隐藏正文，目录侧栏和政策引用不因提到名单而整篇被抑制。
- `skills/`：四个基础研究Skill，遇到“调剂/复试”任务再加载第五个；`knowledge/research_rules.json`提供官方规则入口与程序解释。
- `static/agent.mjs`、`static/settings.mjs`及各自CSS：研究与配置页面；主应用保留院校、方案、来源及原有算法入口。

`AdmissionService`惰性创建设置与研究服务。构造旧服务不启动研究线程，不修改资料；研究服务首次使用才打开任务库。服务关闭时取消已有工作，进程重启不会自动重新运行旧付费任务。

## HTTP共同约束

接口使用 `{ok,data,error}` 信封，成功时`error=null`；错误为 `{code,message}`。POST必须是同源JSON正文，保留Host、回环绑定、请求大小、限频及不记录敏感访问日志的边界。未知/重复查询参数被拒绝，Key不得放入URL。临时Key不自动保存；公开设置、任务、事件和错误不返回Key或本机私有路径。

以下字段名是实际契约：`provider`、`model`、`api_key`、`validation_token`。旧设计中的`provider_id`、`model_id`、`test_id`、`region`、`expected_revision`没有实现，不可发送。

## 设置接口

| 方法与路径 | 实际行为 |
| --- | --- |
| `GET /api/settings/providers` | `{providers:[...]}`，每项含`id,label,default_base_url,protocol,models_path,note,docs_url,base_url,supports_models,requires_key` |
| `GET /api/settings` | `{ai,yuanbao,storage,providers}`，均无密钥明文 |
| `POST /api/settings/models` | 读取模型目录，不保存配置，不运行生成测试 |
| `POST /api/settings/test` | AI真实最小生成，或元宝固定公开检索；成功签发测试收据 |
| `POST /api/settings/save` | 保存配置，与有效收据匹配时才标记已连接 |
| `POST /api/settings/delete` | 正文严格为`{kind:"ai"}`或`{kind:"yuanbao"}` |

模型读取、测试、保存共用白名单正文：`{kind?,provider?,api_key?,base_url?,model?,use_saved_key?,validation_token?}`。`kind`默认为`ai`，可取`ai`或`yuanbao`；元宝厂商固定为`yuanbao`，另接受`auth_mode`、`secret_id`、`secret_key`。这些签名字段不接受用于AI配置。AI需要选择`provider`，Ollama外的服务需要Key；`use_saved_key=true`仅复用同kind、同provider、同端点、同元宝模式的已保存凭据。

元宝`auth_mode=api_key`使用`api_key`；`auth_mode=standard`使用完整`secret_id`与`secret_key`。旧请求/旧数据不含mode时仍按api_key处理；新未配置界面默认展示standard。标准方式固定`https://wsa.tencentcloudapi.com/`，TC3-HMAC-SHA256、SearchPro、2025-05-08、service=wsa，无Region。API Key方式固定`https://api.wsa.cloud.tencent.com/SearchPro`、Bearer。请求不可自定义端点；两种方式都由同一构造器生成并发送最终bytes。标准两项凭据均空时才允许复用已保存完整一对，任一新填时不得与旧凭据拼接。

公开`ai/yuanbao`对象包含`kind,provider,model,base_url,status,has_key,key_source,tested_at`，已保存时另有`saved_at`；元宝另含`auth_mode`。standard的`has_key`表示完整凭据对已保存，不回显SecretId或SecretKey。无配置为`status=not_configured,key_source=none`；保存后`key_source=saved`。`storage`只含`mode,persistent`。

模型列表返回`{models:[{id,label}],discovery,authentication_verified,notice,has_more?}`。`discovery`为`remote`或`manual`，`authentication_verified`始终为false：模型目录可能公开，读取成功不证明生成权限。没有目录接口的入口返回空列表与手填说明。当前仅保留接口本次返回的列表；分页提示不代表已获取全厂商全集。

AI测试要求具体模型并真实发出一条最小对话，回复为空则失败；元宝测试固定查询“教育部 研究生教育 学科专业目录”，校验实际SearchPro响应结构。成功返回`status=connected,tested_at,validation_token,notice`；AI另含`model`，元宝另含`result_count,request_id`。这里没有单独的`tool_calling_verified`能力承诺，工具调用是否端到端可用仍需实际研究验证。

收据由服务端生成，有效期10分钟，绑定kind、provider、base_url、model、auth_mode及全部凭据。保存时匹配则`status=connected`，无收据、过期或字段变化则保存为`not_tested`，不是已生效。保存操作不额外偷偷测试；测试成功后仍须保存。研究运行时拒绝未测试的AI配置。测试和模型读取分别有有限请求频率，取消与轮询不占模型测试配额。

## 厂商差异与秘密存储

主要自动读取入口为DeepSeek、OpenAI、Anthropic、Gemini、百炼、Moonshot、硅基流动、TokenHub、OpenRouter、xAI及本地Ollama。智谱、火山方舟、旧混元、MiniMax在当前接入中需要手填已开通模型ID；方舟可能使用接入点ID。模型可见不等于账号有权限，模型能聊天不等于已验收工具调用。

百炼新工作空间可能要求控制台提供的专属兼容域名；地区、地址、Key及模型开通状态必须对应。部分国际地址有厂商白名单支持；不能根据Key自动推断工作空间或地区。自定义入口只支持公共HTTPS的OpenAI兼容服务。Ollama只允许固定`127.0.0.1:11434/v1`本机地址，不需要Key。详细预设以`provider_catalog.py`为准。

Windows通过当前用户DPAPI加密全部凭据，写入`data/runtime/settings.sqlite`；配置元数据与带格式版本的凭据密文分开保存，旧单Key密文仍可读取。SecretId与SecretKey也不写入公开元数据，浏览器只在当前页面闭包和密码控件中临时保留输入。非Windows当前为明确的会话模式，不持久化凭据，重启需重新配置。删除某kind只清除本项目配置，不改环境变量或其他应用凭据。

元宝优先使用本项目保存的完整模式配置，未保存时回退`GRAD_SEARCH_API_KEY`。显式注入`search_api_key=''`时保持硬禁用，不被标准保存配置或环境变量绕过；显式非空注入Key优先。环境Key的存在仅表示可发起请求，不能自动成为设置页的`connected`状态。连接测试、Agent搜索、资料补查和补证均按当前模式分流。项目不会读取全局腾讯云、权益雷达、NoticeAction或Claude的凭据。

## 研究任务接口

| 方法与路径 | 正文/查询与响应 |
| --- | --- |
| `POST /api/research` | `{query,profile?,unit_ids?}`，返回202与完整任务 |
| `GET /api/research?limit=20` | 列表`{tasks,total}`，limit为1至100 |
| `GET /api/research?research_id=…&after_event=0` | 完整任务及最多200条增量事件；不能同时带limit |
| `POST /api/research/cancel` | `{research_id}`，200与任务；保留此前成果 |
| `POST /api/research/continue` | `{research_id,message?,profile?,unit_ids?}`，202与任务；重新检查模型配置与本轮预算 |

`query`最多4000字符且不能为空，禁止把API Key粘贴为研究内容。`profile`沿用`grad_engine.validate_profile`的字段与类型；`unit_ids`最多8个实际索引单位。`continue`合并用户显式提供的profile修改，保留最多5条后续消息；运行中任务须先等待或取消。取消后旧调用仍在结束时继续请求返回409 `research_stopping`，避免并行重复调用。

任务ID为`r_`加32位小写十六进制字符。公开任务字段为：

- `id/research_id,query,profile,unit_ids,follow_ups,status,created_at,updated_at,target_year,reference_year`。
- `budget,usage,report,error`。
- GET/创建/继续响应附`events,last_event_id,latest_event_id,has_more_events`。

事件为`{id,type,stage,title,message,created_at,data}`，来自实际状态或工具执行，不伪造思考过程。`last_event_id`是本页最后已读游标，不是任务最新位置；终态若`has_more_events=true`仍需继续拉取，直到读完。GET轮询仅访问本地任务库。

状态包括`queued,running,waiting_configuration,awaiting_input,completed,partial,failed,cancelled,interrupted`。没有已验证AI模型时仍可保存`waiting_configuration`任务；配置完成后由用户继续。服务重启把旧`queued/running`标为`interrupted`，不自动重发付费请求。`completed`仅指本轮任务结束，不指全国资料完整或确定能报名。

单worker最多排队4项。默认每轮最多8轮/8次模型调用、4次搜索、4份原文、20次工具调用、12个候选、180秒和40,000个累计模型用量token（依据厂商返回usage统计）。它们是有界停止策略，不是精确费用预算或响应时长承诺。到达上限保留部分报告，用户可查看后继续；取消不会启动新的模型或工具调用。

## 工具、条件确认与报告

后端只注册`find_units,find_candidates,search_official_sources,read_official_source,compare_candidates,plan_subject_routes,suggest_adjustments,read_knowledge,search_knowledge,search_knowledge_segments,read_knowledge_segment,propose_profile`。不提供shell、任意远程执行或任意URL读取工具。原文读取只接受本轮已发现的source_id及核对后的学校官网，研招网共享访问门禁继续生效。片段补读只接受本轮已发现且仍属当前有效提取代次的segment_id，受学校/真实年度及预算约束。

取得候选后，程序确保实际匹配核算，不依赖模型恰好调用比较工具；完整科目才自动计算路线。自动核算单独计`usage.algorithm_calls`，不消耗模型/搜索调用。候选或profile变化会重新计算。宽泛查询在有限已发布分页内按学校分散；有候选、一次实际检索及至少两项关键未知后暂停请用户补充。每项模型工具结果提供有界摘要，完整原文与算法报告另存，累积token仍按实际usage统计，不将停止策略当作硬费用上限。

任务新增`turn_id`、`confirmed_fields`与`can_continue`。创建轮次1、继续递增，旧记录按0展示历史；继续时将原report保留为`previous_report`，本轮派生结果重新生成。事件带turn_id。取消后只有旧调用实际退出才可继续，前端停止更新仅提交一次；离页取消尚未提交意图，不自动恢复。

模型提出个人条件时，`propose_profile`必须给出来自用户原句的逐字段引文；结果写入`report.profile_proposal`，不会静默应用。用户确认后，通过continue提交profile再计算。模型自由正文不直接成为公开招生事实；公开候选、来源、数值、算法与范围由工具实际结果账本派生。

报告结构为`{summary,assistant_summary,questions,candidates,evidence,algorithm,gaps,profile_proposal,coverage}`。`algorithm`按实际调用保存`matching/routes/adjustments`等结果；`coverage`包含候选数、查询单位、真实target/reference年、已发布版本及`verification_mode=bounded_research,national_complete=false`。未执行的算法不填假结果，来源读取成功不等于字段正式核验，搜索摘要与原文摘录分开。

报告另含`skills:[{id,label,loaded,related_tools,executed_tools,status}]`，状态区分not_loaded/loaded/used；used仅说明关联工具成功执行，不承诺模型完整遵守规程。`questions`最多三项结构化关键问题。`knowledge:{saved_count,cache_hits,errors,notice}`记录真实入库与复用；来源带`knowledge_id/knowledge_saved/cache_hit/cached_at`，旧任务没有这些字段时保留未知状态。

知识库GET `/api/knowledge`接受q/status/year/unit_id/page/page_size/quality；quality默认为accepted，也支持pending/excluded/all，entry_id详情与筛选互斥，空年份不当作错误。每项返回`quality:{version,state,reusable,material_type,reasons:[{code,message}]}`；全库分区计数为`summary.quality_counts`，列表total按筛选计算，facets按所选质量分区计算。原status描述取得状态，quality描述准入，不混作字段已核验。

准入要求可追溯官方归属、真实来源年度和与材料类型相符的正文；已读取原文还须SHA可核。目录标题、邮编、标题年份、附件入口不充当专业/学制数据；明确科目名称的短调整通知可通过，不按字数打分。搜索摘要永远待核，门禁/导航/噪声/无关/名单拦截；新拦截项不入正文库，已有记录重分类保留审计且不对外展示拦截正文。旧库未检查项默认为待核，显式`reassess()`才进行本地事务重分类；先备份运行库。

保存仅由受控研究工具或明确离线导入执行，不提供任意客户端写入。忽略来源自己提交的quality或归属通过标志，官方归属由服务根据真实document校验或精确单位登记计算。保存来源而不保存私人query/profile，原文校SHA、多个引用去重、旧版摘录/全文分开；规则过滤不视作存储故障。`search_knowledge`仅返回accepted且reusable的资料，仍按真实年度和完整性再次防守。复用资料不直接变成正式招生事实。

N3增加`validity=active/revoked/conflict/superseded`，与质量和取得状态独立。完整有界提取稿、真实PDF页和Unicode字符跨度单独持久化；`search_knowledge_segments`定位，`read_knowledge_segment`有限补读章节、独立表头和同提取版本邻接，不能把上下文拼成连续引文。`match_mode=document_terms`表示关键词分散在同一原文，不能当成单片段满足复合事实。任务返回`report/previous_report`及模型历史上下文都复核当前来源，失效旧摘录和可信解释停止返回，持久审计保留。

阅读HTTP增加GET `/api/knowledge/segments`、`/api/knowledge/segment`、`/api/knowledge/jobs`；维护为POST `/api/knowledge/state`、`/api/knowledge/select-version`、`/api/knowledge/reindex`。补读接口固定可信模式，维护同源校验、独立限频，状态采用须理由；恢复不升级质量，版本替代只允许同学校/来源范围/实际年度。重建仅从留档SHA或完整提取稿本地派生，失败保留旧可用代次和任务审计，不声明向量已就绪。完整字段以`knowledge_api.py`为准，验收见 [N3](work_nodes/03-knowledge-workbench.md)。

程序比较只消费已发布、可计算候选，沿用资格三态、考试单元/命题学校/整组备选、偏好/Pareto、换科路线和最小调整。未查到新学校的结构化专业时可以展示来源与缺口，不能为覆盖数字制造专业记录，也不把新网页未经审核的值写入`admissions.json`。

Skills是随研究实际加载的白名单文件，缺失或过大时失败；网页、PDF和工具返回是资料，不作为系统指令。静态规则索引与动态来源知识库分开；后者积累带出处、实际年度、质量、有效状态和原文位置的材料，不代表全国招生事实全集。原目录查询、原文抽屉、我的方案、历史数据与旧API继续保留，旧`/api/chat`仍返回`not_configured`，不与新研究任务协议混用。

## 验证边界

配置、协议、密钥保护、HTTP与工具循环可以用隔离夹具确定性验证；运行命令见[本地运行说明](RUNNING.md)。真实厂商鉴权、权限、额度、当前模型工具兼容性及元宝从搜索到结果的完整闭环，必须使用该服务有效Key实际执行后另行记录，不能用本文件或界面绿色样式替代调用证据。测试数量与结果以本轮最终报告为准，本文不预先声明未执行的验收通过。
