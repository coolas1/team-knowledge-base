---
name: story-ja
title: "Wren Adachi 個人アーカイブ（合成コーパス）— 日本語概要"
description: STORY.md の日本語訳。メンテナ向け。評価データではない。予測エージェントは使用禁止。
type: translation
created: 2026-07-09
tags: [personal, persona, synthetic-corpus, multilingual, translation-ja, human-facing]
---

# Wren Adachi — 個人アーカイブ（日本語概要）

> **本ファイルは `eval/STORY.md` の日本語訳であり、人間のメンテナ向けである。
> 評価用データではない——予測エージェント（prediction agents）は本ファイルを回答根拠として
> 使用してはならない。** コーパス本体は合成物であり、多モーダル RAG のベンチマーク用に作られた。

## Wren とは？

**足立 若葉（Wren Adachi）** 博士（34歳）はカリフォルニア大学サンタクルーズ校海洋科学研究所の
海洋生物学者である。アーカイブは三つの筋で貫かれる：

| 筋 | 産出物 | ハブファイル |
|---|---|---|
| サンゴ礁研究 | 論文、複数シート Excel、潜点 CSV、図 | research/coral-resilience-paper.md |
| アンビエント音楽（"Polyp"） | ジャケット、BPM/調 CSV、フィールド録音転写 | music/polyp-tidal-ep-notes.md |
| 発酵趣味 | レシピ、バッチ記録 Excel、pH 時系列 CSV | fermentation/recipes.md |

三つをつなぐのは **2024年春（3〜4月）の沖縄滞在（6週間）** である。仕事の礁調査、
次作 EP のフィールド録音、そして泡盛と麹の伝統への没入。詳しくは travel/okinawa-2024/blog.md。

**Wren は三つの言語で働く。** 日本語と北京語を学んでおり、一部のフィールドノートと日記は日本語。
**林偉（リン・ウェイ）博士**（廈門大学）と南海のサンゴ白化を共同研究し、
**中国の紅麹（紅曲）発酵** を日本の麹と並べて探求している。三言語用語集
notes/multilingual-glossary.md が多言語ハブ。

## コーパスが存在する理由

ベンチマーク用コーパスには、跨フォーマットの現実的な参照、非自明なグラフ、
フォーマット多様性（Markdown 22、Excel 3、CSV 10、PDF 3、生成 PNG 3、ダウンロード JPG 7）、
そして **多言語カバレッジ** が必要である。日中のコンテンツが各筋を貫き、一部の事実は
非英語でのみ存在するため、cross-language 検索が真にテストされる。

## 推奨テストクエリ（例）

- "林偉の南海調査が報告した白化の閾値は？" → scs-bleaching-survey-zh.pdf + south-china-sea-coral-zh.md
- "Wren は英・日・中の三言語で「サンゴの白化」をどう表すか？" → notes/multilingual-glossary.md
- "Tidal EP 日本盤ジャケットの日本語タイトルとその意味は？" → music/tidal-ep-cover-ja.png + tidal-ep-liner-notes-ja.md
- "Wren の紅麹と麹の工程の違いは？" → fermentation/hongqu-notes-zh.md + recipes.md

## ファイル一覧（筋別、多言語ファイルを含む）

- **研究**：coral-resilience-paper.md / .pdf、lab-notebook.md、bleaching-survey-2024.xlsx、
  dive-sites.csv、figures/temp-vs-bleach.png；日本語：sesoko-field-notes-ja.md / .pdf、
  dive-sites-ja.csv；中国語：south-china-sea-coral-zh.md、scs-bleaching-survey-zh.pdf、
  scs-bleaching-2024-zh.xlsx、scs-dive-sites-zh.csv、photos/scs-reef-zh.jpg。
- **音楽**：polyp-tidal-ep-notes.md、field-recordings-okinawa.md、setlist-2024-04-12.md、
  album-cover.png、tracklist.csv；日本語：tidal-ep-liner-notes-ja.md、tidal-ep-cover-ja.png、
  tracklist-ja.csv。
- **発酵**：recipes.md、batch-log.xlsx、ph-readings.csv、photos/scoby-jar.jpg；
  日本語：awamori-notes-ja.md、awamori-batch-ja.csv；中国語：hongqu-notes-zh.md、
  hongqu-ph-readings-zh.csv、photos/hongqu-rice-zh.jpg。
- **旅行**：travel/okinawa-2024/（blog.md、itinerary.md、expenses.csv、写真複数）。
- **ノート**：ideas.md（最も密なハブ）、reading-list.md、finance-2024.csv；
  多言語ハブ：multilingual-glossary.md（英/日/中）、mandarin-study-zh.md（中国語）。
- **日記**：2023-11-15、2024-03-01、2024-05-08、2024-08-22；日本語：2024-03-20-ja.md。

## グラフと多言語クラスタ

ideas.md、travel/okinawa-2024/blog.md、lab-notebook.md が入次数最大のハブのまま。
multilingual-glossary.md が新たな cross-language ハブとして加わり、各筋へ密にリンクする。
すべての PDF/XLSX/CSV/PNG/JPG（中日文を含む）は葉ノードであり、入辺のみを持つ。

## コーパスの再生成

```bash
# 全バイナリ：
uv run --with reportlab --with openpyxl --with matplotlib --with Pillow \
    --with PyMuPDF scripts/generation/scaffold-personal.py
# 中日文（CJK）のみ：
uv run --with reportlab --with openpyxl --with Pillow --with PyMuPDF \
    scripts/generation/scaffold-personal.py --only cjk
```

Markdown は手書き（raw/ 配下、スクリプト非生成）。完全な英語正文は [STORY.md](STORY.md)。
中国語概要は [STORY_zh.md](STORY_zh.md)。
