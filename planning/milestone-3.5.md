# Milestone 3.5: Profiling Follow-ups

> **Status: proposed.** Spec PR for review before any implementation.
> Shared context (architecture, report schema, design decisions) is in [project-overview.md](project-overview.md). Where this spec changes the report schema (see **Schema changes**), **this spec wins** until the implementation PR updates the overview. The previous milestone is [milestone-3.md](milestone-3.md). Its D9 deferred these items to "a separate short spec after M3". The milestone sits between M3 and M4, so M4 (remote deployment) and M5 keep their numbers.

**Goal:** Make the report say more about *why* data is dirty, not only *how much*. It adds four things:
- which observed columns the missing values depend on
- numbers that look like placeholder codes rather than measurements
- identifier columns whose values repeat with different data
- a fix for three behaviours that mislead today: ISO dates in a CSV flagged as a type mismatch, correlations from a handful of rows, and mean/median fills that include infinity

**Layer boundary:** every heuristic is plain pandas/scipy in `profiler_core/`:
- `missing.py` gains the missing-value associations
- `outliers.py` gains the sentinel candidates
- `duplicates.py` gains the duplicate-key check
- `type_detector.py` gains the ISO-date rule
- `correlations.py` gains the minimum row count

`cleaner_core/` gets the finite-value fills and the `sentinel` outlier method, and reuses the profiler's sentinel function, so the rows it acts on are exactly the ones reported. `tools/` only changes docstrings: no new tools and no new tool arguments. None of these modules imports `mcp`, and `tests/test_architecture.py` is unchanged and must keep passing.

---

## Guiding principle: measured on the fixture, honest about limits

These are **new heuristics**, so there is no analyzer behaviour to port, with one exception: the analyzer's MCAR/MAR/MNAR labels, which M3's D9 measured as mislabelling the fixture. Each heuristic below is checked against the problems that `scripts/generate_messy_data.py` injects on purpose, which are the ground truth. Each one reports **evidence, not a verdict**. It says what was observed (a column, a measure, a value) and leaves the conclusion to the agent and the user. A heuristic that can't be measured on the fixture, or that a different dataset would obviously break, is either scoped down or left out.

Three items change behaviour that M1–M3 ported from the analyzer: D4 (dates), D5 (correlations) and D6 (fills). They are **ported back** afterwards, as after M2 and M3.

## Decisions to confirm in review

