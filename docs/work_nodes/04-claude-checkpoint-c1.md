# N4C1 · 混合检索、HTTP与Agent共用通路（待下发）

仅在Codex明确下发且A/B通过独立验收后开始。所有业务与正式测试由本机Claude Code实施，Codex只派发、执行验收及只读复查。不独自在代码库，保留N1—N3和A/B，未验草稿自行审查取舍。完成本检查点后停止，等验收再进入C2。

先读`docs/work_nodes/04-hybrid-retrieval.md`及A/B报告和独立验收记录，采用最终接口。范围是`knowledge_retrieval.py`及必要拆分、`knowledge_api.py`、`service.py`、`server.py`、`research_service.py`、`research_tools.py`、`research_knowledge.py`、本机固定公开模型的显式准备小模块/接口、相关Skill和专门回归。不实施前端或N5研究规划/N6事实自动采用。

## 行为合同

- SQLite是权威原文/质量/代次。有限keyword+semantic候选按segment_id去重，等权RRF=`sum(1/(60+rank))`，规则重排`rule_v1`须可复算，保留返回来源、排名及规则贡献。不能宣传为cross-encoder或录取概率。
- 301/302不是同一科目；否定408不等于排除全文提到408的所有资料，正式候选筛选仍交N2。不能把同SHA多URL当多份独立证据。
- 保留GET `/api/knowledge/segments`完整关键词分页与精读契约；新GET/POST `/api/knowledge/retrieve`只返有限top_k及returned_count/candidate_count，不能冒充全库/全国total。参数白名单、重复参数、超界limit、school/year/entry过滤、隐私/secret边界、错误脱敏都验证。
- GET retrieve只允本地语义；远端索引时关键词诚实降级，明确degraded_reason，绝不后台计费。POST是用户主动搜索，可用已配置远端并记录已知/未知usage。GET状态、轮询、原文阅读不调用Embedding、不建库、不自动重建。
- GET `/api/knowledge/vectors`纯只读状态；POST `/api/knowledge/vectors/build`显式异步构建并202返回；POST `/api/knowledge/vectors/cancel`接B的真实cancel_build，不能只abort请求。Host/Origin/CSRF沿用现有保护，查询和构建分别限流，无Key回显。后端错误安全返回，关闭服务确实关闭共享索引。
- 给C2一个明确的本机模型准备入口：显式POST `/api/knowledge/embedding/prepare`，只下载A固定公开model/revision/固定5文件，校验锁定SHA、大小和目录边界，临时写入成功验证后发布；已有完整缓存直接复用。普通GET、导入、启动、索引轮询不得下载；模型目录/URL不能由客户端任意指定，不携带任何用户Key。延时操作异步202+纯状态，失败保留可见原因，状态并入既有向量/模型状态契约即可，不造新的复杂系统。全部用Fake下载回归，实际已准备固定文件可做离线验证，不重复下载约95MB权重。若选CLI加UI准备方法而无下载接口，须先向Codex说明并一致修改C2验收，不能伪造可点击功能。
- `AdmissionService`拥有一个共享VectorIndex和HybridRetriever，Agent使用同一协调器，不再另开同Lite DB竞争。现有依赖注入/fakes兼容。
- 用户发起的研究可显式使用远端Embedding；在研究工具结果/用量中单列Embedding，未知token保持unknown，不能记为新元宝检索或新官网读取。仍遵守控制/超时/取消与多校交错，不扩大确认范围。
- 未建库、模型文件缺失、连接删除、向量后端失败均可关键词降级；取消不吞成成功降级。返回前核对当前source/代次/原件SHA，撤信条目不可复用。
- 用量跨层合同须明确：B返回的usage_known在顶层，不能仅从usage字典读它；Embedding已调用而ANN失败，或provider超时后关键词降级，也应保留安全的调用/已知及未知用量记录。降级不等于没有发调用，`{}`不能被消费层误算成已知0；必要时采用B已冻结的安全error metadata，不回显Key或原始错误。
- 更新Skill说明当前真实工具能力/收费边界，保留原文精读、年度、否定/例外规则。新检索不把网页字段直接送入正式推荐，N6再处理。

## 检查和交付

先真实业务RED，再实现GREEN，N4相关新分支覆盖>=80%。已有`tests/test_knowledge_hybrid.py`和`test_knowledge_hybrid_http.py`是待审草稿，可修正错误夹具但不能削弱正确安全断言。补真实Store+共享索引联调、Agent actual search/read通路、跨校/年/代次/撤信、GET远端0调用/POST主动调用、关闭和安全API回归。远端全部Fake，不读取用户Key、不发收费调用。

环境与D盘TEMP延用A/B，专属venv内已装真实Lite/本地模型。只使用临时测试DB；不得启动主4180，不修改五个绑定collectors、不提交/推送、不安装全局依赖。别并行反复跑高内存全库测试；保存一次完整输出和真实exit code。

报告到`data/runtime/node4/claude-c1/report.json`，列改动、接口、RED/GREEN、覆盖、实际和未验边界，status=`submitted_for_review`或`blocked`。C2随后负责UI、冻结真实金标公平对比与交付文档，本检查点不自行扩展。
