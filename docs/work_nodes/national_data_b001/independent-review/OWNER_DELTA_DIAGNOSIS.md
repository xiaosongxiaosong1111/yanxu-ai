# Own-entry delta: six lexical false rejections

Scope: read-only diagnosis of the actual saved public originals referenced by `entry-delta.json`. No production edits, network calls or runtime/Key access.

All six failures come from `_nearest_entity` treating an entire Chinese prose prefix ending in an institutional word as the institution name. `known_names` and raw-original SHA checks pass. This is not a missing-source or source-identity problem.

| Unit | Prefix immediately before selected role | Current wrongly inferred entity |
| --- | --- | --- |
| 北京师范大学 / 367859 | `推免生接收、报名、初试成绩、分数线、复试安排、拟录取名单等均可在北京师范大学` | `拟录取名单等均可在北京师范大学` |
| 北京市市政工程研究院 / 367853 | `复试参考书目将在北京市市政工程研究院` | `复试参考书目将在北京市市政工程研究院` |
| 大连民族大学 / 302981182 | `八、信息公开公示 学校招生章程、招生政策和规定、招生专业目录和分专业招生计划等招考信息，均在大连民族大学` | `均在大连民族大学` |
| 上海交通大学 / 368129 | `见上海交通大学` | `见上海交通大学` |
| 衢州学院 / 3274414232 | `自命题科目的命题工作由我校组织，相关考试大纲及参考书目将在学校` | `相关考试大纲及参考书目将在学校` |
| 西安交通大学 / 368554 | `考生可登录教育部“中国研究生招生信息网”（https：//yz.chsi.com.cn）及学校` | `及学校` |

## Common grammar correction

- Recognise the institution immediately attached to the role, allowing only explicit own identity (plus defined graduate-department suffixes) and clear leading syntax boundaries.
- Delimit access/presentation predicates, including login/visit verbs and published-location phrases such as 将在/均在/均可在/可在; a sentence-initial 见 is an instruction, not part of a university name.
- Strip leading conjunctions such as 及/和 before testing a standalone generic 学校. Do not globally split arbitrary name characters: 北京协和医学院 must remain one full institution.
- An earlier own name must never override a different nearer explicit entity. Preserve the foreign-owner and shared-university-suffix RED/GREEN controls; do not restore longest-common-substring ownership or per-school allowlists.

The exact current-unit name may be accepted at the suffix only with an empty/punctuation/known grammatical left boundary. Unconditional endswith would incorrectly treat an unknown longer institution such as 东北京师范大学 as 北京师范大学.

## Hubei Automotive follow-up

The actual failed candidate is the later contact section with `网 址:` (space inside the role). Its candidate is not the earlier full statement `学校研究生院官网(网址：http://... )公示`; that first occurrence never reached role evaluation because of the separate URL-plus-Chinese-prose token issue being diagnosed by the HTML worker. Restoring the first full explicit statement is sufficient for this delta; broadening contact-label grammar is optional and should be constrained by the clear institution contact heading if implemented.

Full input identities, original SHA and trace records: [owner-delta-analysis.json](owner-delta-analysis.json). This diagnosis does not modify proof states to maintain a target count.
