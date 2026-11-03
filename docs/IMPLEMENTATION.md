# 研序：正式开发基线

用户于2026-10-28确认V2前端并授权正式开发。保留冷白、炭黑、钴蓝和独立页面，修复超高屏页脚以下空白。旧权益雷达、NoticeAction、旧服务器与密钥均不修改。

## 当前交付边界

建立真实来源的本地应用、可复核算法、全国索引、年度单位核准、采集解析与完整性验收。任何不足以支撑全国完整目录的采集结果必须保持发布门槛关闭，不用演示记录补齐。2028未发布目录只允许2027历史参考。前端原型单独保留。

2027年度名单核准931个独立硕士招生单位，通用索引仍保留936条。5条未列入当年独立名单的记录不据此推断停招。年度名单、资料下载、专业结构化与全校目录完整性分别统计，不能用原文文件数或成功请求数替代目录覆盖。

## 共享数据契约

沿用 PRODUCT_CONTRACT.md 的字段定义与算法返回；以下补充优先。资料库 data/units.json 和 data/admissions.json，真实快照 data/snapshots/，禁止正式资料库出现虚构院校或合成招生数字。

admissions.metadata 必须含 reference_year、target_year、catalog_complete(false默认)、unit_catalog_status数组。每项 {unit_id,year,status,expected_records,parsed_records,verified_records,notes}；status verified/partial/needs_review/not_found/restricted/not_applicable。完整目录要求原始记录数与解析记录对账、全部考试科目与可选组合可追溯；仅把已发现网页下载完不等于verified。全国发布门槛须索引完整、适用单位逐一核验且关键字段无缺口。

程序保留 subjects [{code,name,scope,exam_unit?,choice_group?,evidence?}]。exam_unit为原始考试单元1..4，OR备选必须在同一考试单元内分组，不能扁平数组错位。没有备选的旧顺序数组允许按顺序兼容。全国统考scope=national必须来自已核实统考身份；自命题scope=unit_id，不按编号大小推断。用户的subjects不得用同名或跨校相同代码替代身份，201与204、301与302不自动互认。未解析的实质条件统一由 requires_review=true 标记。

score_lines仅代表对应层级的复试线。录取初试区间/中位数等使用独立admitted_statistics（真实年度、admission_source招生类别、学习方式、计划类型、样本完整度），无名单则缺失，不用复试线模拟录取分布。subject_scores保留初试各单科统计，retest与weighted_final分开。可选data/history.json按id合并历史数据；采集失败或表结构改变不得覆盖已有有效资料。release_ready由service逐单位对原文重新解析核验，不直接信任metadata.catalog_complete、expected_records等自述数字。资格条件完整性与目录完整性独立统计。

PDF坐标表格适配器 `pdf_table_v1/v2` 另须通过根审核文件 `data/catalog_semantic_audits.json` 的独立语义门槛：原PDF、适配器代码和完整记录集摘要同时一致，批准唯一且有效，才能入库或发布。文档自身的“已审核”字段不能授权采用。已发现跨院系或跨页误读的候选继续隔离；原文重放与同一解析器复算一致不能替代独立语义审校。

科目代码与名称矛盾或原标识待核时，保留原值、原文和官方链接，记录 `subject_identity_issues`，设置 `exam_matching_supported=false`。目录身份可追溯与考试身份可计算分别判断：待核记录可供查看，但不能进入已准备科目选择、最小换科、排序、路线和最小调整；返回被排除方向及原因，不以零换科掩盖未知。推免专用、单独考试、强军等须按公开范围排除，不能套普通统考条件。

用户预算按max_annual_tuition（每年）与max_total_tuition（全程）区分；来源只有总额时，不在未知学制下推算年费。eligibility_evidence_complete必须有显式true才能认定资格已完整核验，目录本身不能代替招生简章与院系条件。公开公示的“未标专项”不能直接等同普通计划，未确认一志愿或调剂时admission_source=not_specified。

