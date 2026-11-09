# 官方入口发现验收：2026-11-09

本轮覆盖72个招生单位案例。计分调用生产 `ResearchTools.run('discover_official_entry', ...)`，经过归属核验、导航、年度文种判断、正文解析和临时知识库原文保存。**正式渠道可确认不等于目标正文已读，更不等于招生事实已经采用。**

| 样本 | 数量 | 正式入口/渠道 | 2027目标记录 | 实质正文 | 原文保存且质量通过 |
|---|---:|---:|---:|---:|---:|
| 原32所失败单位 | 32 | 32 | 13 | 7 | 7 |
| 31省级地区40所留出样本 | 40 | 40 | 18 | 15 | 15 |

32所做了冻结源码后的公开HTTP读取，再用原始响应重放；40所从原登记主页开始，使用此前同批公开HTTP响应重放。后者存在修复后新路径未录制的缺口，不是全国官网发现率，也不能把 `recording_missing` 解释为学校网站失败。两份最终重放报告均保存开始/结束源码SHA，必须 `source_changed_during_run=false`。

| 32所的起点 | 单位数 | 2027目标记录 | 实质正文及合格原文保存 |
|---|---:|---:|---:|
| 原有登记入口 | 9 | 8 | 4 |
| 已留档研招网本校账号声明 | 20 | 3 | 2 |
| 本轮人工审校后补正的3个入口 | 3 | 2 | 1 |

20个留档研招网本校账号是可核对的正式发布渠道，**不能说成20个独立学校官网均已自主发现**。复旦大学、青海师范大学、山西中医药大学的入口由本轮人工核对主站原件后补进 `unit_bindings.CURATED`，其贡献单列，不归入自主发现提升。原件、实际链接、观察时间和SHA见 [人工审校入口清单](../tests/fixtures/official_discovery/reviewed_roots/manifest.json)。复旦2025年主站声明只证明入口归属，不证明2027政策。

正文要求年度和文种相符，排除预计发布、占位、本科、博士、推免及推荐免试；网页核对原始主标题，剔除菜单、上下篇和附件列表后须有实质政策段落。嵌入PDF、验证码、仅附件标题、旧年度与无年度材料不升级为正文通过。完整证据还须匹配实际原始字节SHA和日期，且临时知识库返回 `knowledge_id`、`original_available=true` 与 `quality.state=accepted`。

原32所人工复核的25份2027发布记录、5份历史/院系材料、2份目标未读状态保存在 [案例清单](../tests/fixtures/official_discovery/cases32.json)，未统一改写为全文通过。本轮长沙学院读到另一份2027官网正文，保留原2026参考记录，未将旧简章改标2027。

尚有真实边界：湖南中医药大学PDF文字提取质量不足、川北医学院附件入口受验证码约束、重庆文理学院大附件超出读取上限、中国地质大学（武汉）外部PDF发布域需要精确附件归属证据，以及部分入口只保留了本校账号或归属待核状态。失败按网络访问、归属证据、预算、原文解析和录制缺口分别保留；不把这些结果解释为学校没有官网或没有招生。

独立验收模块27个测试通过，语句覆盖率 91.7%。测试含完整BISU主页/官方脚本/常规章程与推免竞争页面、CUG真实路由、缺年度、历史材料、HTML列表误判、原文落库、响应截断、SHA篡改和路径越界。测试知识库用临时目录并在结束后清理，业务知识库及招生数据未写入。

无付费元宝/模型调用、无密钥读取、无新研招网请求、无真实4180服务和用户数据库访问。公开读取使用4个工作线程、同主机至少2秒间隔；每单位受生产导航数量和时间预算约束。本轮请求2027章程，不代表全部专业目录和2028报考政策已经核验。

简明机器结果及每校状态、来源URL、SHA与知识条目ID见 [acceptance-summary.json](../tests/fixtures/official_discovery/acceptance-summary.json)。完整公开HTTP录制与过程报告保留在 `D:/CodexRuntime/yanxu-closure-upgrade-20261109/coverage/`，未将全部录制复制进仓库。

重放命令（无网络后备）：

```powershell
python -X utf8 tools/evaluate_official_discovery.py --out D:/CodexRuntime/yanxu-closure-upgrade-20261109/coverage/reproduce32 --cohort regression32 --heldout 0 --workers 4 --interval 2 --replay D:/CodexRuntime/yanxu-closure-upgrade-20261109/coverage/live-regression32-reviewed-final
python -X utf8 tools/evaluate_official_discovery.py --out D:/CodexRuntime/yanxu-closure-upgrade-20261109/coverage/reproduce40 --cohort heldout_registered --heldout 40 --workers 4 --interval 2 --replay D:/CodexRuntime/yanxu-closure-upgrade-20261109/coverage/live-heldout40
python -X utf8 -m unittest tests.test_official_discovery_evaluation -v
```
