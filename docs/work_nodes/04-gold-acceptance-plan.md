# N4 C1/C2 · 金标与检索只读验收规划

实施者必须为本机 Claude；Codex 仅派发、只读复查与验收。本文只根据当前草稿提出检查点，不认定待实施 C1/C2 已通过。

本次仅阅读 C1/C2 任务书、knowledge_retrieval 草稿、B 的 VectorIndex 搜索接口、既有评测工具和公开冻结金标；未修改业务、正式 tests 或金标，未读取用户 runtime 数据库、启动服务/模型或运行大型套件。A 通过不等于 B/C 已通过。

## 六个优先验收点

1. **同输入比较尚未实现，不能把两类问题混比。** 现有 `tools/evaluate_knowledge_gold.py:_question_result` 只用人工冻结的 `retrieval_query` 直查 Store；C1 草稿则先取最多 40 个关键词候选，融合后还执行 hydrate、科目相容和规则重排。不能直接拿这个旧入口与自然语言 hybrid 宣称向量增益。
   - Claude 应保留三条明确分开的 arm：①原 `retrieval_query` 的 N3 兼容重放；②原 `human_question` 的 keyword；③完全相同 `human_question` 的真实 hybrid。后两条共享同一份临时语料、学校/参考年度/entry scope、top_k、关键词候选/重排策略和最终补读预算，差异只在是否有语义通道。②可通过协调器无向量依赖的已知基线路径运行，不必给产品增加算法开关。
   - 每题落盘 question 原文与 hash、arm、scope、候选上限和预算；断言②③输入字节与 scope 相等。不得只对 keyword 去停用词、改写或换成人工关键词，也不得把金标答案注入索引或查询。若自然语言整句导致当前严格关键词实现几乎无命中，必须说明这是“当前实现基线”，不能推广成优于所有关键词/BM25方法。按题列出新增命中、丢失命中和未改善。此评测已给定学校与历史参考年，不能宣传成全国无条件找学校的准确率。原两组已被调试观察，JSON 继续明确非盲测。

2. **年份/发布链挑战需要真实负断言，不能仅看报告里的常量。** 原工具 `_challenge_results` 将 `publication_chain_runtime_supported` 固定为 false，只检查保存的主来源年/质量，没有证明真实向量或最终检索没有泄漏该附件。
   - 冻结 34 题、29 原件、17 校、31 适用题/3 挑战、54 适用引文/61 总引文，所有 fixture 字节/query/答案不动，仍从 public bundle 原始 SHA 校验→生产提取→准入→分段→临时 SQLite 导入。未知年资料不可通过 metadata_year、requested_year、向量 payload 或关联发布页被自动授年。
   - 对 PF02、catalog-11、catalog-12 检查实际 entry.year/quality、eligible 向量集合和两条 arm 的全部返回 source；无年 PDF 保持未知/待核且不能成为可信命中，父章程/入口允许按自身范围独立出现，不能算附件已通过。另单列 PF06 的 2024 线、PF10 的 2026 历史调剂、catalog-10 的 2027→2028 反推：source 年份保持原值，目标年事实仍未知。三题挑战不混入 31 题召回分母，7 条挑战引文与 54 条适用引文分开报告。

3. **来源召回不能替代完整证据精读，向量上下文不能充当原文。** 草稿 `_hydrate` 已回到 N3 读取，并非采用 ANN text，但现有 N3 评测仍需被真正接到 C1 返回命中之后；不能只检 vector_id 或 source_id 就判题对。
   - 两条比较 arm 对最终 top_k 使用同一 N3 read_segment，固定 before=3、after=3、单次 max_chars=12000，读回调用数不超过 top_k，最终证据读回总量不超过 top_k×12000。候选 hydrate 的内部读取另外计数，不偷偷算入“只读了同等最终证据”的宣传。
   - 逐项检查正文/邻接/独立表头 context 的 canonical text SHA、extraction/generation、Unicode `[start:end]`、实际 PDF 页和返回预算。复用原重复引文的冻结全文/整页空白一致映射，不用 find 首个命中。embedding_text、父标题拼接、ANN payload 不计为连续原文。保留 PF06 的远处“专业学位”与 PF10 的年度/末尾限定缺口；只有在相同预算下真的读到了全部必读原文才算改善，不用扩大预算或金标 oracle 补齐。

