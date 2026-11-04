# N4B · 本机Claude实施检查点（待Codex下发）

本文件不是当前任务。只有Codex明确下发本检查点，且N4A已验收通过后才开始。所有业务实现由本机Claude Code负责，Codex仅下命令和验收。保留N1—N3及已验收A，不回退他人改动；现有B草稿未经交付，需你审查、修正并完整验证。

先读`docs/work_nodes/04-hybrid-retrieval.md`、A交付与Codex验收记录，以及[B 独立验收计划](04-b-acceptance-plan.md)的8条草稿复查。沿用A最终公开契约。只做向量后台，不自行进入C的HTTP、Agent或UI。

本次明确下发：A已在`data/runtime/node4/codex-a-acceptance.json`验收通过，公开实现见`claude-a/report.json`，独立验收见`independent-a/final-report.json`；95项定向回归、7处实质修复及8个HTTP安全负例均通过。A源码/正式测试SHA已冻结，原则上不要改A；若接口问题确实必须调整，先在报告说明并补回归，不削弱约束。原完整1662 Python余11个失败是本B/C草稿，Node191通过；现在修B范围，C接口404留给下个检查点。A报告曾将含tests的覆盖合计写90%，根已单独导出六生产模块含分支综合87.48%、每模块>=80.56%，采用根验收记录的真实口径。

## 范围

`knowledge_vectors.py`、`knowledge_vector_build.py`、`knowledge_vector_store.py`、`knowledge_vector_schema.py`、`knowledge_vector_cache.py`、`milvus_backend.py`及合理小型拆分和专用测试。`knowledge_store.py / knowledge_operations.py / knowledge_schema.py / knowledge_indexing.py`只允许轻量dirty钩子和必要接口，不改N3结构索引含义或N2正式推荐。

Facade为`VectorIndex(knowledge_store,settings,embedding=None,backend_factory=None)`，提供`status/build/start_build/cancel_build/search/close`。cancel_build真实通知当前worker停止，返回cancelling或实际cancelled，不能将终止HTTP请求冒充服务端取消；provider已发请求不能承诺退款或强制中断，但不发布迟到结果。status至少含backend、active_space_id、active_profile_id、active_provider、spaces、jobs计数、building、dirty及安全错误标识，并向C冻结资料/片段/窗口、成功/失败/缓存、已知/未知用量字段。search返回N3权威segments、有限候选数、space/profile、usage、latency与真实语义状态。remote且allow_remote=False在调用前拒绝，供GET安全降级；用户启动研究或主动POST才允许remote。所有本次测试仅Fake远端或真实本机模型。

- SQLite新增独立space/items/jobs/control和派生缓存。N3 current_generation_id与knowledge_index_jobs仍表示结构索引，不冒充向量成功。
- 只为当前accepted、reusable、active、实际年明确、searchable、原件SHA有效的公共片段派生向量。私密档案、用户问题和Key不进公共缓存。
- ANN前使用SQLite权威正向allowlist，按学校、年、当前代次和可见范围过滤后取top_k；空集合不发送无条件ANN。长列表分批各取k再全局合并，返回后再次核对来源/代次/SHA。向量payload正文永不当作事实。
- 完整处理A的子窗口及embedding_text/input_sha；相同输入复用缓存，查询向量不落公共持久缓存。指纹与实际模型输入一致，不含Key。
- 外部写入成功后才标完成；lease/attempt CAS、确定性ID和缓存保证重试幂等。撤信/换代次/取消后的旧worker不能复活资料。超时费用未知保持unknown，不记假零。
- 查询失败也不能丢消耗：Embedding返回后ANN失败/撤信复核失败/取消保留该请求已取得的usage；provider超时保留attempted call及usage_known=false。通过每请求安全error metadata（例如retrieval_usage，含calls/known/stage/latency）或等价可读合同传给C1，不能使用并发共享last_query属性或把降级当0调用。不得记录原始Key、私人query或provider原始异常。
- 新模型/维度建staging空间，校验后CAS切active；失败保留旧空间及其绑定模型。重复构建不重复调用已有Embedding；重启不自动续发收费请求。GET status不自动建表、建库或推理。
- 使用一种真实后端：Windows原生Milvus Lite3.2.2 / PyMilvus3.0.2，延迟导入，路径为data_dir下runtime/vector-index专属目录。新进程重开后collection处于released，须load_collection再查；根已用两个进程实测通过。

独立草稿审查补充：同fingerprint换Key成功构建后切换到新的serving profile引用，但失败保留旧引用且不重复向量化已缓存输入；dirty覆盖rebuild_segments、reassess和构建期目标集合变化；lease过期及旧attempt拒绝完成，异步返回build_id与实际持久任务相同；provider超时/无usage汇总保留unknown，取得usage后存储失败不能丢用量；重复构建、Milvus丢失后缓存恢复及实际adapter跨进程重开要测；校验A窗口连续完整覆盖，尾窗可回原文；local判断沿A能力而非只认provider名；cache/Milvus最终resolved路径仍须在data_dir内部，外指junction在写前拒绝。详细复现实验见上述8条规划。不要照搬错误草稿测试要求失败后连尚未处理的新输入也不允许调用；应验证已完成输入不重复。

## 验收和交付

先写/复现业务RED，再GREEN。固定向量验证过滤/持久化，与真实模型效果分开；包含他年/他校/旧代次/待核/撤信高相似干扰、空allowlist、分批排序、同长度篡改、失败重试/lease、重复缓存、重启、模型切换失败和旧空间查询模型一致性。

按A相同隔离venv、任务专属D盘TEMP、测试命令前缀运行，使用临时库。不要启动主4180、读取用户运行DB或凭据、做收费推理、提交或推送。不得修改五个审校绑定适配器。新模块含分支覆盖80%以上，保留N3知识/来源/Agent回归。

报告到`data/runtime/node4/claude-b/report.json`，记录真实RED/GREEN命令/输出、每模块覆盖、真实SDK测试、公开契约、usage/unknown语义和未验项。完成后停止，等Codex独立验收；不自行开始C。