| # | Decision | Recommendation |
|---|---|---|
| D1 | Missing-value patterns | **Report associations, not MCAR/MAR/MNAR labels.** Each column gets `missing_related_to`: the observed columns whose values go with its missingness, strongest first. A label can't be justified from the data. "MCAR" claims that nothing explains the gaps, but a test can only fail to find an explanation. "MNAR" depends on the values that are missing, which by definition aren't in the data. The analyzer's rules show the problem: it labels any column more than 60% missing "MNAR", and calls a column "MAR" only when it goes missing together with another column. That labels `school_rating` MCAR, although its gaps are driven by `age_years`. <br>**What is measured,** for each column with at least `MIN_MISSING_PATTERN_ROWS` (10) missing **and** 10 present values, against every other column:<br>• **numeric/mixed column:** point-biserial correlation between the missing indicator and that column's values, over the rows where the other column is present (`"point_biserial"`). The sign gives the direction: positive means more missing where the other column is higher.<br>• **categorical/boolean column** with at most `MAX_CRAMERS_V_CARDINALITY` distinct values: Cramér's V between the indicator and the categories (`"cramers_v"`).<br>• **another column's missing indicator,** if that column also has 10 or more missing: phi correlation, i.e. how far the two columns go missing in the same rows (`"missing_together"`). This is the analyzer's co-missingness rule, kept as one of the measures.<br>• **text and datetime columns are skipped:** text has no usable scale, and dates would need an arbitrary encoding. ID columns are kept, because missingness that drifts with the row ID (e.g. a collection change partway through) is meaningful.<br>**Which associations are listed:** those with `\|value\| >= MISSING_ASSOCIATION_THRESHOLD` (0.3), strongest first, at most 5. <br>**Measured on the fixture:**<br>• `school_rating` lists `age_years`, point-biserial **0.663**. This is the injected rule "homes older than 60 years have no rating".<br>• `price` and `sqft` list nothing. Their gaps were injected at random. The strongest chance associations are `price`↔`garage` (V = 0.216) and `sqft`↔`id` (r = −0.110).<br>• The threshold has a clear margin on both sides. A significance test wouldn't help: the chance association `price`↔`garage` already has p = 0.001 (chi-square at n = 520, with small expected counts), so effect size is the useful gate.<br>**Meaning of the value:** `null` means not applicable (no missing values, or too few missing or present to test). `[]` means tested and nothing found, which is consistent with random gaps but doesn't prove them. <br>**Recommendation:** each association adds a `missing` warning that says what was found and why it matters. For example: "'school_rating' is missing more often where 'age_years' is higher (point-biserial r = 0.66): the gaps are not random, so a mean or median fill would bias it." |
| D2 | Sentinel values (placeholder numbers) | **Flag outliers that look like codes, in a new `possible_sentinels` field of `OutlierInfo`.** A candidate is a value outside the column's IQR fences that also passes one of two tests:<br>• **Repeated:** it occurs at least twice once exact duplicate rows are removed, in a column whose deduplicated unique ratio is at least 0.5. Real measurements of a continuous quantity rarely repeat an extreme value exactly, while placeholder codes do. Discrete columns (counts, ratings) repeat by nature, so the rule skips them. Exact duplicates are excluded first because a duplicated row repeats every value in it. On the fixture this matters: without it, `price` 144,667 and `sqft` 341 would be flagged only because their rows are duplicated.<br>• **All 9s:** the absolute value is an integer of three or more 9s (999, 9999, …, 9999999), the most common numeric placeholder. This catches a single occurrence too.<br>**Output:** `[{value, count}]`, where `count` is the number of rows holding the value; sorted by count, then value; at most 10 entries. `[]` means checked, none found. The field lives inside `OutlierInfo`, so a column whose `outliers` is `null` has none.<br>**Measured on the fixture:**<br>• `price`: −1,000 (4), 5,000 (4) and 9,999,999 (2), **exactly the 10 injected placeholders**. The other 5 IQR outliers are genuine extremes and are not listed.<br>• Every other column: none. The all-9s test matches only 9,999,999.<br>**Text placeholders** (`TBD`, `unknown` in `sqft`) are already reported as `non_numeric_examples`. pandas reads `N/A`, `NA`, `null` and similar strings as missing.<br>**Recommendation:** an `outlier` warning naming the values and how many rows each is on, and pointing to D7: "'price' has values that look like placeholder codes rather than measurements: -1000 (4 rows), 5000 (4 rows), 9999999 (2 rows). Treat them as missing: handle_outliers with method 'sentinel' and strategy 'replace_nan'." <br>**Not chosen:** a fixed list of codes (−1, 0, 99, …), which would flag legitimate values in many datasets. The outlier gate is what keeps both rules safe: a code that sits inside the normal range isn't flagged. |
| D3 | Duplicate IDs | **Check identifier-like columns for values shared by rows with different data, in a new `key_columns` field of `DuplicateInfo`.** <br>**A column counts as a key when both hold:**<br>• **Its name says so** (case-insensitive): it is exactly `id`, `key`, `uuid` or `guid`, or ends in one of them after `_`, `-`, a space, or a camelCase boundary (`customer_id`, `orderKey`, `Row ID`).<br>• **It is nearly unique:** after removing exact duplicate rows, at least `KEY_MIN_UNIQUE_RATIO` (0.95) of its non-null values are distinct.<br>The uniqueness gate keeps foreign keys out. A `customer_id` in an orders table repeats by design, and its unique ratio is low. A name-free rule (nearly unique alone) would also flag `price` (0.945) and `sale_date` (0.962) on the fixture, so it isn't used. <br>**Output:** per key column: `column`, `conflicting_values` (key values found on two or more rows that aren't exact duplicates of each other), `conflicting_rows`, and `sample_indices` (capped at 50, with `indices_truncated`). A key column with no conflicts is listed with zeros, which means checked, none found. `[]` means no key column was found. Exact duplicates are already counted in `exact_count`, so they aren't counted here again. <br>**Measured on the fixture:**<br>• `id` is the only key column (unique ratio 0.998).<br>• `conflicting_values` = **1**: id 484 is on rows 13 and 65, which differ only in `sqft` (2094 vs "unknown"). The injection script made one duplicated row's `sqft` a placeholder, so the copy is no longer exact.<br>• The clean sample: `id` is listed with 0 conflicts.<br>**Recommendation:** a `duplicate` warning: "'id' looks like a row identifier, but 1 value appears on rows with different data (rows 13, 65): decide which record is correct before deduplicating." Not scored (D8). |
| D4 | ISO dates in a CSV | **A text column in a CSV whose values are all ISO 8601 dates is `datetime` with `type_mismatch: false`.** CSV has no date type, so ISO text is the normal way to store a date there, and `pd.read_csv` never types it. Today such a column counts as a mismatch, costs score, and gets a "parse them as datetimes" recommendation.<br>**The rule:** every non-null value matches `YYYY-MM-DD`, optionally followed by a time (`T` or a space, then `HH:MM`, optional `:SS` and fraction), optionally with `Z` or a `±HH:MM` offset, and every value parses.<br>**When the rule applies:**<br>• **CSV input only,** whether a file or inline, decided by the loader's extension. XLSX has a real date type, so a text date there is still a mismatch.<br>• **ISO only:** other formats, even a single consistent one such as `MM/DD/YYYY`, stay mismatches, because day-month order is ambiguous and the agent should parse them deliberately. Mixed formats stay mismatches too.<br>**Measured:**<br>• **Raw fixture: unchanged.** `sale_date` rotates through three formats (33.5% ISO), so it is still flagged, and the score stays **77 (C)**.<br>• **The CSV output of the M3 six-step plan** is 100% ISO. It now scores **88 (B)**, up from 86. That equals the in-memory frame and the XLSX output, which settles the gap M3's D8 described. The M3 figures in the overview and the README are updated to 88. |
| D5 | Correlations on a few rows | **A pair needs at least `MIN_CORRELATION_ROWS` (10) rows where both values are present.** Below that, its Pearson or Cramér's V value is `null`, and it can't become a `high_correlation_pairs` entry or a recommendation. Today two rows always give r = ±1: the 4-row inline CSV used in M3's manual testing reported `id`↔`price` as perfectly correlated, from the 2 rows that both had a price.<br>**Measured:** on the fixture the smallest pairwise overlap is 320 rows, so every figure is unchanged. In the 4-row inline example, the pair becomes `null` and its recommendation disappears.<br>**Implementation:** pandas' `corr(min_periods=...)` for Pearson, and the same rule applied to the crosstab total for Cramér's V. This is why `cramers_v` values become `float \| null` (see **Schema changes**). |
| D6 | Mean/median fills with infinity | **`fill_mean` and `fill_median` compute their value from finite numbers only, and need at least one finite number.** Today `[1, inf, NaN]` is filled with `inf`, although the report's stats exclude ±inf (M2) and show a mean of 1.0. A column of only ±inf silently fills nothing. The rule matches `cap_iqr`, which already fences on finite values. A column without a finite number gets the existing "needs a numeric column with at least one number" error. Fixture figures don't change, because it has no infinite values. |
| D7 | Cleaning sentinels precisely | **`handle_outliers` gains `method: "sentinel"`:** the rows are those whose value is one of the column's `possible_sentinels`, computed on the input by the same function as the report (as for `iqr` and `zscore`, M3 D5). It works with `remove` and `replace_nan`. With `cap_iqr` it is rejected, as `zscore` is, because clipping a code to a fence would make it look like a real value. A column with no sentinels is a logged no-op (`outliers_treated: 0`). Without this method, `replace_nan` on all IQR outliers also nulls the genuine extremes: on `price` that means 15 values instead of the 10 placeholders. This method is an addition the analyzer doesn't have, and it isn't ported back (see D9). |
| D8 | Score | **The formula is unchanged, and the new findings aren't scored.** Sentinels are already counted as outliers. A conflicting key is a judgement call, often a corrected record, like a near-duplicate, which isn't scored either. Missing-value associations describe values that are already penalised as missing. Scores move only through D4. |
| D9 | Port back, version | **Port D1, D4, D5 and D6 back to the analyzer** in a separate PR after this merges:<br>• **D1:** the analyzer keeps its `missing_pattern` field, now derived from the same evidence. `"none"` means no missing values. `"MAR"` means `missing_related_to` is non-empty, i.e. related to an observed column. `"MCAR"` means tested and nothing found, which its docstring already defines as "no detectable pattern". The >60% → `"MNAR"` rule is dropped, because the data can't show it.<br>**D2, D3 and D7 stay MCP-only for now.** The analyzer would need new UI panels, so they're listed as analyzer candidates rather than ported.<br>**Version:** 0.4.0 (new report fields). Milestone numbers and versions stop matching from here on: M4 will be 0.5.0. |

