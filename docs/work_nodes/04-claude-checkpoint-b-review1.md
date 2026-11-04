# N4B 第一轮独立验收返工

继续你自己的B会话，只修B，不进入C。仍由本机Claude写业务与正式测试；Codex下发任务并独立验收。遵守原B任务书、A已验收合同及所有数据/服务/提交边界。你已提交B报告，CLI退出0，根仅将其视为submitted_for_review；B还未通过，主项目未合入。

根与独立验收员已用临时SQLite、Fake远端及真实本机SDK复测，原27用例、真实Lite两进程Facade重开、SettingsService显式本机test/save→真实BGE→Milvus→权威片段链路均能工作。下列5类实质缺陷仍须修复，不能只改测试让它通过。先复现并补业务RED，再修GREEN。

## 必修

1. **取消/失败的同空间复建破坏旧serving内容**：相同fingerprint已成功build后重建，取消或cache写失败时active_space_id虽然没变，原先可查询的segment却从完成items中消失。须将已经提交的可用内容与新attempt结果区分；失败/取消不能撤掉仍有效、同SHA/当前代次的旧向量。新资料、撤信和换代次仍受权威eligible过滤，不能为保旧结果复活失效来源。回归比较完整旧segment集合，不能只断言pointer未变。
2. **cache.put失败丢失已知用量**：Embedding已返回合法向量与known usage，随后cache.put失败，当前job丢usage，汇总将本次已知调用误改unknown。须在后续缓存/ANN/复核失败时保留已收到的验证用量；严格区分尚未发送、已发送未返回、已返回合法收据及后续存储失败，不按一个returned布尔值丢掉事实。无响应超时仍unknown，不能假0；不要无依据承诺精确一次计费。
3. **query失败三路径丢调用事实**：Embedding成功后ANN失败、provider超时、Embedding返回后取消，异常均没有可消费的安全retrieval_usage，C1降级可能被误算成0。提供每请求的明确安全metadata或等价合同（如embedding_calls、successful_embedding_calls、input_tokens数值或None、usage_known、stage、latency）；成功返回已知usage后失败保留已知，超时unknown。不要共享last_query或持久化私人query/Key；取消仍取消，不能吞成普通成功。
4. **显式build前自动建N4表**：`knowledge_schema.ensure_schema`无条件调用`ensure_vector_schema`，使N3普通save_source已建立4张N4表。按主合同，仅显式build初始化向量表；N3保存/撤信/重审仅在已有表时写dirty，无表安全跳过。GET/status保持纯只读，不建DB/表、不推理。保留N3结构表和旧库兼容。
5. **Lite释放失败完全静默**：`release_server(self._key)`失败被except Exception:pass吞掉，调用方误以为已释放，后续重开仍可能LOCK失败。提供安全可见的错误/状态或服务端稳定错误事件，不能回显原始异常/Key/私人路径；保留只在本路径最后一个引用关闭时release，绝不能全局stop_all/close_all或关闭其他数据库。回归注入释放失败与同路径双引用。

## 独立证据

交付报告SHA：`326e0dabee2f241d0ab6e2612a7382286f3d27ffcb0a46cff98447e1b4d42363`。

`data/runtime/node4/independent-b/runs/326e0dabee2f241d/`下的query-accounting、regressions及其他模式stdout/stderr与JSON；探针位于同目录上级的`probe_regressions.py`、`probe_query_accounting.py`。请只读复用，不能改独立探针绕过缺陷。源码/正式tests及报告前后SHA一致，故本反馈绑定实际交付。

普通Fake探针一处初始强制失败位置与正常skip-upsert不匹配已由验收员在ignored脚本中纠正，不是业务缺陷；本机初探针遗漏显式test/save也已纠正，A拒绝未配置运行是正确行为。不要为它们放开安全约束。

## 报告需校正

- `report.json`加`status=submitted_for_review`和实际修改文件；后续C1/C2也由Claude实施，Codex仅派发/验收，纠正“Codex implements C”的两处旧文字。
- uncertain应针对remote/not-local而不是local，当前报告写反。recording_order描述须与最终真实代码一致，不能声称已在ANN前持久记录却实际只改内存stats。
- 27项为23个Fake业务+1个本机模型+3个native SDK，4项real是27项的子集，不重复累加；覆盖率用“六生产模块含分支综合覆盖率”，不是纯branch百分比。每模块含分支>=80%。
- 原完整1686测试只余10个C HTTP草稿失败；无新广泛修改时不要再反复跑整库。优先B全部正式回归、A95及必要N3知识/原文/HTTP/Agent相关，真实SDK与模型串行。最终N4统一全库验收。

环境保持专属venv和D盘TEMP，不安装全局依赖，不读用户运行库或Key、不启动主4180、不碰五个绑定适配器、不提交推送、不清理他人工作。修完保存实际RED/GREEN/覆盖及公开错误metadata合同到`data/runtime/node4/claude-b/`，更新report.json后停止等独立复查。
