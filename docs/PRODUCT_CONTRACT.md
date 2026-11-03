# 考研招生决策台：首版共享契约

确认范围：2028入学择校；择校/专业筛选、最少换科、通知跟踪、历年线、历史复试/调剂匹配。公共采集平台承担；额外AI问答为后续范围，当前未接通。全国索引与已解析专业覆盖分开。2028未发布资料不得从2027补写。旧项目/服务器不改。本文件是首版数据契约；实现补充见[高级功能](V3_PLAN.md)及[原文依据与图表](V4_PLAN.md)，后者对来源及可视化的要求优先。

## 文件和数据

- `data/units.json`：对象，`metadata` 与 `units`。metadata含`source_url`、`checked_at`、`expected_pages`、`fetched_pages`、`failed_pages`、`complete`（仅指该公开索引本轮分页采集）、`notes`。unit含`id`(字符串)、`name`、`province`、`source_url`、`official_url`(可空)、`checked_at`。不得把索引complete解释为全国专业已收齐。
- `data/admissions.json`：对象，`metadata`、`documents`、`programs`、`score_lines`、`retest_rules`、`transfer_rules`、`notices`。各数组缺资料为空，禁止演示假记录。
- `document`：`id,unit_id,title,url,year,kind,published_at,checked_at,sha256,local_path,status,notes`。kind如catalog/charter/fees/duration/campus/score/retest/transfer/change。status verified/parsed/needs_review/unavailable。
- `evidence`：`document_id,url,quote,page`（页码可空），字段来自原文件，不把搜索片段当完整原文。
- `program`：`id,unit_id,unit_name,year,department,code,name,direction,study_mode,degree_type,province,campus,subjects,tuition,duration_years,conditions,evidence,notes`。subjects是`[{code,name,scope}]`，scope national 或具体招生单位id；不把同名自命题视为全国同科。tuition为null或`{amount,period,total,notes,evidence}`，period year/total/unknown；total不能在学制未知时瞎算。
- 原文未列院系时允许 `department="", department_scope="unit"`；原文未单列方向时允许 `direction="", direction_scope="program"`。必须由同一原文解析重放证明，不能用此标记掩盖解析失败。原文一级学科为4位代码时保留代码并标记 `code_level="discipline"`，不补造末尾00。
- `conditions`：可执行的已核对条件`[{field,op,value,label,evidence}]`；field限 education/background/work_years/study_mode等真实明确字段。不能识别的要求保存在notes并导致待核对，不能假造结构化限制。
- `score_line`：`id,unit_id,unit_name,year,kind,program_code,program_name,study_mode,total,politics,foreign_language,business1,business2,evidence,notes`；kind national/university/department/program。分数缺失为null。各科门数不固定，专项类型不合并。拟录取最低分不当复试线。
- 学校门类线补充`discipline_code,degree_type,code_prefixes,plan_type,full_score,column_order`及适用说明；原文未单列学习方式、满分或具体专业时保持未知。`column_order`记录视觉核对的原表列序，数值行与表头各自保存精确引文。学校基本线不等于学院最终线。
- 运行时为历史统计及复试线附加`evidence_status/evidence_note`。仅`verified/derived`可绘确定数据点，原文失效或统计不符留为待核。图表中的`referenceScoreLines`独立于已明确可适用的专业线，不进入资格或成绩条件判断。
- `retest_rule`/`transfer_rule`：`id,unit_id,unit_name,year,title,program_codes,requirements,evidence,notes`，requirements同conditions。公开原文不足以判定时`notes`说明，匹配为unknown。历史调剂不表示当前有名额。
- `notice`：`id,unit_id,unit_name,year,title,kind,published_at,url,summary,affected_program_codes,evidence`。拟调整与正式目录分开，未判明替代关系不得直接覆盖。

## 算法接口（grad_engine.py）

- `validate_profile(value)` -> 新字典，拒绝未知/敏感字段、错误类型；允许`target_year,reference_year,education,background,work_years,study_mode,max_total_tuition,provinces,subjects,max_subject_changes,preferences`。subjects与program结构相同。education bachelor/master/associate/unknown。preferences `cost,subjects,location`各0..5。默认target2028/ref2027；未公开年份只作历史参考。
- `match_programs(programs,profile)` -> `{matches,excluded,needs_confirmation,questions,summary}`。每项为`{program,status,reasons,missing,subject_changes,rank_score,pareto}`。status matched/excluded/unknown仅对reference_year已知规则；不得把年份不符的记录加入；须输出historical_reference=true。未知不当false/0。subject_changes给`added,removed,count,uncertain`，同代码不同scope不等同；缺少用户科目不当0新增。questions只询问会改变当前结果的条件，含`field,label,affected_count`。summary含input_count,matched_count,excluded_count,unknown_count,target_year,reference_year,historical_reference。
- `compare_score_lines(lines,unit_ids=None)` -> 新列表，保留year/kind/科目门数与缺失；不输出录取概率。
- `match_historical_rules(rules,facts,year)` -> `{matched,excluded,needs_confirmation,year,historical_reference:true}`。facts明确输入，保留证据，未知不当通过；notes中未解析条件不能默默忽略。

## HTTP和前端

Python仅127.0.0.1:4180，原生静态HTML/CSS/JS，无旧服务共享依赖。GET `/api/bootstrap` 返回 `{meta,coverage,units,programs,score_lines,retest_rules,transfer_rules,notices,service}`；meta target_year=2028,reference_year=2027,score_years，免责声明和阶段。coverage含index_total,program_units,program_count,document_count,registry_complete,missing_units_count,document_kinds及实际缺口。service含search_configured和不含密钥的状态。

POST `/api/match` `{profile,unit_ids?}`，返回算法对象。POST `/api/history/match` `{kind:'retest'|'transfer',year,facts}`。导出在浏览器从当前候选生成文件；没有`GET /api/export`服务接口。

POST `/api/search` `{unit_id,year,kind}`：公开学校名+资料类型检索，平台Key只读`GRAD_SEARCH_API_KEY`，不借旧Key。不配置返回明确not_configured。只回显脱敏请求元数据，固定官方API地址。

GET `/api/evidence?program_id=`提供字段级claims，含原文、页码、官方链接、范围及核验状态。同源文件去重、引用链与支持关系分开；明确不适用材料不计为直接支持。匿名统计必须由原表重放，程序解释文字不得作为官方逐字引文。GET `/api/sources?unit_id=&year=&kind=&q=`提供已收录来源总览及关联字段，路径及逐人信息不外露。

POST `/api/chat`当前只返回`not_configured`，没有接收用户Key的问答界面。后续若接入须先确认官方模型接口、费用与临时Key生命周期，不能据本契约宣称已经完成。主筛选不需Key。

页面主导航为「院校专业」「我的方案」；分数、复试与调剂在专业档案内，数据覆盖独立按需展开。界面显示2028规划/2027参考、来源和缺口。公共目录不等于已满足2028资格。不得宣称公开多人隔离或已跑通真实元宝。用户选项已确认，普通实现可自决。

## 验证

TDD：先失败再实现。Python覆盖≥80%，真实Chrome临时数据回归，320/390/768/1440无横向溢出。真实官方快照和测试夹具分开。未调用真实API要如实标明。未经后续授权不部署新站或写旧仓库。
