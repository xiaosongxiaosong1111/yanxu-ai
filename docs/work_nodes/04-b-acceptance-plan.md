# N4B 独立验收设计（只读，未实施）

核对范围：`docs/work_nodes/04-claude-checkpoint-b.md`、当前A的catalog/transport/prepare_inputs、已有vector facade/build/store/schema/cache/Milvus草稿。未修改生产代码或正式测试，未读取用户运行库，未启动服务或收费请求。以下是静态风险与下一轮临时库验收设计，不表示B已通过。

当前B任务包已正确写入：独立向量状态、ANN前正向allowlist、返回后复核、子窗口、模型影子切换、remote主动调用边界及真实SDK重启检查。应再明确以下8点。

1. **同模型换Key后的serving配置引用（P2，草稿可定位）。** `knowledge_vector_store.begin_build` 同fingerprint冲突时只更新error/time，不更新profile_id；finish_build也不切新profile_id。A每次保存会生成新ep快照，Key不在fingerprint中是正确的，但同模型新Key成功重建后仍可能绑定已失效旧Key。验收：两个不同ep、相同模型fingerprint、不同假Key；新build失败仍用旧ep，新build成功后应采用新ep，Embedding缓存可复用且不重新计费。不同模型/维度则新空间，失败不影响旧空间查询模型。

2. **dirty与目标集合完整性（P2）。** 草稿钩子覆盖save/显式撤信/版本选择/原件丢失，尚未覆盖`rebuild_segments`和`reassess`等导致当前结构代次或准入集合变化的路径。finish_build依赖revision及最初items，不能仅因旧快照全部成功就宣称当前全部ready。验收：构建过程中显式重分段、将pending通过重审变accepted、切当前版本；结束后必须保留dirty/needs_rebuild或对齐后再切换，且所有旧items即使残留也不能可信召回。

3. **lease、取消和异步build身份（P2）。** `finish_item`目前只比token/state，没有检查lease期限或所属build；`start_build`返回的随机build_id和worker内部build_id不同。close只设置停止事件，不说明线程实际何时退出。验收：控制时间让lease过期/被新attempt领取，旧回调不得完成；关闭/取消后不能再发后续Embedding；POST返回的build_id应与持久化任务一致。并发构建失败要有可见错误，不得吞成永远not_ready或假started。

4. **未知费用不能在汇总变为假零（P2）。** 草稿build stats初始化`usage_known=True,input_tokens=0`，provider在返回前超时时尚未修改stats，job虽可标uncertain，last_build却可能仍显示用量已知且0。验收：Fake远端分别模拟返回前超时、返回无usage、成功返回usage后Milvus失败、缓存命中。汇总必须区分已知token之和与存在未知用量；向量写失败不得丢掉已取得的provider用量。不要承诺无幂等服务上的精确一次计费。

5. **空间快照/重启后的真实可用性（P2）。** 草稿以同fingerprint空间复用items，同profile重复build仍逐项重新claim/upsert，Milvus `contains`尚未接入；需要明确增量与显式全量恢复的行为。验收：重复build只对新增/变化输入调用Embedding；清空Milvus后可仅用派生缓存重建；同模型重建失败仍能查旧有效items；新进程重启且collection released时先load再查。验证使用实际adapter和字符串vector_id，不能用root独立SDK smoke或MemoryBackend替代整个闭环。先前6条草稿测试有1条把“尚未处理的输入”也当成已缓存，测试应检查已缓存输入不重复，而不是要求失败后整个未完成库绝不再调用。

6. **A子窗口完整覆盖与真实命中位置（P2）。** B草稿prepare只校验单个window是原文子span，没有校验完整覆盖、无遗漏/重叠、空windows或重复window。A最终模型维度/前缀/输入版本是唯一协议，不要沿用未验收的512/384猜测；input_sha必须仍是实际document_prefix+embedding_text。验收：长中文/emoji/尾部否定与例外、跨页表头，逐个window坐标与原稿一致，覆盖全parent；命中尾窗可回到真实原文，不能将合成context作为连续quote。不同profile、维度和输入前缀的缓存不得混用。

7. **安全范围过滤的对抗验收不能只测一所学校（P1门槛）。** 目前草稿总体采用SQLite eligible → completed IDs → ANN allowlist → SQLite复核，方向正确。独立验收需故意注入48条他年、8条他校、pending、旧代次、同长度篡改raw和撤信高相似向量，使用较小top_k确认目标未被挤掉；空allowlist绝无ANN；超过256 IDs的多批次排序和子窗口归并不漏更佳parent。query期间再撤信应在返回前剔除。remote allow_remote=False必须在任何推理前拒绝；本机BGE/Ollama按A最终local能力判断，不把所有非BGE都冒充远端。

8. **专属目录边界和输出契约（P2）。** VectorCache当前只比相对于`self.root.resolve()`，若runtime/vector-index本身是外部symlink/junction可能自洽通过；MilvusBackend也未对最终resolved路径再比data_dir。验收：在临时目录模拟外指链接（系统不允许时如实skip），应在初始化或写入前拒绝；不得触碰外部目录。status在无DB时不建表/建模型/连Milvus；query向量不持久化；错误不含路径/Key。status真实区分结构索引成功、向量未建/部分/失败/正在build及旧active可服务。ANN候选数不能叫全国总数。

## 交付验收层次

- A先完成当前返工并冻结公开契约。B由本机Claude读取草稿、写业务RED、修复及声明接管；Codex不修改生产或正式tests。
- 独立验收先运行Fake provider/临时SQLite治理边界，再实际Milvus固定向量及跨进程重启，最后在本机公开模型上验证prepare→Embedding→Milvus→权威片段链路。三种结果分别记录。
- 每模块统计含分支覆盖，保留N3知识/来源/Agent回归。原文/模型输入SHA与保护的五个适配器不变；不读取主工程私有运行库，不重启4180，不发生真实远端费用。
- B通过以后再进入C；本文件仅是检查设计，没有新增业务实现或验收通过结论。

### Query失败的费用事实合同补充

语义查询已经取得Embedding响应后，ANN/原件复核失败或随后取消，不能在关键词降级时丢掉真实消耗。建议异常携带请求独立且已清洗的`retrieval_usage`，字段为`embedding_calls, successful_embedding_calls, input_tokens, usage_known, stage, latency_ms`。超时返回前为1次尝试/0次成功/unknown/token=null；ANN失败但Embedding已返回则保留已知token。不得用全局`last_query_usage`串并发请求，不保存Key或原始私人query。C应在异常转换之前提取该metadata。`probe_query_accounting.py`已准备3个Fake场景，尚未运行。
