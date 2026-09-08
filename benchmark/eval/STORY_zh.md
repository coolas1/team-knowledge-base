---
name: story-zh
title: "Wren Adachi 个人档案（合成语料）— 中文概览"
description: STORY.md 的中文翻译，面向人类维护者。非评测来源；预测代理不得使用本文件。
type: translation
created: 2026-07-09
tags: [personal, persona, synthetic-corpus, multilingual, translation-zh, human-facing]
---

# Wren Adachi — 个人档案（中文概览）

> **本文件是 `eval/STORY.md` 的中文翻译，面向人类维护者阅读。它不是评测数据来源——
> 预测代理（prediction agents）不得将本文件作为答题依据。** 语料本身是合成的，
> 仅为多模态 RAG 基准测试而构造。

## Wren 是谁？

**Wren Adachi** 博士（34 岁）是加州大学圣克鲁斯分校海洋科学研究所的海洋生物学家。
她的档案贯穿三条主线：

| 主线 | 产出 | 枢纽文件 |
|---|---|---|
| 珊瑚礁研究 | 学术论文、多工作表 Excel、潜点 CSV、图表 | research/coral-resilience-paper.md |
| 氛围音乐（"Polyp"） | 专辑封面、BPM/调性 CSV、田野录音转写 | music/polyp-tidal-ep-notes.md |
| 发酵爱好 | 食谱、批次记录 Excel、pH 时序 CSV | fermentation/recipes.md |

串联三者的，是 **2024 年春季（3–4 月）为期六周的冲绳学术休假**：为工作做珊瑚调查、
为下一张 EP 做田野录音、并深入泡盛与麹的传统。详见 travel/okinawa-2024/blog.md。

**Wren 同时使用三种语言工作。** 她正在学习日语和普通话，部分田野笔记与日记用日语写成；
她与 **厦门大学的林伟博士** 合作研究南海珊瑚白化；她还把 **中国红曲（红曲米）发酵**
与日本麹工艺放在一起探索。三语术语表 notes/multilingual-glossary.md 是多语言枢纽。

## 语料为何存在

基准语料需要：跨格式的真实引用、非平凡的图（22 个 Markdown 节点加附件，边数充足）、
格式多样性（Markdown 22、Excel 3、CSV 10、PDF 3、生成 PNG 3、下载 JPG 7），以及
**多语言覆盖**——日文与中文内容贯穿每条主线，部分事实仅以非英文存在，从而真正测试
跨语言检索。

## 建议测试查询（示例）

- "林伟的南海调查报告的白化临界值是多少？" → scs-bleaching-survey-zh.pdf + south-china-sea-coral-zh.md
- "Wren 如何用英、日、中三种语言表达'珊瑚白化'？" → notes/multilingual-glossary.md
- "Tidal EP 日版封面的日语标题是什么，含义为何？" → music/tidal-ep-cover-ja.png + tidal-ep-liner-notes-ja.md
- "Wren 的红曲工艺与麹工艺有何不同？" → fermentation/hongqu-notes-zh.md + recipes.md

## 文件清单（按主线，含多语言文件）

- **研究**：coral-resilience-paper.md / .pdf、lab-notebook.md、bleaching-survey-2024.xlsx、
  dive-sites.csv、figures/temp-vs-bleach.png；中文：south-china-sea-coral-zh.md、
  scs-bleaching-survey-zh.pdf、scs-bleaching-2024-zh.xlsx、scs-dive-sites-zh.csv、
  photos/scs-reef-zh.jpg；日文：sesoko-field-notes-ja.md / .pdf、dive-sites-ja.csv。
- **音乐**：polyp-tidal-ep-notes.md、field-recordings-okinawa.md、setlist-2024-04-12.md、
  album-cover.png、tracklist.csv；日文：tidal-ep-liner-notes-ja.md、tidal-ep-cover-ja.png、
  tracklist-ja.csv。
- **发酵**：recipes.md、batch-log.xlsx、ph-readings.csv、photos/scoby-jar.jpg；
  中文：hongqu-notes-zh.md、hongqu-ph-readings-zh.csv、photos/hongqu-rice-zh.jpg；
  日文：awamori-notes-ja.md、awamori-batch-ja.csv。
- **旅行**：travel/okinawa-2024/（blog.md、itinerary.md、expenses.csv、多张照片）。
- **笔记**：ideas.md（最密集的枢纽）、reading-list.md、finance-2024.csv；
  多语言枢纽：multilingual-glossary.md（英/日/中）、mandarin-study-zh.md（中文）。
- **日记**：2023-11-15、2024-03-01、2024-05-08、2024-08-22；日文：2024-03-20-ja.md。

## 图与多语言集群

ideas.md、travel/okinawa-2024/blog.md、lab-notebook.md 仍是入度最高的枢纽；
multilingual-glossary.md 作为新的跨语言枢纽加入，密集链接各主线。所有 PDF/XLSX/CSV/PNG/JPG
（含中日文文件）都是叶子节点，只有入边。

## 重新生成语料

```bash
# 全部二进制：
uv run --with reportlab --with openpyxl --with matplotlib --with Pillow \
    --with PyMuPDF scripts/generation/scaffold-personal.py
# 仅中日文（CJK）：
uv run --with reportlab --with openpyxl --with Pillow --with PyMuPDF \
    scripts/generation/scaffold-personal.py --only cjk
```

Markdown 文件为手写（位于 raw/，不由脚本生成）。完整英文正本见 [STORY.md](STORY.md)；
日文概览见 [STORY_ja.md](STORY_ja.md)。
