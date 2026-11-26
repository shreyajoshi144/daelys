# DaelysBench

`make bench SUITE=plan` runs every case in `datasets/plan/` against each model config in `configs.json` and writes
`results/latest.md` (table) and `results/run-<timestamp>.json` (per-case detail). Needs `ANTHROPIC_API_KEY`;
without it the run stops before any call and exits 2.

## Plan suite

Each case is a folder with `brief.md` and `gold.json`:

```json
{
  "items": [{"title": "Build delivery slot picker", "lane": "Engineering"}],
  "dependencies": [["Launch email sequence", "Build subscription checkout"]],
  "contradiction": {"quotes": ["Friday 20 March", "27 March"]}
}
```

`dependencies` are `[item, prerequisite]` by gold title. Contradiction quotes must appear verbatim in the brief
(`tests/test_bench.py` checks every gold file).

| metric | definition |
| --- | --- |
| dep_precision / dep_recall | predicted dependencies whose both ends match gold items and the gold has that edge |
| item_precision / item_recall | items matched one-to-one: same lane, title similarity ≥ 0.5 (difflib ratio) |
| cycle_rate | share of cases whose first model answer contained a dependency cycle |
| uncited_item_rate | share of first-answer items whose quote was not found in the brief |
| contradiction_caught_rate | share of cases where an open question cites a span overlapping a planted contradiction |
| repair_rate | share of cases that needed the one repair retry |

The three cases here are DRAFT templates written while building (tracked as PB-02 in the
[product backlog](../docs/project-management/PRODUCT_BACKLOG.md#4-open-backlog)): write the other 12 briefs with
gold plans, and review the title-match threshold against your labels.