## Deviations from the analyzer

| Item | Analyzer | Here | Reason |
|---|---|---|---|
| Missing-value pattern | `missing_pattern` label (MCAR/MAR/MNAR) from co-missingness and a 60% rule | `missing_related_to` evidence list (D1). The label is never reported. | Measured mislabelling; a label claims more than data shows. Port back as a relabelling (D9). |
| ISO dates in CSV | type mismatch | not a mismatch (D4) | CSV has no date type; port back |
| Correlations below 10 shared rows | computed (r = ±1 from 2 rows) | `null` (D5) | Not meaningful; port back |
| `fill_mean` / `fill_median` | include ±inf | finite values only (D6) | Consistent with the stats and `cap_iqr`; port back |
| Sentinels, duplicate keys, `method: "sentinel"` | not present | new (D2, D3, D7) | New heuristics; analyzer candidates |

## Schema changes (proposed)

**These change the report contract** (`profiler_core/schema.py`), so reviewers should check them. All are additions, apart from one widened type:

```python
class MissingAssociation(TypedDict):
    column: str
    method: Literal["point_biserial", "cramers_v", "missing_together"]
    value: float                     # rounded to 3 decimals; sign meaningful for point_biserial

class ColumnReport(TypedDict):       # + one field
    ...
    missing_related_to: list[MissingAssociation] | None
                                     # None: no missing values, or < 10 missing / present
                                     # []: tested, no association >= 0.3

class SentinelValue(TypedDict):
    value: float
    count: int                       # rows holding the value

class OutlierInfo(TypedDict):        # + one field
    ...
    possible_sentinels: list[SentinelValue]   # [] = checked, none

class KeyColumnInfo(TypedDict):
    column: str
    conflicting_values: int
    conflicting_rows: int
    sample_indices: list[int]        # capped at 50
    indices_truncated: bool

class DuplicateInfo(TypedDict):      # + one field
    ...
    key_columns: list[KeyColumnInfo] # [] = no identifier-like column found

class CorrelationInfo(TypedDict):    # widened
    cramers_v: dict[str, dict[str, float | None]]   # None below MIN_CORRELATION_ROWS
```

