# 研序 AI · README 封面生成记录

- 文件：[`cover-yanxu-ai.png`](cover-yanxu-ai.png)
- 生成工具：内置 `image_gen.imagegen`，默认工具模式；没有使用项目或本机 API Key。
- 类型：项目主题视觉封面；系统真实运行截图单独保存在 `screenshots/` 子目录。
- 尺寸：1774 × 887，2:1；PNG、RGB。
- 生成日期：2026-11-03。
- 当前版：主题封面生成后，按用户确认用同一内置工具将副标题改为“你的考研推荐 Agent”；保留构图、配色与功能标签。

## 首次生成提示词

```text
Use case: ads-marketing.
Asset type: premium GitHub README hero, a wide landscape banner with an approximately 2:1 aspect ratio.
Primary request: Create a beautifully art-directed cover for “研序 AI”, an AI agent for Chinese postgraduate school and major research. The product turns scattered official admission documents into evidence-backed, explainable choices, using Yuanbao search, algorithms and an accumulating source knowledge library.
Scene/backdrop: clean cool-white architectural studio with subtle pale cobalt light and plenty of intentional negative space.
Subject: a single coherent sculptural composition: a refined miniature university-like knowledge architecture formed from translucent glass and white ceramic document planes, joined by a few delicate cobalt branching decision paths and precise knowledge nodes. A restrained ascending comparison/trend line is integrated into the sculpture. Convey orderly research and verified sources, not generic AI magic.
Style/medium: exceptionally polished editorial branding meets restrained architectural 3D still life, precise soft shadows, crisp glass edges, matte paper textures, premium tech publication quality.
Composition/framing: wide designed cover. Large perfectly legible editorial typography balances the visual sculpture. Strong grid, generous margins, calm hierarchy. Avoid a pile of UI cards. Keep all text away from edges.
Lighting/mood: sophisticated daylight, calm clarity, optimistic intelligence, rigorous and welcoming.
Color palette: cool white, charcoal-black typography, cobalt blue accents; very subtle ice-blue gradients.
Text (verbatim, Chinese must be exact): Main headline “研序 AI”. Subheadline “你的考研研究 Agent”. Three restrained feature labels “元宝搜索”, “可解释择校”, “可溯源知识库”. A small elegant English brand label may read “YANXU AI”. Do not add any other text.
Constraints: text is large enough to read at GitHub README width. This is a concept hero, not a screenshot: no real-looking application dashboard, no fabricated universities, no student personal data, no admission probabilities, no fake data numbers. Yuanbao is represented by the exact text “元宝搜索”; do not invent official Tencent logos, partnerships or award badges.
Avoid: generic robot heads, human mascots, neon cyberpunk, dark space backgrounds, purple gradients, crowded floating cards, stock marketing clipart, graduation-cap clichés, excessive tiny text, watermark, decorative noise.
Output intent: a genuinely premium, refined, visually memorable open-source project cover ready to lead a high-quality README.
```

## 检查记录

已核对主标题“研序 AI”、副标题“你的考研推荐 Agent”和“元宝搜索 / 可解释择校 / 可溯源知识库”三项功能文字。采用冷白、炭黑与钴蓝，文档、院校建筑、分支路径及知识库组合为统一主体，不展示虚构学校、录取分数或概率。实际界面截图与封面分别维护。


## 当前版副标题编辑

使用内置 `image_gen.imagegen`，通过 `referenced_image_paths` 编辑现有封面，`transparent_background=false`；没有使用项目服务凭据，没有进行程序修图。最终仍为 1774 × 887、2:1、RGB。

```text
Use case: text-localization. Edit the attached existing README cover. Change only the subtitle text from “你的考研研究 Agent” to the exact text “你的考研推荐 Agent”. Preserve every other visual element as faithfully as possible: main headline “研序 AI”, small “YANXU AI” label, three feature labels “元宝搜索”, “可解释择校”, “可溯源知识库”, their typefaces and placement, all colors, lighting, spacing, university architecture, glass platform and shelves, documents, branching knowledge paths, trend line, shadows and background. The subtitle must keep the same typography, weight, size, color, baseline and position; only replace the two Chinese characters 研究 with 推荐. Keep the original wide 2:1 composition and preferably the exact 1774×887 canvas. Do not crop, zoom, redesign, embellish or move anything. No new text, labels, logos, watermark or decorative elements. Opaque background, preserve the original image quality. Exact final subtitle: “你的考研推荐 Agent”.
```

最终 PNG SHA-256：`eee55c896c871e0bc474f272ccd427a6f55c6e0aca94123b73165bf815c5c13c`。已视觉核对副标题、主标题与三项功能标签。
