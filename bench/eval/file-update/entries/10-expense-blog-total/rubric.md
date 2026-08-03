---
id: 10
category: MOD
difficulty: medium
edit_weight: 0.50
knowledge_weight: 0.50
edit_scoring: hybrid
inconsistency: false
---

# Rubric: FU-10 — Expense blog total update

## Edit files (2)

| # | File | Weight | Scoring |
| - | ---- | ------ | ------- |
| 1 | travel/okinawa-2024/expenses.csv | 0.50 | diff |
| 2 | travel/okinawa-2024/blog.md | 0.50 | llm-judge |

The CSV must have a new row for 2024-03-18 gear 4,500 JPY at Yonaguni Dive Shop for dry bag, inserted in date order. The blog.md must have its total line updated from 387,200 JPY to 391,700 JPY and from 2,616 USD to 2,646 USD.

## Verification questions (2)

| # | Question | Weight |
| - | -------- | ------ |
| 1 | Updated trip total in JPY | 0.50 |
| 2 | Expense entry count | 0.50 |

## Notes

- The new row goes between existing entries in date order (after 2024-03-17 entries, before 2024-03-19 entries).
- Only the total line in blog.md changes; all other blog content is unchanged.
- There is no inconsistency flag — the expense count just increases by 1.