`"method"` in the `handle_outliers` cleaning step widens to `"iqr" | "zscore" | "sentinel"` (D7). No new recommendation categories: the new findings use `missing`, `outlier` and `duplicate`. The focused tools show the fields of their section: `detect_outliers` includes `possible_sentinels` and `find_duplicates` includes `key_columns`. `missing_related_to` is part of `profile_dataset`'s column entries.

**Measured examples on the fixture** (the values the implementation must reproduce):

```python
report["columns"]["school_rating"]["missing_related_to"]
# [{"column": "age_years", "method": "point_biserial", "value": 0.663}]
report["columns"]["price"]["missing_related_to"]        # []
report["columns"]["id"]["missing_related_to"]           # None (no missing values)
report["columns"]["price"]["outliers"]["possible_sentinels"]
# [{"value": -1000.0, "count": 4}, {"value": 5000.0, "count": 4}, {"value": 9999999.0, "count": 2}]
report["duplicates"]["key_columns"]
# [{"column": "id", "conflicting_values": 1, "conflicting_rows": 2,
#   "sample_indices": [13, 65], "indices_truncated": False}]
```

## Configuration

New settings in `config.py`, each passed to the core as a keyword parameter:
- `MIN_MISSING_PATTERN_ROWS = 10`
- `MISSING_ASSOCIATION_THRESHOLD = 0.3`
- `MAX_MISSING_ASSOCIATIONS = 5`
- `SENTINEL_MIN_UNIQUE_RATIO = 0.5`
- `MAX_SENTINELS = 10`
- `KEY_MIN_UNIQUE_RATIO = 0.95`
- `MIN_CORRELATION_ROWS = 10`

