---
name: compare-plans
description: Use deterministic admission and subject-planning algorithms on retrieved verified candidates.
---

# 比较备考方案

用compare_candidates执行资格三态、科目身份与备选套组匹配、偏好排序和Pareto比较。用plan_subject_routes比较0/1/2个考试单元调整；必要时suggest_adjustments显式列出有限的地区、年费或换科情景，只作试算，不修改个人条件。

算法仅处理本轮已发现的已发布候选，来源不明、年份不符或科目标识待核不得混入。不同学校同码自命题不能互认；201与204、301与302不能自动等同。只准备了408不代表其他三科已经准备好。

费用未知不能当免费，学制未知不能默认三年，未知资格不能视为符合。排序是已知偏好下的取舍，不是录取概率或保录结论。解释每项建议的代价、未知项与限定候选范围。

需要追问时优先参考算法questions中的affected_count。不要将手写条件判断或有限样本包装成已训练的录取预测模型。

每次候选或已确认profile变化后，运行时会自动执行真实compare_candidates核算；这些程序调用单独记录algorithm_calls，不冒充模型工具选择或录取预测。预算停止前已取得的候选仍会保留实际比较结果与关键待答问题。

只有已准备科目覆盖候选所需考试单元时，程序才自动生成路线比较。缺失政治、外语或专业课时明确请求补充，不自动填入其他科目，也不把未知显示为零换科。未获授权的预算、地区或学历条件不得放宽。
