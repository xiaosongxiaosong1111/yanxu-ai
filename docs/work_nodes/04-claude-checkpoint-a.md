# N4A · 本机Claude实施检查点

你是本机Claude Code。用户明确要求你写代码，Codex只负责下命令和验收。当前工作区已包含通过验收的N1—N3和一批未经验收的N4草稿；其他Codex实施者均已停止。你不独自在代码库，不回退N1—N3，不清理他人的工作。

先读`docs/work_nodes/04-hybrid-retrieval.md`。本检查点只完成Embedding与连接配置A，完成后提交报告停止等Codex验收，不自行实施B/C。其他N4草稿保留，跨模块调用使用冻结合同；必要时报告接口问题。

## 你的范围

`embedding_catalog.py`、`embedding_transport.py`、`embedding_inputs.py`及合理拆分的Embedding小模块、`settings_store.py`，`ai_transport.py`仅必要的安全Embedding请求与模型目录兼容，以及相关测试。先审查已有草稿，自己决定保留、修正或重写；未经完整测试的草稿不能算你的交付。

- 独立`kind=embedding`，保留AI与元宝原契约及配置。默认本地中文模型`local_bge`，远端仅实际支持的厂商，真实向量测试和绑定维度/模型身份的收据；不拿chat成功冒充Embedding成功。
- `EmbeddingTransport(data_dir,transport=None).embed(config,texts,purpose='document'|'query',stop_event=None)`返回有序vectors、dimension、model、model_revision、usage、usage_known、input_count。拒绝bool/NaN/Inf/零向量、缺项、重复index和维度漂移，输出归一化。
- `profile(config)`返回不含Key的模型空间指纹，包含服务、模型/revision、维度、归一化、输入版本和前缀。
- `prepare_inputs(segment,contexts,config)`保留原文子span的`text,char_start,char_end`，另有合成的`embedding_text,input_sha,window_id`；input_sha绑定实际模型输入。精确token窗口包括前缀/上下文预算，不能静默丢掉1800字符片段末尾的否定/例外。
- `runtime_embedding(profile_id=None)`支持保存的desired配置和旧serving的加密快照。新配置不能破坏旧空间查询模型；删除Embedding清理旧profile后诚实降级，不借用聊天Key。
- 复用已有安全HTTP、DPAPI及会话降级，不读取真实用户运行库或凭据，不发真实收费调用；远端只用Fake合同测试。

## 已准备环境

PATH最前和VIRTUAL_ENV已指向N4专属venv，Python3.14.2。已安装SDK3.0.2、Lite3.2.2、FastEmbed0.9.1及项目测试依赖，不安装全局依赖。

根代理已在本工作区准备固定公开模型：`data/runtime/embedding-models/bge-small-zh-v1.5/46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59/`，含model_optimized.onnx与4个tokenizer/config文件。ONNX SHA为`1294ea4b6331115a353d81f96b85e8c8d7fdcc284453d5b2fab5b016230aad38`，94781076字节；BAAI/bge-small-zh-v1.5是512维、512token。使用specific_model_path、local_files_only=True、CPU threads=2、小批次；不要再次下载权重。常规运行不能隐式下载，显式准备/测试方可准备公开模型。

## 交付与验收

先记录真实业务RED，再实现GREEN。优先覆盖JSON/vector格式、scope/输入边界、模型版本/凭据隔离、删除/保存、IME相关配置契约、512token窗口尾部与Unicode、取消/超时和网络错误清洗；新逻辑含分支覆盖80%以上。保持现有AI/元宝测试通过。

可使用python -X utf8 -m unittest、python -m coverage、node --test和git diff/status。测试使用临时库；不要启动主项目4180，不发布或提交代码，不修改五个审校绑定适配器，不读取主项目data/runtime或全局凭据。所需公开数据在工作区中，且tests已在PYTHONPATH。

会话恢复说明：仅将本次Claude和Python测试的TEMP/TMP改到任务专属D盘目录，不搬动业务代码或用户文件。此前只读包探索命令因范围或命令前缀限制被拒绝，不要重复同一拒绝命令。FastEmbed0.9.1所需公开源码已复制到本工作区`data/runtime/node4/reference/fastembed/`供Read/Grep；直接读取这里，无需越出工作区。运行测试使用上述已授权前缀，避免多操作混合命令。其余环境诊断可报告给Codex执行，Codex不会代写实现。

将真实命令、RED/GREEN输出、覆盖、改动文件、接口合同、未验项保存到本工作区`data/runtime/node4/claude-a/`，最终写`report.json`，status=`submitted_for_review`或`blocked`。遇到缺少上游配置时如实报告，不偷偷改用其他模型或让Codex代写。完成A后停止等待验收。