2027年度全国统一命题科目名称以[教育部2027年招生工作管理规定第三十四条](https://www.moe.gov.cn/srcsite/A15/moe_778/s3261/202609/t20260923_1451734.html)为依据，与学校目录中的代码和完整名称交叉核对。现有目录适配器仅采2027目录；扩大历史年份或新增学校时仍需核对该校该年的全国统考选用公告，不将当前词典当作所有年度自命题/统考身份的通用证明。

## 算法

grad_engine.py: validate_profile, match_programs, compare_score_lines, match_historical_rules。资格采用明确匹配/明确冲突/未知三态；从全量传入的有效年度项目筛选，而非搜索TopK；科目按有序考试单元与OR备选组比较；缺用户科目不声称零换科；多目标偏好与Pareto解释不输出录取概率；至少提供敏感性/条件试算辅助函数。历史复试调剂逐年度核验，录取/复试线口径不混用。

## HTTP服务

stdlib ThreadingHTTPServer，本机默认127.0.0.1:4180，静态目录static。service.py负责存储/覆盖/算法调用，server.py负责HTTP边界。不引入旧应用配置。所有API统一信封 {ok,data,error}，错误error {code,message}，成功error=null。

- GET /api/bootstrap: data {meta,coverage,units,programs,score_lines,retest_rules,transfer_rules,notices,service}。
- GET /api/subjects?unit_id=&year=&q=: data {subjects,total,coverage}；每条 {code,name,scope,unit_name,year,program_ids}。同code不同scope不合并。未收录和不存在严格区分。
- GET /api/programs?q=&unit_id=&year=: data {programs,total,coverage}，仅真实入库记录。
- POST /api/match {profile,unit_ids?}: 返回引擎对象加coverage/release_ready；全国完整性未达标时不得宣称全量推荐。指定范围只允许明确注明范围的研究预览，所有项目仍是参考年度规则。
- POST /api/sensitivity {profile,unit_ids?,scenarios:[{name,changes}]}: 按用户明确调整的地区、预算、备考科目或偏好重新计算；返回逐场景增加/移除/保留的项目，不擅自放宽学历、工作年限等硬条件。
- POST /api/history/match {kind,year,facts}: 引擎结果加出处。
- POST /api/search {unit_id,year,kind}: 公共平台元宝检索；固定 https://api.wsa.cloud.tencent.com/SearchPro，平台Key只读GRAD_SEARCH_API_KEY，不借旧项目Key。无Key明确not_configured，不伪造结果。
- GET /api/health: 仅非敏感健康状态。

扩充资料后前端使用明确发布的SQLite查询版本，旧接口保留兼容：

- GET /api/catalog-bootstrap: 轻量单位、覆盖、`revision` 与 `published_at`；不含全量专业。
- GET /api/catalog-programs?unit_id=&year=&q=&offset=&limit=&revision=: 服务端分页目录，前端每页6条。
- GET /api/catalog-program?program_id=&revision=: 单方向与相关资料。
- GET /api/catalog-subjects?unit_id=&year=&q=&code=&scope=&exam_unit=&offset=&limit=&revision=: 按考试单元的科目聚合，排除身份待核记录。
- GET /api/catalog-sources?unit_id=&year=&kind=&q=&offset=&limit=&revision=: 已发布来源，每页12条。
- GET /api/collection-status?unit_id=&q=&status=&page=&page_size=: 各单位的年度身份、目录、读取及解析状态、缺口与官方原文链接。

`python -m collectors.publish_catalog --data-dir data` 独立重放来源、年度名单和语义批准，再事务写入 `data/runtime/catalog-index.sqlite`；失败不替换旧版本。运行时不自动采用改过的JSON。发布查询标为 `verification_mode=published_snapshot`，不是每次实时重新核验全国原文；原文与字段依据另按当前内容检查。版本变化使旧分页返回409，避免跨版本拼接。列表分页不改变算法范围，算法仍计算明确选中学校的全部有效已收录方向。

字段抽屉的现场核验限定所选方向的相关原文，不冒充整校重新对账。来源关联从发布核验后的专业字段引文派生，不仅凭单位和年份挂靠全部方向；关联索引不改写原文SHA-256。SQLite格式版本参与发布版本身份，格式升级要求重建并发布，避免旧索引继续提供缺失关联。

前端 data.mjs 不得继续作为生产数据源；通过轻量启动和分页接口适配真实资料。正式UI不可造min/max/median/count，仅有复试线时按复试线显示。空资料应说明采集/核验状态并链接官方来源，不用‘无资格’代替‘数据缺失’。平台检索状态与更新时间可见；普通查询与规则筛选不需要用户Key。额外AI问答Key入口只在用户主动使用时出现，此轮不伪造模型输出。

全国公开来源采集保留断点、逐域节奏与有限重试。研招网触发频控后共享 `data/runtime/chsi-host-gate.json` 阻止新请求，不用清除状态、切换身份或绕过登录续采；离线快照仍可重放。公共详情采集器默认禁用网络，只有正常公开路径、单位和年度上下文匹配的真实详情才能采用。结构化原文保留原字段路径和值，不能把生成的摘要冒充逐字引文。

## 验证与运行

仅当前新项目文件，TDD使用unittest/Node内置断言与真实浏览器。Python通过coverage运行，目标>=80%；旧XLS依赖隔离安装到 `.runtime-deps/`，PDF原文工具和坐标Poppler分别配置，缺失时明确报依赖不可用，不改动全局工具以掩盖引用差异。每种提取器有真实快照与独立测试夹具，网络结果不可当作验证通过。根负责最终跨模块与浏览器检查，使用新端口不替换4173/4182的原型。实际测试数量、覆盖率和未验边界统一见 VERIFICATION.md。
