# 研序 AI · Claude 开发交接

更新于 2026-11-04。开发分支为 `handoff/n4-vector-retrieval`。Claude 负责实现、测试和返工，Codex 在交付后独立检查。4C2 与节点 5–7 已全部本地提交，状态均为 `submitted_for_review`，统一交给 Codex 独立复查。每个检查点保留独立 commit、测试日志、报告和未验证事项。

## 从哪里开始

| 检查点 | 本快照状态 | 接手入口 |
| --- | --- | --- |
| 1–3 | 已验收。包括资料作用域、用户意图、来源有效性、原文分段和精读。 | [验收节点](CLAUDE_WORK_NODES.md) |
| 4A | 已独立验收。95 项定向测试通过，8 项 HTTP 安全负例均拒绝，六个生产模块含分支综合覆盖率 87.48%。本机 BGE 实测 512 维，远端厂商用模拟接口验证。 | [A 实现与接口要求](work_nodes/04-claude-checkpoint-a.md) |
| 4B | 代码已交付，独立验收要求返工。181 项相关正式测试通过，真实 Milvus 跨进程重开及本机模型链路通过。下面的五项缺陷仍可复现。 | **[B 返工任务书](work_nodes/04-claude-checkpoint-b-review1.md)** |
| 4C1 | 已交付，待 Codex 独立复查。提交 `5b658f1`；报告 `data/runtime/node4/claude-c1/report.json`，RED 11 项失败/错误，定向 GREEN 315 项通过（跳过 1），完整回归 1715 项通过（跳过 2），七个生产模块含分支覆盖率 82%–94%。 | [检索、HTTP 与 Agent](work_nodes/04-claude-checkpoint-c1.md) |
| 4C2 | 已交付，待 Codex 独立复查。提交 `9a7fa0f`；C2 定向 64 Python、65 Node、6 浏览器通过；真实本机 BGE + Milvus Lite 的 34 题配对显示来源召回与冻结基线同为 31/31，完整必要引文 25/34 对 29/34，未观察到全面提升。报告 `data/runtime/node4/claude-c2/report.json`。 | [页面、评测与交付](work_nodes/04-claude-checkpoint-c2.md) |
| 5 | 已交付，待 Codex 独立复查。提交 `f57d473`；确定性计划账本与 Skill 完成检查，38 项定向通过。端到端接线随 N6 提交 `237361c`。报告 `data/runtime/node5/claude/report.json`。 | [Agent 路线图](AGENT_ROADMAP.md) |
| 6 | 已交付，待 Codex 独立复查。提交 `237361c`；证据继承、字段候选、两源一致学费和冲突暂停，52 项定向通过。报告 `data/runtime/node6/claude/report.json`。 | [Agent 路线图](AGENT_ROADMAP.md) |
| 7 | 已交付，待 Codex 独立复查。提交 `9000700`；计划/缺口、本轮变化、字段出处和原文回链界面，27 项 Node 单测与 6 项隔离浏览器回归通过。报告 `data/runtime/node7/claude/report.json`。 | [Agent 路线图](AGENT_ROADMAP.md) |

先读 [节点 4 总合同](work_nodes/04-hybrid-retrieval.md)。4B 返工与 4C1 已在前序提交完成；本分支继续交付 4C2 和节点 5–7。此前任务书中“等 Codex 下发”描述的是旧协作节奏。本次由 Claude 先连续实施，自验通过一个检查点后按本页顺序继续，最终把各检查点的提交和报告一次交给 Codex 独立复查。真实付费模型、元宝、4180 主端口和用户运行库仍未在本轮验证。

任务书中旧的 `data/runtime/node4/` 报告与日志路径是历史证据索引。克隆仓库后用 [已收录的验收记录](work_nodes/04-acceptance-snapshot.md) 和独立探针复现，新的运行报告写回本地忽略目录。

## 本地复现

