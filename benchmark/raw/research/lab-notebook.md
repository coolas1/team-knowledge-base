# Lab notebook — Ryukyu coral resilience project

> Long-running research log. Most recent entries at the top. Earlier entries
> (pre-2023) are archived in a separate paper notebook; only entries relevant
> to the survey or the 2024 paper are mirrored here.

## 2024-11-08 — Revision 2 submitted

Second-round revisions went out this morning. Reviewer 2 finally accepted the
breakpoint analysis; reviewer 1 wants us to add a paragraph on possible
*Cladocopium* C3k loss under prolonged warming. Drafted that section in
[coral-resilience-paper.md](coral-resilience-paper.md) — needs one more pass
before I push the regenerated PDF. See journal entry
[2024-08-22](../journal/2024-08-22.md) for the emotional context of the whole
revision hell.

## 2024-09-30 — Figure 2 regenerated

Updated [figures/temp-vs-bleach.png](figures/temp-vs-bleach.png) to use the
site-cluster colors rather than a single colormap. The three clusters (northern
refugia, mid-latitude forereef, southern forereef) now read more clearly. R²
dropped from 0.71 to 0.66 with the cluster-aware trend line — that's expected,
since pooling across clusters was inflating the correlation.

## 2024-05-01 — Back from Okinawa

Six weeks of fieldwork wrapped. Refugia sites (RYK-05, RYK-06) behaved as
predicted; the surprise was RYK-04 (Sekisei Lagoon) showing a partial recovery
pattern we hadn't seen before. Photos and dive notes are in
[travel/okinawa-2024/blog.md](../travel/okinawa-2024/blog.md); the relevant
SST/DHW numbers are now in [bleaching-survey-2024.xlsx](bleaching-survey-2024.xlsx)
sheet "Bleaching_quarterly_pct".

## 2024-03-15 — Logger pull, Sesoko West

Pulled and redeployed the HOBO at RYK-06. Bottom temp (15 m) read **22.7 °C** on
the logger — cooler than I'd expected for mid-March. I also started keeping
parallel field notes in Japanese for the Tanaka lab; those are in
[sesoko-field-notes-ja.md](sesoko-field-notes-ja.md) (PDF:
[sesoko-field-notes-ja.pdf](sesoko-field-notes-ja.pdf)). Viz still around 18 m.

## 2024-03-08 — First 2024 quarter on site

Dove RYK-06 (Sesoko West) today with Kenta. SST 22.4 °C, viz 24 m. The C3k
dominance is holding even though DHW is climbing past 6 — exactly what the
resilience hypothesis predicts. Filed the dive metadata into
[dive-sites.csv](dive-sites.csv).

## 2023-11-20 — NSF grant resubmission

Three weeks of rewrite hell. See journal entry
[2023-11-15](../journal/2023-11-15.md) — the original submission got shredded
by the panel, mostly on the symbiont community methods. Reframed the metabarcoding
protocol around ITS2 primers (sym_VAR_5.8S2 / sym_VAR_REV). That's now §2.4 in
the paper.

## 2023-08-14 — 2023 heatwave peak

DHW > 10 across nine of twelve sites. The Cape Hedo and Sesoko West readings
came back this morning: <20% bleaching at both. This is the observation that
started the "resilience refugia" framing. Spent the rest of the afternoon
calling collaborators and rewriting the abstract.

## 2022-04-02 — HOBO logger swap, Cape Hedo

Annual logger swap. The 2010–2019 climatology baseline is now re-anchored
post-calibration. Cape Hedo's baseline shifted by −0.18 °C, which slightly
inflates 2023 DHW at that site — but the resilience signal is so strong
(<20% bleaching at DHW > 10) that the recalibration doesn't change the
conclusion.

## Open questions

1. **C3k stability under RCP 8.5**: does the resilient symbiont community
   persist under sustained warming, or does it eventually shuffle?
2. **Dispersal corridors**: are Cape Hedo / Sesoko West larval sources for
   less-resilient southern sites, or sinks?
3. **Background microbial diversity**: is the C3k dominance a cause of
   resilience, or a correlate of some other environmental factor (depth,
   exposure, substrate)?

These will likely form the next paper. Notes are scattered across
[coral-resilience-paper.md](coral-resilience-paper.md) §4 Discussion and
[notes/ideas.md](../notes/ideas.md).