4. **C1 草稿现有 fake 主要覆盖静态结果，仍缺执行期间有效性与共用调用链的证明。** RRF/rule_v1 的单元断言不能证明最终 Agent、HTTP 都用同一索引、同一精读与过滤边界。
   - Claude 用小型真实临时 Store + 可控 Fake ANN 验证：ANN 取候选后、hydrate/返回前发生撤信、原始 SHA 变更、结构换代或跨校/跨年，最后全部拒绝；伪造 ANN text/页码/代次不能成为引用。空 allowlist 和 metadata 高分污染先过滤，不能只在全库 top_k 之后补过滤。正式推荐仍走 N2。
   - 重复窗口归并为父 segment 后才 RRF；同一 SHA 两 URL 不得被声称两份独立证据。复算等权 `1/(60+rank)` 和 rule_v1 各项贡献；301/302/303 精确区分，“不考408”不因来源其他段落提过 408 而整篇排除。只报告有限 candidate_count，不假造 total。用 Spy 验证服务/Agent 共用已注入 VectorIndex，GET 状态/原文/轮询/远端索引 GET retrieve 为零远端调用；显式 POST 和研究调用才允许远端，取消必须传播为取消，不能显示成功降级。

5. **未知用量有一个明确的草稿接口错位，需要 C1 修复。** 当前 `knowledge_vectors.search` 返回顶层 `usage_known`；`HybridRetriever._usage` 仅从 `result['usage']` 里取字段，顶层 known 标记会丢。`_semantic` 捕获异常后又返回空 metadata，若已做 Embedding、随后 ANN 失败，调用/用量可能被整段丢弃。
   - Claude 应统一真实传递 `embedding_attempts/calls`、`input_count`、cache_hits 和明确 known 标记；无字段或超时只能是未知，不能靠空字典/default=0 说零 token/零费用。本机调用、远端已知 usage、远端未提供 usage、缓存未调用、超时计费不明应区分；没有定价就不生成费用数值。
   - 小 Fake 验收五种结果：无调用；已知 usage；usage={} 且 usage_known=false；Embedding 超时；Embedding 已成功但 ANN 失败。最后两种即使关键词降级也应保留可确认的 attempt 与 known/unknown 边界。另验证 GET 的 0-call 是真实 Spy 计数，而非把未知调用写零。Agent 中 Embedding 用量不增加元宝检索或官网读取计数。

6. **C2 需要一次真实流水线与可复现成本报告，不能以“Lite 能启动”代替语义效果。** 现有 evaluate 入口尚无真实模型、Milvus、空间指纹或冷/热查询阶段；当前 RRF 同分按随机 segment_id 排序，独立重建时还可能影响截断边缘结果。
   - 固定本机模型 revision/ONNX SHA、tokenizer/输入策略、512 维、CPU provider、threads=2、SDK/引擎锁定版本；实际原件窗口送真实模型→Milvus→C1→N3 精读，构建与推理严格离线，仅用根代理已经准备的固定模型。保存原件/提取/结构/模型/空间指纹、各来源窗口数、实际 Embedding 调用/输入/缓存及构建失败明细；不使用固定向量或合成句子作为金标语义证据。
   - 在同一临时库上配对运行②③，构建时间单列，模型首次加载/首次查询与热查询延迟分开，记录所有预热调用；同分结果以 source SHA+canonical span 追踪而非临时 UUID 判断“语义变化”，必要时让 Claude 定义稳定 tie-break。报告 source Recall@k、完整必读证据/引文覆盖、每题耗时及学校/年度/状态/span/预算违规数，改善和退步一起列。0 违规是安全验收要求；检索效果没有提升时如实保留，不回改冻结集。低内存环境一次串行执行，小批次，不并发跑大型套件或重复下载模型。

## 已读依据定位

- `docs/work_nodes/04-claude-checkpoint-c1.md`：共用协调器、显式下载/构建、只读 GET、远端主动调用、取消、规则重排合同。
- `docs/work_nodes/04-claude-checkpoint-c2.md`：同自然语言输入比较与 UI/交付边界。
- `knowledge_retrieval.py`：`_usage`、`_semantic`、`_hydrate`、`retrieve`、`rule_rerank` 草稿。
- `knowledge_vectors.py:search`：顶层 usage_known 与空间绑定模型接口，B 仍须以最终验收版为准。
- `tools/evaluate_knowledge_gold.py`：`_read_hits`、`_question_result`、`_metrics`、`_challenge_results`、`evaluate`。
- `docs/KNOWLEDGE_EVALUATION.md`、`tests/fixtures/knowledge_gold/corpus.json`：原 N3 分母、边界与未完整补读题。

本次只读确认 corpus SHA：`0b46b370bb75215fac0fa3c028a80309f37b28c300b0b36f7fa9cd04448edcd9`；没有据本规划写任何业务修复或正式测试。