1. 克隆仓库后切换到 `handoff/n4-vector-retrieval`。为每个检查点开独立提交，记录提交 SHA 和报告。若已有同名工作目录，先查看 `git status`，保留现有改动。
2. 按 [运行说明](RUNNING.md) 恢复公开资料包、建立隔离 Python 环境并设置 PDF 工具。节点 4 在 Windows、CPython 3.14.2 上验证过。安装 `requirements-dev.txt` 和 `requirements-vector.txt`，后者锁定 PyMilvus 3.0.2、Milvus Lite 3.2.2 与 FastEmbed 0.9.1。测试使用临时 SQLite、Milvus 和凭据夹具。磁盘空间紧张时，将当前测试进程的 `TEMP` 和 `TMP` 指向专用的可写磁盘目录。
3. 可复用已有的固定公开模型。模型目录为 `data/runtime/embedding-models/bge-small-zh-v1.5/46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59/`。缺文件时从 [固定版本的模型文件目录](https://huggingface.co/Qdrant/bge-small-zh-v1.5/tree/46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59) 显式准备 `model_optimized.onnx`、`config.json`、`tokenizer.json`、`tokenizer_config.json`、`special_tokens_map.json`。权重 94,781,076 字节，SHA-256 为 `1294ea4b6331115a353d81f96b85e8c8d7fdcc284453d5b2fab5b016230aad38`。其余固定摘要见 `embedding_catalog.py`。
4. 优先运行 `python -X utf8 -m unittest tests.test_knowledge_vectors tests.test_knowledge_vectors_milvus -v`。补跑 A 的 `tests.test_embedding_{inputs,local,settings,transport}` 四个模块，以及 `tests.test_provider_settings`、`tests.test_ai_transport`、`tests.test_yuanbao_auth`、`tests.test_yuanbao_integration`。模块名要分别传入 unittest。浏览器和 Node 的依赖、环境参数见 [运行说明](RUNNING.md)。
5. [独立探针](work_nodes/probes/independent-b/) 适合复查业务测试难覆盖的异常路径。将七个 `*.py` 复制到本地忽略目录 `data/runtime/node4/independent-b/`。先把 Claude 当轮交付报告保存为 `data/runtime/node4/claude-b/report.json`，取其 SHA-256，依次运行 `python -X utf8 data/runtime/node4/independent-b/run_probes.py --expected-report-sha <SHA256> --mode <模式>`。模式分别为 `fake`、`regressions`、`query-accounting`、`native`、`local`。最后一项可传 `--model-dir`，缺固定权重会标明未执行。逐个运行真实模型和 Milvus 测试，避免互相争用内存。

## 后续实施顺序

1. **4B 治理返工。** 让取消或失败的复建保留旧索引里仍有效的片段。保留缓存写失败、ANN 失败、超时和取消时的真实调用记录，区分已知用量、未知用量和零调用。仅显式构建创建向量表。让 Milvus 文件锁释放失败可见，按具体数据库路径释放。提交前重跑五类复现与真实重开。
2. **4C1 接通后端。** HTTP 和 Agent 共用同一个检索协调器。保留关键词分页，新增有限候选混合检索，先过滤学校、年份、代次和可信状态，再融合和重排。用户明确发起才允许远端向量调用。保留错误降级和用量记录。完成真正的服务端取消和本机模型准备入口。
3. **4C2 接通页面并做效果对比。** 增加精简的知识检索设置与真实索引状态。检查切换厂商、测试收据、异步响应、中文输入、移动端和原文定位。用真实 BGE、Milvus 和冻结的 34 题做同输入配对对比。保留既有关键词基线和三条年份挑战，逐题报告来源召回、必要引文、延迟与用量。冻结金标不随结果修改。
4. **节点 5 · 研究计划。** 建立目标、缺口、证据、预算、完成条件的账本。让 Skill 有可验证的执行结果。缺资格优先查章程，缺费用优先查收费规则，失败后换合规来源或给出待补证项。以确定性测试覆盖超时、预算耗尽、停止和恢复。
5. **节点 6 · 多轮与推荐。** 按证据版本继承上轮成果，条件变化只重算受影响部分。把新资料解析成带来源和作用域的字段候选，验证后再参与推荐。冲突、未知资格和历史调剂记录保留各自语义。推荐仍由确定性规则输出理由，用户可回到原文。
6. **节点 7 · 交互和验收。** 页面明确呈现已知、待补、候选取舍和本轮变化。完成真实浏览器回归和代表性任务，检查数据、原文、算法、元宝和模型链路。真实付费服务的验证由用户明确发起，并在报告里与模拟结果分别记录。

节点 5–7 的任务编号、依赖和验收条件见 [Agent 路线图](AGENT_ROADMAP.md)。如发现 4B 合同需要变更，先说明影响及调用方，再提交对应回归。

## 交给 Codex 的验收材料

每个检查点在报告中提供 `status=submitted_for_review`、提交 SHA、变更范围、真实 RED/GREEN 命令与结果、生产模块含分支覆盖率、接口契约变化、未验项和复现步骤。把验收日志放在本地忽略目录。提交只包含代码、测试、公开资料夹具及交接文档。用户设置库、研究库、原文运行库、模型缓存和 Key 属于本地环境。

保持冻结原件、引文金标和五个已绑定采集器字节一致。旧数据库的兼容测试使用临时副本。主应用的测试使用隔离数据和端口。

## 本次交付摘要

- 提交：`9a7fa0f`（4C2）、`f57d473`（N5）、`237361c`（N6）、`9000700`（N7）。
- 整库回归：Python 1,747 项通过（跳过 2）；Node 220 项通过；C2 浏览器 6 项、N7 隔离浏览器 6 项通过。
- 覆盖：N5–N7 四个后端模块含分支合计 93%；C2 三个前端模块分支 80.85%–84.51%；`agent.mjs` 整文件行 60.26%/分支 74.21%，未覆盖主要是既有 mount 控制器，浏览器回归另行执行。
- 明确未验证：真实付费模型/元宝闭环；远端 Embedding；用户运行库迁移；4180 主端口；OCR；发布链年度自动授予；关键词整句匹配修复；跨机器浏览器环境。
- C2 诚实结论：来源召回与冻结基线相同，完整引文覆盖更低；关键词臂 0/31 是当前整句匹配边界，不能据此宣称优于 BM25 或其他 RAG。
