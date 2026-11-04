# N4A 第二轮独立验收反馈

继续自己的A会话，仅修本反馈，不进入B/C。原分工/范围/环境/隐私约束不变；Codex不实现。你已真实交付review1，独立验收92项全过，4个实质边界缺陷由独立探针复核均修复。完整Python余11项为后续B/C草稿，Node191通过。主工程未合入。

仍需修复3项（先业务RED，再GREEN）：

1. **P2 超大JSON整数向量**：合法JSON数值`10**1000`出现在embedding分量时，`_finish`的`math.isfinite`抛原始OverflowError。须统一为安全受控TransportError，不能让异常越出连接/构建边界。保留已有有限极大/极小float正常归一化与bool/NaN/Inf/零拒绝，不能把一般大float全拒绝规避。
2. **无动态目录的manual合同**：Dashscope preset `models_path=None`，models()不应调用wire或抛invalid_request，而应返回明确manual/手填模型结果（沿既有AI无目录交互契约），能力仍以实际Embedding测试为准。不要伪造远程读取或鉴权成功。
3. **P2 Ollama目录路径和JSON形状**：用既有fake _PinnedConnection经过真实安全HTTP层实测，当前GET最终到`/v1/api/tags`，给官方`models:[{name,model}]`形状后解析空列表却写remote。正确fixed `/api/tags`位于服务根（保留允许的origin/已有安全loopback政策），解析候选的name/model并严格校验、去重。不能放开任意path或重定向/SSRF约束。官方现查`https://docs.ollama.com/api/tags`为GET根路径并返回models数组；`https://docs.ollama.com/api/openai-compatibility`说明OpenAI兼容接口仍为/v1/embeddings。不要访问本机Ollama或做真实远端调用，用fake合同即可。

证据：`data/runtime/node4/independent-a/review1-probe.json/log`、`catalog_probe.py`、`catalog-probe.json/log`及review1-sha-before/after.json。不要修改独立探针以绕过缺陷。

当前A无需扩展prefix高级UI，后续页面只暴露实际可保存的配置；保留底层prefix/input_sha契约。也无需重跑整库1662和191一遍：定向A+旧AI/元宝/设置（上一轮92）及新增回归，重测相关六模块覆盖>=80%，必要安全HTTP合同与离线本机模型即可；本轮源码变化没有涉及的全套已有结果保留时间与SHA说明。全N4结束统一完整回归。原公共分数/来源18项独立重跑也已通过，环境性假失败不得改业务/绑定适配器。

修复后更新`data/runtime/node4/claude-a/report.json`与实际日志/命令/覆盖，纠正review1目录失败旧夹具归因（custom的models_path覆盖不是实际SiliconFlow失败）；status submitted_for_review。对外合同保持后续B可用。提交报告后停止等独立验收。
