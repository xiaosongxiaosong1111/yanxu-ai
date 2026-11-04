# N4 · 向量、混合检索与可恢复索引

> 状态：本机Claude接管实施，尚未验收。用户明确要求“交给claude做，你只负责下命令和验收”。Codex及其代理仅负责下发任务、环境准备、测试/审查和验收，禁止继续写N4业务实现。此前隔离工作区的N4草稿已冻结并单独记录，交Claude审查取舍；未整合主项目。承接已通过的N1—N3，不启动N5—N7，不提交、推送或部署云端。

## 目标和边界

SQLite继续是来源、原文、精确片段、质量和当前结构代次的权威记录；Milvus只保存可重建的派生向量。新增独立Embedding配置和真实测试，增量向量化、持久化、故障重试和影子空间切换，以及有限候选RRF融合与可复算规则重排。原N3关键词分页/精读契约和N2推荐算法保留。

在隔离工作树与临时库实施，保留主项目所有未提交N1—N3。不得读取或复制用户的settings/research/knowledge/catalog运行数据库或Key，不操作NoticeAction、权益雷达、Dify源码与服务，不修改五个审校绑定适配器，不做真实收费模型/元宝调用。公开原件由既有安全包恢复，不扩大采集。只允许隔离venv和任务专属模型/向量缓存，不安装全局依赖。

环境预检已在Windows CPython3.14.2隔离venv安装PyMilvus3.0.2、Milvus Lite3.2.2与FastEmbed0.9.1。原生Lite固定向量实际写入及第二进程重开过滤检索通过；重开collection需load_collection，不能把released误认作数据丢失。正式适配器仍须独立验证。默认中文CPU模型为BAAI/bge-small-zh-v1.5（512维、512 token），固定Qdrant ONNX revision；未完成实际推理前不称Embedding已通过。

## A：Embedding与连接配置

- `kind=embedding`与AI/元宝独立，厂商只列实际支持的能力，不把所有聊天厂商当向量服务。
- `local_bge`为本地默认选项，不要求Key；普通运行只使用已校验本机模型，显式准备/测试才能下载。目录为`<data_dir>/runtime/embedding-models/bge-small-zh-v1.5/<revision>`。CPU threads=2、小批次，避免占满本机资源。
- 远端服务真实测试`/embeddings`，严格校验顺序/index、数量、有限非零float、实际维度和模型身份；chat成功不能替代。密钥继续使用现有DPAPI，不回显。
- `runtime_embedding(profile_id=None)`提供desired配置；旧serving空间读取其绑定profile快照，新配置不覆盖旧凭据。删除连接同时使旧profile不可再使用，安全退回关键词。
- 指纹含服务/模型/revision/实际维度/归一化/输入版本/前缀，不含Key。换Key不能导致整库重复Embedding。
- 512 token模型不能盲截1800字符结构片段。子窗口保留原始Unicode跨度，local按实际tokenizer分窗，前缀和上下文也计入限额，覆盖尾部否定、例外和emoji。`text`是精确原文；额外`embedding_text`可组合有限真实上下文，不得冒充连续引文；input_sha绑定实际模型输入。

## B：独立向量治理

- 不复用N3 knowledge_index_jobs/current_generation_id来声称向量成功。另建space/items/jobs/control及派生缓存；source保存/撤信只写dirty标记，不在SQLite事务内做外部调用。
- 正向eligible集合来自SQLite：accepted、reusable、active、known actual year、当前成功结构代次、searchable、原件SHA仍有效。传给ANN的allowlist在top_k之前生效，空集合绝不省略filter；过长集合分批取k再全局排序，不能退化全库查询。
- ANN返回后重新读取权威原文/跨度并核代次和状态，绝不采用向量payload正文或学校年份。异步删除只回收空间，不承担撤信的正确性。
- 任务使用lease/attempt CAS，外部写入后才标成功；撤信或换代次后的旧worker不得复活。确定性vector_id和输入缓存支持幂等重试；超时计费未知保留unknown，不假报零。
- 新模型/维度构建staging空间，校验完成后CAS切active；失败保留旧空间与其查询模型。重启不自动继续收费任务。N3旧库只读不偷偷迁移，显式构建才建立新表/索引。

## C：检索、Agent与页面

- 保留GET `/api/knowledge/segments`原关键词完整分页语义。新增`/api/knowledge/retrieve`返回有限segments/returned_count/candidate_count和真实retrieval模式，不把ANN候选数当全库总数。
- GET状态、原文阅读和后台轮询不触发收费调用。GET retrieve仅允许本地语义或诚实关键词降级；主动提交POST retrieve、用户启动的Agent研究可以使用已配置远端Embedding并记录用量，测试仍只Fake/真实本地模型。
- RRF为`sum(1/(60+rank))`、两路等权、按segment去重；`rule_v1`为可复算精确短语/代码等规则重排，不能宣传为模型交叉编码器。301/302不因相似而等同；不考408不能直接按全文含408排除，正式筛选仍由N2决定。
- Agent和UI共用协调器。未配置/未建库/存储或服务失败时保留关键词检索并提示实际降级，不伪造语义得分、token或就绪状态。
- 设置新增“知识检索”页签，常用项精简，高级地址/维度/索引维护折叠；保留AI与元宝原配置。原生中文IME、选区、迟到响应、测试收据失效、取消、移动阅读和来源安全须有回归。

## 验收

先业务RED、再GREEN，相关新逻辑含分支覆盖不低于80%，独立复查发现P1/P2先修再整合。固定向量存储证明与真实Embedding效果分开记录。

1. 真实Milvus写入/过滤/第二进程重开，ANN前排除他校、他年、旧代次、pending和撤信高分项；空allowlist、分批排序、篡改原件均有负例。
2. 重复任务、缓存、失败、超时、取消、双worker、旧attempt、源撤信中途写入、重启和模型切换均不损坏旧可用索引。
3. 本地真实中文Embedding、精确token分窗和语义检索通过；远端API用严格Fake合同验证，未使用用户凭据不能写真实远端已通过。
4. 同一冻结34题/29原件/17校对比；保留31适用题与3年份挑战、54适用引文，报告同输入下的keyword/hybrid差异、来源Recall@k、证据覆盖、延迟和调用/缓存/未知用量。不改金标/query/答案凑结果；原两组已参与调试，非盲测。
5. 整体N1—N3、设置/Agent/HTTP/知识阅读回归保留，主工作区整合前比较单独main/worker字节基线，五个绑定适配器原始SHA不变。本地4180重载沿用已有明确授权并只操作核对身份的该进程；未经范围内验证的真实服务和用户初次建库步骤如实记录。

实现/日志位于本工作区`data/runtime/node4/`；根控制与独立环境位于主项目`data/runtime/claude-node4/`，均不上传。任务完成更新既有README、需求、项目介绍、路线和本验收文件，不将临时报告误标发布成果。
