# 发布缓冲内存修复

仅修改 `catalog_index.py` 与新增 `tests/test_catalog_index_memory.py`；没有改 SourceVerifier、evidence、五冻结适配器或 v3 的12份依赖，没有采用或发布正式数据。

## 已复现的问题

`CatalogIndex.sync` 同时保留所有程序、文档、历史的 JSON 字符串，然后在仍持有程序副本时重放来源上下文。真实HOT发布在这个重叠阶段读取ZJU界面脚本时抛出MemoryError，SQLite尚未发布。

合成回归先失败：64条记录共用256KiB输入notes，输入在tracemalloc启动前分配。旧实现程序额外峰值51,182,109字节（其中来源重放临时缓冲6MiB）、文档54,053,254、历史17,183,584，超过8MiB边界。不是依靠机器RSS/可用内存阈值的测试。

## 变更

- 按原门禁计算来源关系，置于大JSON序列化之前，不省略公开表单或其他来源重放。
- 单位、程序、文档、历史及科目边交给SQLite惰性迭代；没有一次保留整个序列化目录。科目桶仍按相同作用域与去重规则计算，但在payload写完、来源投影释放后才构建；桶payload也逐条编码。
- 历史数据仍在same-revision早退前逐条检查可JSON编码，但不保存编码副本。
- 生成器在事务中发生编码错误、MemoryError等异常时显式回滚并传播原异常；SQL错误仍按原公开错误契约回滚。没有吞掉MemoryError。

## 验证

- RED：7项中3个内存断言失败；其他原子性/兼容断言保留。
- GREEN：9项新回归；连同catalog_index、published_service、publish_catalog、evidence、公开表单等消费者，共196项通过。
- 相同合成输入额外峰值：程序6,318,343字节、文档4,482,907、历史664,827。首项包含6MiB来源临时缓冲；三个值均低于8MiB。
- 程序/文档中途编码失败、程序中途MemoryError、SQL trigger中途失败、来源重放MemoryError，均保持旧已发布版本与旧记录。原文/输入未改。
- CatalogIndex语句425/435；纯分支180/202=89.11%。五冻结SHA及v3的12份依赖逐字不变，复合摘要仍`07b0afe0b2d5eb34770295b8dcbbb015bec7f5b324a167f67049025a9ef701e3`。

原始日志：`red-tests.txt`、`green-tests.txt`、`consumer-tests.txt`、`memory-peaks.json`、`coverage.json`、`code-and-coverage.json`。

这些证明序列化缓冲峰值与事务回退；**不代替真实22,630条全库发布和冷恢复**。全库是否不再内存失败，仍须由root在受控HOT/COLD验证目录使用最终代码重放。没有启动第二个全库发布，没有修改演示服务或正式公共包。
