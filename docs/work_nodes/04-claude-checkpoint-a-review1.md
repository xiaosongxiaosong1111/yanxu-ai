# N4A 续接与首轮独立复查

继续你自己的N4A会话。仍遵守`docs/work_nodes/04-claude-checkpoint-a.md`及主合同。上次CLI在完整回归期间意外退出，无最终报告，业务成果已保留。不要重做全部探索，不扩大到B/C。所有实现及正式回归修复仍由你负责，Codex仅派发与验收。

独立验收发现下列问题。先自行复现并补业务RED，再修复，不能通过削弱测试规避：

1. 目录用例复核已校正：原失败夹具实际使用custom附加models_path，不是SiliconFlow。当前改成真正provider=siliconflow并断言白名单`/models?sub_type=embedding`是正确修正；不要为旧夹具放开任意models_path覆盖。以当前正式测试及安全约束验证即可。
2. `embedding_inputs._hard_split`对合法长前缀不前进：`document_prefix='背'*256`、local512，公开原文重复8次，独立子进程4秒超时。疑因measure已包含前缀又与减前缀预算比较，cut到left+1仍循环。合法预算不足须快速可控报错，充足预算须前进且覆盖尾部；任何配置不能死循环。
3. `embedding_local._verified_dir`只凭ONNX大小/mtime缓存验证，首次通过后篡改tokenizer文件而不改ONNX仍被接受。固定模型全部必需文件的身份/哈希须核对，不能仅校验权重。无网络隐式下载。
4. `SettingsService` profile_id未绑定实际dimension。相同服务、模型、Key的测试从7维变8维后覆盖同一旧profile，runtime(old_id)从7变8。旧serving快照必须不可被新维度测试覆盖；新观测维度产生新身份。凭据轮换与持久重启语义保留。
5. `EmbeddingTransport`在provider返回后未再次检查stop_event；Fake request设置stop后仍返回成功vectors。完成前复核取消，不发布已取消结果。

证据：`data/runtime/node4/independent-a/preliminary-suite.log`、`preliminary_edges.py`、`preliminary-edges.json`、`vector-preliminary.json`。探针仅供独立证据，不要改探针以绕过问题；在正式测试中加入相应业务回归。

首批60测试59通过，数值/缺项/NaN/Inf/零向量/极端范数11探针通过，删除profile failclosed与普通prefix/SHA单次绑定已通过。继续保持。

A检查点不负责B/C尚未实现的HTTP404和向量后台草稿失败。回归请保存完整输出及真实exit code，避免重复整套跑两三次只截尾；A与N1—N3须无新增回归。必要时分清未来N4B/C测试。不要并行跑多套高内存全量用例。TEMP/TMP已在D盘任务目录。

交付前完成A相关分支覆盖>=80%、真实本机模型离线烟测，所有命令/结果/未验项写入`data/runtime/node4/claude-a/report.json`，status为`submitted_for_review`或`blocked`。达到范围后停止等验收。
