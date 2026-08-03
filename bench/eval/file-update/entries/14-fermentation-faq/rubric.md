---
id: 14
category: SYN
difficulty: hard
edit_weight: 0.30
knowledge_weight: 0.70
edit_scoring: llm-judge
inconsistency: false
---

# Rubric: FU-14 — Fermentation FAQ

## Edit files (1)

| # | File | Weight | Scoring |
| - | ---- | ------ | ------- |
| 1 | fermentation/fermentation-faq.md | 1.00 | llm-judge |

Create a new synthesized FAQ document at `fermentation/fermentation-faq.md`.

## Verification questions (2)

| # | Question | Weight |
| - | -------- | ------ |
| 1 | Kombucha duration | 0.50 |
| 2 | Koji temperature requirements | 0.50 |

## Acceptance criteria (golden)

The synthesized document must:

1. **Cover at least 3 ferment types** from: kombucha, koji, hot sauce, kimchi, miso, awamori.

2. **Include temperature ranges for each covered type:**
   - Kombucha: 22–26 °C
   - Koji: 28–32 °C
   - Hot sauce: 22–26 °C
   - Kimchi: 18–22 °C
   - Miso: 25–28 °C

3. **Include duration/timing for each covered type:**
   - Kombucha: 7–14 days
   - Koji: 54–66 hours total (18 h covered + 36–48 h tray)
   - Hot sauce: 7–21 days
   - Kimchi: 3–7 days
   - Miso: 6–18 months

4. **Include doneness indicators for at least 2 types:**
   - Kombucha: target end pH 3.0–3.4
   - Koji: sweet chestnut aroma, white mycelium covering kernels
   - Hot sauce: target end pH < 4.0
   - Kimchi: ready when refrigerated after 3–7 days

5. **Reference source files** (recipes.md, ph-readings.csv, batch-log.xlsx).

## Notes

- The FAQ should be well-structured, accurate to the source data, and useful for beginners.
- Answers must be consistent with the actual recipe data in the corpus.
