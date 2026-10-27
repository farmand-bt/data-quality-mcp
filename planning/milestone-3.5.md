# Milestone 3.5: Profiling Follow-ups

> **Status: complete.** The spec was approved in #13 and implemented on `feat/milestone-3.5`. See **As built** at the end for figures and small differences.
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
| D1 | Missing-value patterns | **Report associations, not MCAR/MAR/MNAR labels.** Each column gets `missing_related_to`: the observed columns whose values go with its missingness, strongest first. A label can't be justified from the data. "MCAR" claims that nothing explains the gaps, but a test can only fail to find an explanation. "MNAR" depends on the values that are missing, which by definition aren't in the data. The analyzer's rules show the problem: it labels any column more than 60% missing "MNAR", and calls a column "MAR" only when it goes missing together with another column. That labels `school_rating` MCAR, although its gaps are driven by `age_years`. <br>**Which columns are tested:** every column with missing values, whatever its type (the *target*), at most `MAX_MISSING_PATTERN_COLUMNS` (50).<br>**What it is compared with,** limited to the same column caps as the correlations section:<br>• **numeric/mixed column:** point-biserial correlation (Pearson r between the 0/1 indicator and the values) and its p-value, over the rows where that column has a finite number (`"point_biserial"`). Positive means more missing where the other column is higher.<br>• **categorical/boolean column** with at most `MAX_CRAMERS_V_CARDINALITY` distinct values: **bias-corrected** Cramér's V (Bergsma 2013) and the chi-square p-value, without Yates' correction, over the rows where that column is present (`"cramers_v"`). Categories with fewer than 5 such rows are left out first, and at least 2 categories must remain.<br>• **another column's missing indicator:** phi, the Pearson r of the two indicators, with its p-value, over all rows (`"missing_together"`). Only positive values count: the two columns go missing in the same rows.<br>• **text and datetime columns aren't comparison columns:** text has no usable scale, and dates would need an arbitrary encoding. They are still tested as targets. ID columns are kept, because missingness that drifts with the row ID (e.g. a collection change partway through) is meaningful.<br>**Each pair needs enough rows:** among the rows the comparison uses, at least `MIN_MISSING_PATTERN_ROWS` (10) with the target missing and 10 with it present. For `missing_together`, that holds for both columns. A pair that doesn't qualify is skipped. That rules out the case where a sparse column present on two rows gives \|r\| = 1.<br>**Which associations are listed:** \|value\| of at least `MISSING_ASSOCIATION_THRESHOLD` (0.3) **and** p below `MISSING_ASSOCIATION_MAX_P` (0.001) divided by the number of pairs tested for that target (a Bonferroni correction, because one target is compared with many columns). Both are compared before rounding. Values are rounded to 3 decimals, sorted strongest first, at most 5.<br>**Calibrated under random missingness** (400 seeded trials per cell; 40 to 5,000 rows; 10% and 50% missing; 2 to 48 categories):<br>• **The plain Cramér's V of the correlations section is unusable here:** it reaches 0.3 in up to **100%** of trials with 40–520 rows and 20–48 categories.<br>• **Effect size alone isn't enough for the correlation:** \|r\| ≥ 0.3 occurs in up to 6.5% of trials at 40 rows.<br>• **Per pair, with the rules above** (before the Bonferroni correction): the false-positive rate is **0%** for Cramér's V in every cell, and at most **0.3%** for point-biserial.<br>• **Per target column (200 seeded trials):** a target compared with about 100 columns (85 numeric, 15 categorical with 8 levels) lists something under random missingness in up to **8%** of trials at 100 rows with p < 0.001 alone, and in **0%** of trials, at every size, with the Bonferroni correction.<br>• **Power with the correction, in the 100-column worst case:**<br>&nbsp;&nbsp;– a missing rate of 60% in one category of four, against 10% in the others: found in 57% of trials at 100 rows and 99.5% at 300<br>&nbsp;&nbsp;– missingness that rises with a numeric column: found in 71% of trials at 100 rows and in all of them at 300<br>&nbsp;&nbsp;With fewer comparison columns the correction is milder. A false listing is the worse error for an evidence-only field.<br>**Measured on the fixture:**<br>• `school_rating` lists `age_years`, point-biserial **0.663** (p ≈ 5e-67, far below 0.001 / 10, for its 10 pairs: 6 numeric, 2 categorical and 2 missing indicators). This is the injected rule "homes older than 60 years have no rating".<br>• `price` and `sqft` list nothing. Their gaps were injected at random. The strongest chance association is `price`↔`garage`, with corrected V = 0.183 (p = 0.001; the plain V would be 0.216).<br>**Meaning of the value:**<br>• `null`: not tested. Either the column has no missing values, or no pair qualified.<br>• `[]`: at least one pair was tested and none was listed. That is consistent with random gaps but doesn't prove them.<br>**Recommendation:** each association adds a `missing` warning that says what was observed, without a verdict. For example: "'school_rating' is missing more often where 'age_years' is higher (point-biserial r = 0.66, p < 0.001). If the gaps depend on age_years, a mean or median fill would bias the column." |
| D2 | Sentinel values (placeholder numbers) | **List outliers that may be placeholder codes, in a new `possible_sentinels` field of `OutlierInfo`.** This is a list of *suspects* for the user to confirm, not a finding that the values are wrong. Nothing is cleaned without the caller naming the values (D7). <br>**A candidate** is a finite value outside the column's IQR fences that passes one of two tests:<br>• **Repeated:** it occurs on at least 2 rows once exact duplicate rows are removed (`drop_duplicates(keep="first")`), in a column whose finite numeric values on those rows have a unique ratio of at least `SENTINEL_MIN_UNIQUE_RATIO` (0.5). Placeholder codes repeat, and exact repeats of an extreme value are less common in measurements with many distinct values. The ratio leaves out most counts and ratings, which repeat by nature. It doesn't leave out every discrete column. Exact duplicates are removed first because a duplicated row repeats every value in it: without that step the fixture would also list `price` 144,667 and `sqft` 341.<br>• **All 9s:** the absolute value is an integer of three or more 9s (999, 9999, …), the most common numeric placeholder. This one also catches a single occurrence.<br>**Not applicable:** ±inf is never a candidate (it is reported as `infinite_count`). The list is `[]` when the fences are `null`. Mixed columns use their numeric values.<br>**Known limits, stated in the tool docstring:**<br>• Legitimate repeated extremes are listed, e.g. a real price of 1,000 sold twice in data otherwise between 1 and 100.<br>• So is a real 999.<br>• A code inside the normal range is not.<br>**Output:** `[{value, count}]`, where `count` is the number of input rows holding the value, duplicates included. A value on one row plus an exact copy of that row therefore has a count of 2, but isn't a candidate under the repeat test, which uses deduplicated rows. Sorted by count descending, then value ascending; at most `MAX_SENTINELS` (10). `[]` means checked, none found.<br>**Measured on the fixture:** `price` lists −1,000 (4), 5,000 (4) and 9,999,999 (2), **exactly the 10 injected placeholders**. The other 5 IQR outliers are genuine extremes and aren't listed. Every other column lists none; the all-9s test matches only 9,999,999.<br>**Text placeholders** (`TBD`, `unknown` in `sqft`) are already reported as `non_numeric_examples`. pandas reads `N/A`, `NA`, `null` and similar strings as missing.<br>**Recommendation:** an `outlier` warning that says what was observed and asks for a check: "'price' has repeated extreme values that may be placeholder codes: -1000 (4 rows), 5000 (4 rows), 9999999 (2 rows). Check with the data owner; values confirmed as codes can be set to missing with handle_outliers, method 'sentinel', values [...]." <br>**Not chosen:** a fixed list of codes (−1, 0, 99, …), which would flag legitimate values in many datasets. |
| D3 | Duplicate IDs | **Check identifier-like columns for values shared by rows with different data, in a new `key_columns` field of `DuplicateInfo`.** <br>**A column counts as a key when both hold:**<br>• **Its name says so:** the whole name, ignoring case, is `id`, `key`, `uuid` or `guid`. Or the name ends in one of them, in any case, after `_`, `-` or a space (`customer_id`, `Row ID`, `ORDER_KEY`). Or it ends in one of them after a camelCase boundary: a lowercase letter or digit followed by the word starting with a capital (`orderKey`, `CustomerID`, `userId`). The boundary needs that change of case, so `paid`, `valid` and `ORDERID` don't match.<br>• **It is nearly unique:** after removing exact duplicate rows, at least `KEY_MIN_UNIQUE_RATIO` (0.95) of its non-null values are distinct.<br>The uniqueness gate keeps foreign keys out: a `customer_id` in an orders table repeats by design, so its unique ratio is low. A name-free rule (nearly unique alone) would also flag `price` (0.983) and `sale_date` (0.998) on the fixture, measured the same way, so it isn't used. <br>**Counting:** done on the rows left after `drop_duplicates(keep="first")`, with null keys ignored. Exact duplicates are already counted in `exact_count`, so they aren't counted again.<br>• `conflicting_values`: key values that appear on more than one of those rows.<br>• `conflicting_rows`: the rows holding those values.<br>• `sample_indices`: their original positions, capped at 50, with `indices_truncated`.<br>• Example: rows A and A′ are exact copies, and row B shares their key with different data. That counts as 1 value on 2 rows, A and B.<br>A key column with no conflicts is listed with zeros, which means checked, none found. `[]` means no key column was found. <br>**Measured on the fixture:**<br>• `id` is the only key column (unique ratio 0.998).<br>• `conflicting_values` = **1**: id 484 is on rows 13 and 65, which differ only in `sqft` (2094 vs "unknown"). The injection script made one duplicated row's `sqft` a placeholder, so the copy is no longer exact.<br>• The clean sample: `id` is listed with 0 conflicts.<br>**Recommendation:** a `duplicate` warning: "'id' looks like a row identifier, but 1 value appears on rows with different data (rows 13, 65): decide which record is correct before deduplicating." Not scored (D8). |
| D4 | ISO dates in a CSV | **A text column in a CSV whose values are all ISO 8601 dates is `datetime` with `type_mismatch: false`.** CSV has no date type, so ISO text is the normal way to store a date there, and `pd.read_csv` never types it. Today such a column counts as a mismatch, costs score, and gets a "parse them as datetimes" recommendation.<br>**The rule:**<br>• Every non-null value matches `YYYY-MM-DD`, optionally followed by a time (`T` or a space, then `HH:MM`, optional `:SS` and fraction), optionally with `Z` or a `±HH:MM` offset.<br>• Every value parses with `pd.to_datetime(..., format="ISO8601", utc=True, errors="coerce")`.<br>• The values agree on timezone-awareness: all have an offset or `Z`, or none does. Mixing naive and offset values is ambiguous, so it stays a mismatch.<br>**When the rule applies:**<br>• **CSV input only,** whether a file or inline, decided by the loader's extension. XLSX has a real date type, so a text date there is still a mismatch.<br>• **ISO only:** other formats, even a single consistent one such as `MM/DD/YYYY`, stay mismatches, because day-month order is ambiguous and the agent should parse them deliberately. Mixed formats stay mismatches too.<br>**Mixed offsets:** the crash on ISO timestamps with different UTC offsets is fixed separately, ahead of this milestone, in #14 (0.3.1). There, profiling parses such values to UTC, and `fix_type` converts them to UTC or rejects a naive-and-offset mix. D4 builds on that parsing.<br>**How CSV is recognised:** by the loader's extension, which it matches case-insensitively: the file path's extension, or `filename_hint`'s for inline input. `.CSV` and inline `data.csv` both count.<br>**Measured:**<br>• **Raw fixture: unchanged.** `sale_date` rotates through three formats (33.5% ISO), so it is still flagged, and the score stays **77 (C)**.<br>• **The CSV output of the M3 six-step plan** is 100% ISO. It now scores **88 (B)**, up from 86. That equals the in-memory frame and the XLSX output, which settles the gap M3's D8 described. The M3 example in the overview is updated to 88. |
| D5 | Correlations on a few rows | **A pair needs at least `MIN_CORRELATION_ROWS` (10) rows where both values are present.** Below that, its Pearson or Cramér's V value is `null`, and it can't become a `high_correlation_pairs` entry or a recommendation. Today two rows always give r = ±1: the 4-row inline CSV used in M3's manual testing reported `id`↔`price` as perfectly correlated, from the 2 rows that both had a price.<br>**Measured:** on the fixture the smallest pairwise overlap is 320 rows, so every figure is unchanged. In the 4-row inline example, the pair becomes `null` and its recommendation disappears.<br>**Implementation:** pandas' `corr(min_periods=...)` for Pearson, and the same rule applied to the crosstab total for Cramér's V. This is why `cramers_v` values become `float \| null` (see **Schema changes**). The correlations section keeps the analyzer's plain Cramér's V, for parity. Its sparse-table bias, which D1's calibration shows, is a candidate for a later change. |
| D6 | Mean/median fills with infinity | **`fill_mean` and `fill_median` compute their value from finite numbers only, and need at least one finite number.** Today `[1, inf, NaN]` is filled with `inf`, although the report's stats exclude ±inf (M2) and show a mean of 1.0. A column of only ±inf silently fills nothing. The rule matches `cap_iqr`, which already fences on finite values. A column with no finite number is rejected with its own message, because the existing one would say "'x' is numeric": "fill_mean needs at least one finite number, and 'x' has only ±inf or missing values. Use fill_constant, or handle_outliers first." Fixture figures don't change, because it has no infinite values. |
| D7 | Cleaning confirmed sentinels | **`handle_outliers` gains `method: "sentinel"` with a required `values` list:** the codes the caller confirmed, 1 to `MAX_SENTINELS` numbers. Each one must pass D2's candidate test on the input. The cleaner uses the same function as the report, but without the `MAX_SENTINELS` cap (as M3 D5 does for outlier rows), so a code beyond the 10 the report shows can still be cleaned. A value that isn't a candidate is rejected with `InvalidInputError` (the core's domain error), which lists the first 10 candidates and says how many there are. `tools/` maps it to a `ToolError`, as for every other validation error.<br>**Matching:** the column's values are converted with `pd.to_numeric(errors="coerce")`, as for outlier detection, so mixed and text-numeric columns work: `"5000"` matches. They are then compared with the listed values as floats, so a JSON `5000` matches `5000.0`. The candidate test and the row selection use the same converted values and the same exact float equality, so a candidate the report lists always selects its rows. A value that only nearly matches (`0.30000000000000004` vs `0.3`) is not a candidate.<br>**Which rows:** the rows whose value equals one of `values`.<br>**Strategies:**<br>• `replace_nan` and `remove` work.<br>• `cap_iqr` is rejected, as with `zscore`, because clipping a code to a fence would make it look like a real value.<br>**Other combinations are rejected:** `values` with another method, and `sentinel` without `values`.<br>**Why this is needed:** without it, `replace_nan` on all IQR outliers also nulls the genuine extremes. On `price` that is 15 values instead of the 10 placeholders.<br>**Why the values must be named:** given D2's limits, the server shouldn't decide that a value is a code. The docstring tells the agent to confirm the values with the user first. This method is an addition the analyzer doesn't have, and it isn't ported back (see D9). |
| D8 | Score | **The formula is unchanged, and the new findings aren't scored.** Sentinels are already counted as outliers. A conflicting key is a judgement call, often a corrected record, like a near-duplicate, which isn't scored either. Missing-value associations describe values that are already penalised as missing. Scores move only through D4. |
| D9 | Port back, version | **Port D1, D4, D5 and D6 back to the analyzer** in a separate PR after this merges.<br>**D1:** the analyzer keeps its `missing_pattern` field, now derived from the same evidence:<br>• `"none"`: no missing values<br>• `"MAR"`: `missing_related_to` is non-empty<br>• `"MCAR"`: `[]`, tested and nothing found, which its docstring already defines as "no detectable pattern"<br>• **new** `"unknown"`: missing values, but no pair qualified for a test<br>• the >60% → `"MNAR"` rule is dropped, because the data can't show it<br>This changes some of the analyzer's labels. Its MAR rule calls a column MAR when more than 70% of its missing rows are also missing elsewhere. A column whose 50 gaps all sit inside another column's 400 is MAR there, but has phi ≈ 0.18 here, so it isn't listed (see Deviations).<br>**D2, D3 and D7 stay MCP-only for now.** The analyzer would need new UI panels, so they're listed as analyzer candidates rather than ported.<br>**Version:** 0.4.0 (new report fields). Milestone numbers and versions stop matching from here on: M4 will be 0.5.0. |

## Deviations from the analyzer

| Item | Analyzer | Here | Reason |
|---|---|---|---|
| Missing-value pattern | `missing_pattern` label (MCAR/MAR/MNAR): MAR if >70% of a column's gaps are also gaps elsewhere, MNAR above 60% missing | `missing_related_to` evidence list (D1), with calibrated measures; no label | Measured mislabelling; a label claims more than data shows. Port back as a relabelling (D9). Some analyzer labels change: a column whose gaps sit inside another's larger gaps is no longer MAR. |
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
    value: float                     # rounded to 3 decimals; signed for point_biserial,
                                     # > 0 for cramers_v and missing_together

class ColumnReport(TypedDict):       # + one field
    ...
    missing_related_to: list[MissingAssociation] | None
                                     # None: not tested (no missing values, or no pair qualified)
                                     # []: at least one pair tested, none listed

class SentinelValue(TypedDict):
    value: float
    count: int                       # rows holding the value

class OutlierInfo(TypedDict):        # + one field
    ...
    possible_sentinels: list[SentinelValue]   # [] = checked, none (also when fences are None)

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
    pearson: dict[str, dict[str, float | None]]     # unchanged: already nullable (constant columns)
    cramers_v: dict[str, dict[str, float | None]]   # widened: None below MIN_CORRELATION_ROWS
```

The `handle_outliers` cleaning step widens `"method"` to `"iqr" | "zscore" | "sentinel"` and gains `"values": list[float]`, which is NotRequired: required with `"sentinel"` (1 to 10 items) and rejected otherwise (D7). No new recommendation categories: the new findings use `missing`, `outlier` and `duplicate`. The focused tools show the fields of their section: `detect_outliers` includes `possible_sentinels` and `find_duplicates` includes `key_columns`. `missing_related_to` is part of `profile_dataset`'s column entries.

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
- `MAX_MISSING_PATTERN_COLUMNS = 50`
- `MIN_MISSING_PATTERN_ROWS = 10`
- `MISSING_ASSOCIATION_THRESHOLD = 0.3`
- `MISSING_ASSOCIATION_MAX_P = 0.001` (before the Bonferroni correction)
- `MAX_MISSING_ASSOCIATIONS = 5`
- `SENTINEL_MIN_UNIQUE_RATIO = 0.5`
- `MAX_SENTINELS = 10`
- `KEY_MIN_UNIQUE_RATIO = 0.95`
- `MIN_CORRELATION_ROWS = 10`

## Tasks

0. **Verify** that `float | None` inside the nested `cramers_v` dict, and the new nested TypedDicts, produce the expected output schema with mcp 2.3.0, and that output validation accepts `None` there.
1. **`config.py`:** the settings above.
2. **`profiler_core/missing.py`:** `missing_associations(df, type_info, *, ...)` (D1).
   - Vectorise the numeric case (one `corrwith` per missing column), and skip constant indicators and columns.
   - Add a bias-corrected Cramér's V next to `correlations.cramers_v`, which keeps the analyzer's formula for the correlations section.
3. **`profiler_core/outliers.py`:** `possible_sentinels(series, fences, deduplicated_series, *, ...)` (D2), called from `detect_column_outliers`, so `detect_outliers` and the report agree.
4. **`profiler_core/duplicates.py`:** key-column detection and conflicts (D3).
5. **`profiler_core/type_detector.py`:** the ISO rule as a keyword argument of `infer_type`/`detect_types`. It uses the UTC-capable parsing from #14.
   - The report builders take the input format, and the tools and `run_cleaning` pass the loader's extension. `run_cleaning` passes the output format for the `after` snapshot (D4).
6. **`profiler_core/correlations.py`:** `min_periods` for Pearson, the same rule for Cramér's V, and the widened type (D5).
7. **`profiler_core/recommendations.py`:** the three new warnings, using the existing categories and ordering.
8. **`cleaner_core/`:**
   - finite-value fills (D6) in `missing_handler.py`, and the finite-number check in the pipeline's `_require_numbers`
   - `method: "sentinel"` in validation and outlier row selection (D7), with `cap_iqr` + `sentinel` rejected
9. **Tools:** docstrings for `profile_dataset`, `detect_outliers`, `find_duplicates` and `clean_dataset` describe the new fields and `method: "sentinel"`, written for a model reader. The `dataquality://docs/quality-score` note on `type_mismatch` mentions the CSV ISO rule.
10. **Tests:**
    - **Fixture figures:** every measured figure above, for D1, D2, D3, D4 (77 raw; 88 for the M3 plan's CSV output, equal to `profile_dataset` on that file) and D5 (the 4-row inline example gives `null`).
    - **D1:**
      - a seeded calibration test: random missingness across sizes and cardinalities lists nothing
      - a column driven by another lists that column, for each of the three measures
      - a sparse comparison column present on 2 rows is skipped
      - 48 categories × 2 rows qualify no pair
      - `null` with no missing values and with no qualifying pair; `[]` when a pair was tested
      - a text target is tested; a text comparison column is not
      - negative phi isn't listed
    - **D2:**
      - a repeated code in a column with few distinct values is not flagged
      - a duplicate row doesn't create a candidate
      - a single 9999 is flagged; a code inside the fences isn't
      - repeated ±inf is never a candidate; `null` fences give `[]`
      - a value on one row plus an exact duplicate of that row: count 2, not a candidate
      - the documented limit: data 1–100 plus two real 1000s lists 1000
    - **D3:**
      - `customer_id` with many repeats is not a key
      - camelCase (`orderKey`, `CustomerID`), separated and spaced names match; `paid`, `valid` and `ORDERID` don't
      - exact duplicates aren't counted as conflicts; the A, A′, B case gives 1 value on 2 rows
      - null keys are ignored
    - **D4:**
      - ISO with times and offsets counts as typed
      - `.CSV` and inline `filename_hint: "data.csv"` count as CSV
      - mixed naive and offset values stay a mismatch
      - `MM/DD/YYYY` stays a mismatch
      - an XLSX text-date column stays a mismatch
      - inline CSV behaves like a file
    - **D5:** 9 shared rows gives `null`, 10 gives a value.
    - **D6:** `[1, inf, NaN]` fills 1, and a column of only ±inf is rejected with the new message.
    - **D7:**
      - `replace_nan` + `sentinel` with the three fixture values nulls exactly the 10 placeholder cells, and `remove` removes those 10 rows
      - `values: [5000]` alone nulls 4 cells, and so does the JSON integer `5000` on a text-numeric column
      - a candidate beyond the report's first 10 can be cleaned
      - a value that isn't a candidate is rejected with the candidates listed
      - `cap_iqr` + `sentinel`, `sentinel` without `values`, and `values` with `iqr` are rejected
    - **pandas 2 and 3:** both pass (CI).
11. **Performance (a gate, not just a report):** `profile_dataset` at 500,000 × 10 within 5 s (it takes 2.9 s now). If it misses, D1's comparisons get tighter caps before the PR opens. A prototype of D1's measures took 0.9 s at that size. Also time a wide file, e.g. 50,000 × 100 with 20 columns missing values, to check D1's column caps, and check that random missingness there lists no association. Report both in the PR.
12. **Docs:**
    - the overview's report schema, the roadmap row for 3.5, and the M3 example in the overview (86 → 88 for the CSV output)
    - `docs/architecture.md`: a short heuristics section with each rule and its limits
    - the README
    - this spec's As built
    - version 0.4.0
13. **Port back to the analyzer** after this merges, as a separate PR: D1 as relabelling (with `"unknown"`), D4, D5 and D6 (D9), together with #14's two mixed-offset fixes: profiling types such a column, and `fix_type` converts to UTC.

**Out of scope:**
- M4 (HTTP transport, Docker, auth, the `file_path` switch)
- text placeholders in categorical columns (`unknown` as a label), and a cleaning step to null arbitrary values
- datetime columns as D1 *comparison* columns (they are still tested as targets)
- user-declared key columns as a tool argument

**CI / ruleset:** no new workflows or job names, so the ruleset's required checks don't change.

**PR title / squash commit message:** `feat: missing-value associations, sentinels, duplicate keys and CSV dates`

---

## As built

- **Fixture figures, all reproduced exactly:**
  - D1: `school_rating` → `age_years`, point-biserial 0.663; `price` and `sqft` → `[]`; columns without gaps → `null`.
  - D2: `price` → −1000 (4), 5000 (4), 9999999 (2); every other column `[]`.
  - D3: `id`, 1 value on rows 13 and 65. The clean sample lists `id` with zeros.
  - D4: raw sample 77 (C). The M3 plan's CSV output is 88 (B), equal to `profile_dataset` on the written file and to the XLSX output.
  - D5: the 4-row inline example gives `null` and no correlation recommendation.
  - D7: `replace_nan` with the three codes nulls 10 cells, and `remove` removes 10 rows.
- **Performance gate (task 11): passed.**
  - `profile_dataset` at 500,000 × 10 takes 3.8 s, against 3.0 s on `main` and the 5 s gate.
  - A first version took 5.2 s. Three changes, all with results identical to before, brought it back under the gate:
    - type detection parses each distinct value once
    - the deduplicated-row mask is computed once per report
    - Cramér's V tables are counted with numpy instead of `pd.crosstab`
  - **Wide file**, 50,000 × 100 with random gaps in 20 columns: 21.5 s against 19.0 s on `main`, and **no association listed**. Of the 21.5 s, 15 s is type detection trying to parse 15 free-text columns as dates, which was the same before this milestone (a candidate for later).
  - `clean_dataset` at 500,000 × 10 takes 8.4 s, including two full reports.
- **Pre-PR review fixes:**
  - **Codex:** a large integer sentinel (9999999999999999) crashed counting, because float conversion changed the key. Fixed.
  - **Standard reviewer:**
    - A constant float column (0.1 everywhere) looked as if it varied because of its mean's rounding error, and produced a false association. Constancy is now decided from the raw values.
    - D1 is vectorised across all targets with matrix products. 200,000 × 50 with gaps in every column takes 0.5 s, down from 20.6 s.
    - Sentinel values print exactly in recommendations, so they can be copied into `values`.
    - The docstring is corrected: all-9s codes are listed in any column.
    - A tool-level XLSX text-date test was added.
- **Task 0 finding:** in lax mode, Pydantic turned `values: [true]` into `1.0` and `"5"` into `5.0` before the core could reject them. `tools/inputs.py` therefore validates `HandleOutliersStep` in strict mode, which still accepts JSON integers (`5000` → `5000.0`). The core rejects bools and non-finite values too, for direct callers.
- **Small differences from the spec's wording:**
  - The report builders take `input_format` (".csv" or ".xlsx"). Without it, as for a direct call with a DataFrame, the M1 rule applies, so ISO text stays a mismatch.
  - A single-column dataset has no other column to tell rows apart, so rows sharing a value are exact duplicate rows. The repeat test then never fires, and only the all-9s test can list a value. This follows from D2's definition, and a test documents it.
  - The correlations section's Cramér's V keeps SciPy's default Yates correction on 2 × 2 tables (analyzer parity). D1's bias-corrected V uses none, as specified.
  - Recommendations read the new fields with `.get`, so a report built without them (as in the recommendation tests) still works.
- **Tests:** 531 pass on Python 3.12 / pandas 3 and on Python 3.10 / pandas 2, with 98% coverage.
- **Version:** 0.4.0.
- **Port-back list (task 13),** for a separate analyzer PR after this merges:
  - D1, as a relabelling with `"unknown"`
  - D4, D5 and D6
  - #14's two mixed-offset fixes