## Tasks

0. **Verify** that `float | None` inside the nested `cramers_v` dict, and the new nested TypedDicts, produce the expected output schema with mcp 2.3.0, and that output validation accepts `None` there.
1. **`config.py`:** the settings above.
2. **`profiler_core/missing.py`:** `missing_associations(df, type_info, *, ...)` (D1).
   - Vectorise the numeric case (one `corrwith` per missing column), and guard against constant indicators and columns.
   - Reuse `correlations.cramers_v`.
3. **`profiler_core/outliers.py`:** `possible_sentinels(series, fences, deduplicated_series, *, ...)` (D2), called from `detect_column_outliers`, so `detect_outliers` and the report agree.
4. **`profiler_core/duplicates.py`:** key-column detection and conflicts (D3).
5. **`profiler_core/type_detector.py`:** the ISO rule as a keyword argument of `infer_type`/`detect_types`.
   - The report builders take the input format, and the tools and `run_cleaning` pass the loader's extension. `run_cleaning` passes the output format for the `after` snapshot (D4).
6. **`profiler_core/correlations.py`:** `min_periods` for Pearson, the same rule for Cramér's V, and the widened type (D5).
7. **`profiler_core/recommendations.py`:** the three new warnings, using the existing categories and ordering.
8. **`cleaner_core/`:**
   - finite-value fills (D6) in `missing_handler.py`, and the finite-number check in the pipeline's `_require_numbers`
   - `method: "sentinel"` in validation and outlier row selection (D7), with `cap_iqr` + `sentinel` rejected
9. **Tools:** docstrings for `profile_dataset`, `detect_outliers`, `find_duplicates` and `clean_dataset` describe the new fields and `method: "sentinel"`, written for a model reader. The `dataquality://docs/quality-score` note on `type_mismatch` mentions the CSV ISO rule.
10. **Tests:**
    - **Fixture figures:** every measured figure above, for D1, D2, D3, D4 (77 raw; 88 for the M3 plan's CSV output, equal to `profile_dataset` on that file) and D5 (the 4-row inline example gives `null`).
    - **D1:** a synthetic missing-at-random column gives `[]` and a column driven by another gives that column. Also `null` below 10 missing or present, and all three measures.
    - **D2:**
      - a repeated code in a discrete column is not flagged
      - a duplicate row doesn't create a candidate
      - a single 9999 is flagged; a code inside the fences isn't
    - **D3:**
      - `customer_id` with many repeats is not a key
      - camelCase and spaced names match
      - exact duplicates aren't counted as conflicts
    - **D4:**
      - ISO with times and offsets counts as typed
      - `MM/DD/YYYY` stays a mismatch
      - an XLSX text-date column stays a mismatch
      - inline CSV behaves like a file
    - **D5:** 9 shared rows gives `null`, 10 gives a value.
    - **D6:** `[1, inf, NaN]` fills 1, and a column of only ±inf is rejected.
    - **D7:**
      - `replace_nan` + `sentinel` on the fixture nulls exactly the 10 placeholder cells
      - `cap_iqr` + `sentinel` is rejected
      - a column without sentinels is a no-op
    - **pandas 2 and 3:** both pass (CI).
11. **Performance:** `profile_dataset` at 500,000 × 10 within 5 s (it takes 2.9 s now). A prototype of D1's measures took 0.9 s at that size. Report the measured time in the PR.
12. **Docs:**
    - the overview's report schema, the roadmap row for 3.5, and the M3 example figures (86 → 88 for the CSV output)
    - `docs/architecture.md`: a short heuristics section with each rule and its limits
    - the README
    - this spec's As built
    - version 0.4.0
13. **Port back to the analyzer** after this merges, as a separate PR: D1 as relabelling, D4, D5 and D6 (D9).

**Out of scope:**
- M4 (HTTP transport, Docker, auth, the `file_path` switch)
- text placeholders in categorical columns (`unknown` as a label), and a cleaning step to null arbitrary values
- datetime columns in D1
- user-declared key columns as a tool argument

**CI / ruleset:** no new workflows or job names, so the ruleset's required checks don't change.

**PR title / squash commit message:** `feat: missing-value associations, sentinels, duplicate keys and CSV dates`
