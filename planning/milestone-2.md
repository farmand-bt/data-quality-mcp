# Milestone 2: Expanded Tool Surface

> **Status: draft for review.** Merging this spec approves its decisions and the schema changes in it; implementation starts afterwards in plan mode.
> Shared context (architecture, report schema, design decisions) is in [project-overview.md](project-overview.md). Milestone 1 is in [milestone-1.md](milestone-1.md).

**Goal:** Fill in the report sections Milestone 1 left `null` (outliers, duplicates, correlations, recommendations), make them part of the quality score, and use the rest of the MCP surface: focused tools, resources, and a prompt.

**Layer boundary:** all analysis is new pure-Python modules in `profiler_core/`. `tools/` gains thin adapters plus two new modules (`resources.py`, `prompts.py`), each with its own `register(app)`. `tests/test_architecture.py` is unchanged and must keep passing.

---

## Guiding principle: consistent with the Data Quality Analyzer

The analysis is **ported from `llm-data-quality-analyzer` with the same behaviour** (`profiler/outliers.py`, `duplicates.py`, `correlations.py`, `recommendations.py`): same methods, thresholds, fields, recommendation rules and score components. Both projects should give the same answers on the same data. A deviation is allowed only when the analyzer's behaviour conflicts with an MCP-specific constraint (result size, no server-side state, an agent as the reader) or with one of this repo's invariants. Every deviation is listed below, so nothing differs silently.

## Decisions to confirm in review

| # | Decision | Recommendation |
|---|---|---|
| D1 | Do duplicates and outliers count in the quality score? | **Yes, as in the analyzer.** The formula already has slots for them. The messy sample drops from 84 (B) to **76 (C)**, and the docs will say scores dropped. |
| D2 | What drives the outlier penalty? | **The analyzer's formula:** the average IQR outlier % across numeric columns that have any outliers, × 2, capped at 10. Messy sample: (2.88% + 1.35%) / 2 = 2.12%, so a penalty of 4.2. |
| D3 | Which z-score? | **The analyzer's classic z-score** (\|x − mean\| / std > 3.0). Known limitation, documented in the docs and the tool docstring: extreme values inflate the std and can hide other outliers. On the messy sample, z flags 2 price outliers while IQR flags 15. IQR is the primary method and the only one used in the score and recommendations, as in the analyzer. |
| D4 | Large row-index lists | **Cap every index list at 50**, the analyzer's existing cap for duplicate samples, applied to outlier lists too, with `indices_truncated` set when a list is cut. Counts and percentages stay exact. The analyzer keeps full outlier lists because its cleaner consumes them from the report. Here an agent reads the report, so a 500k-row file must not produce a multi-MB tool result. M3's cleaning tools will recompute indices server-side with the same core function (on the same loaded data, so no drift) instead of taking them from the agent. |
| D5 | Which resource(s)? *(MCP-only, no analyzer equivalent)* | **Stateless resources:** `dataquality://schema/report` (the report's JSON Schema) and `dataquality://docs/quality-score` (how the score and grade are computed). A `report://last` cache would be server-side state, which would leak one caller's data to another once several HTTP clients connect in M4. |
| D6 | Near-duplicates | **As in the analyzer:** rows identical on every column but one, skipped above 50 columns, reported but not scored. |
| D7 | Tool surface *(MCP-only)* | **Keep `profile_dataset` as the full report and add three focused tools** returning only their section: `detect_outliers`, `find_duplicates`, `analyze_correlations`. Recommendations come only with the full report, because they need every section. |

## Deviations from the analyzer

| Area | Analyzer | Here | Why |
|---|---|---|---|
| Non-numeric columns' `outliers` | zero counts and empty lists | `null` | Invariant: `0` means "checked, none found". Outlier detection doesn't apply to these columns. |
| Index lists | outlier lists uncapped; duplicate samples capped at 50 | every list capped at 50, plus `indices_truncated` | Tool result size (D4) |
| Pearson / Cramér's V | all numeric columns; first 15 categorical columns | at most 50 numeric columns; first 15 categorical columns with at most 50 distinct values each | Bounded result size and runtime on wide or high-cardinality data |
| IQR bounds | not reported (the cleaner recomputes them) | `iqr_lower_bound` / `iqr_upper_bound` per column | Lets an agent explain *why* a value is an outlier. Additive only. |
| Recommendation categories | missing, type, duplicate, outlier, correlation | the same, plus `consistency` | M1 reports inconsistent category labels as a warning instead of a type mismatch, so they need their own category |
| Score | includes the +5 clean-column bonus | no bonus | Already decided and approved in M1 (it partly cancels out real problems) |
| `distribution` section, MCAR/MAR missing patterns | present | not ported | Not in the report schema. A candidate for a later milestone, ported as-is. |

---

## Schema changes (proposed)

These become the contract in `planning/project-overview.md` and `profiler_core/schema.py` once this spec is merged. Row indices are **0-based positions of data rows** (header excluded): index `i` is line `i + 2` of a CSV file.

Example values below are the real results for the `price` column and the duplicates of `tests/fixtures/messy_sample.csv`.

**Per-column `outliers`** (`OutlierInfo`). This is `null` only when outlier detection doesn't apply: non-numeric columns, or fewer than 4 numeric values.

```python
{
    "iqr_count": 15, "iqr_pct": 2.88,
    "iqr_lower_bound": 147204.5, "iqr_upper_bound": 546016.5,
    "iqr_indices": [3, 41, ...],
    "zscore_count": 2, "zscore_pct": 0.38,       # classic z-score; see D3 for its masking limitation
    "zscore_indices": [3, 41, ...],
    "indices_truncated": False,                  # True if any list above hit MAX_REPORTED_INDICES
}
```

**`duplicates`** (`DuplicateInfo`):

```python
{
    "exact_count": 19, "exact_pct": 3.65,        # rows that would be dropped, keeping the first copy
    "sample_indices": [...],                     # every row that belongs to a duplicate group (capped)
    "near_duplicate_count": 2, "near_duplicate_pct": 0.38,
    "near_sample_indices": [...],                # capped
    "indices_truncated": False,
}
```

**`correlations`** (`CorrelationInfo`):

```python
{
    "pearson": {"price": {"price": 1.0, "sqft": 0.02, ...}, ...},       # numeric/mixed columns; null cells where undefined
    "cramers_v": {"neighborhood": {"garage": 0.04}, ...},               # categorical/boolean pairs, upper triangle only
    "high_correlation_pairs": [
        {"col_a": "a", "col_b": "b", "method": "pearson", "value": 0.97},  # |value| >= HIGH_CORRELATION_THRESHOLD, strongest first
    ],
}
```

Limits (see Deviations): Pearson uses at most 50 numeric columns, and Cramér's V at most 15 categorical columns, each with at most 50 distinct values. Columns beyond the limits are skipped, and the docstring says so.

**`recommendations`** (`list[Recommendation]`), sorted critical → warning → info:

```python
{"severity": "critical" | "warning" | "info",
 "category": "missing" | "type" | "consistency" | "duplicate" | "outlier" | "correlation",
 "column": "price" | None,                       # None = dataset-level
 "message": "15 outliers (2.9%, IQR method): review before modeling; ..."}
```

The rules are ported from the analyzer's `recommendations.py`, plus `consistency` for inconsistent category labels. Messages say what to do next.

**Per-column `warnings`:** add an outlier warning when a column's IQR outlier share is above 5%, as in the analyzer.

## Quality score (D1 + D2, same components as the analyzer)

| Component | Penalty | Cap |
|---|---|---|
| Completeness | 2 × overall missing-cell % | 40 |
| Type consistency | 20 × mismatched columns / all columns | 20 |
| Duplicates | 1 × exact-duplicate-row % | 10 |
| Outliers | 2 × average IQR outlier % across numeric columns that have outliers | 10 |

`compute_quality_score()` already takes `duplicate_pct` and `outlier_pct`, so M2 only supplies them. `docs/architecture.md` is updated to match.

---

## Tasks

0. **Before writing code (plan mode):** check against the installed SDK and current docs:
   - How to register resources and prompts from a `register(app)` function: `app.resource(uri, ...)(fn)` / `app.add_resource(...)`, and `app.prompt(...)(fn)` / `app.add_prompt(Prompt.from_function(...))`
   - The `resource_security` defaults (they only matter for templated URIs; D5 uses static ones)
   - The `mime_type` handling for JSON and Markdown resources
   - `Client.list_resources` / `read_resource` / `list_prompts` / `get_prompt`, for the tests
1. **Config:** add `MAX_REPORTED_INDICES = 50`, `MAX_CORRELATION_NUMERIC_COLUMNS = 50`, `MAX_CRAMERS_V_COLUMNS = 15`, `MAX_CRAMERS_V_CARDINALITY = 50`, `MAX_NEAR_DUPLICATE_COLUMNS = 50`, `OUTLIER_COLUMN_WARNING_PCT = 5.0`. The existing `OUTLIER_IQR_MULTIPLIER` (1.5), `OUTLIER_ZSCORE_THRESHOLD` (3.0) and `HIGH_CORRELATION_THRESHOLD` (0.9) already match the analyzer.
2. **`profiler_core/outliers.py`:** `detect_outliers(df, type_info, *, iqr_multiplier, zscore_threshold, max_indices) -> dict[str, OutlierInfo | None]`. Port from the analyzer with the same methods (D2, D3), plus `null` for non-applicable columns, IQR bounds and capped indices (D4).
3. **`profiler_core/duplicates.py`:** `analyze_duplicates(df, *, max_indices, max_near_duplicate_columns) -> DuplicateInfo`. Port, including near-duplicates (D6).
4. **`profiler_core/correlations.py`:** `compute_correlations(df, type_info, *, threshold, ...) -> CorrelationInfo`. Port Pearson and Cramér's V (scipy `chi2_contingency`), with the column and cardinality limits.
5. **`profiler_core/recommendations.py`:** `generate_recommendations(report, *, missing_threshold) -> list[Recommendation]`. Port the rules, add `consistency`, and sort by severity.
6. **`schema.py` + `report.py`:** add the new TypedDicts and wire the four modules into `generate_report`. Every section is computed now. Add the outlier warning and the new score inputs. Expose the section-level functions so the focused tools can call them directly.
7. **`tools/profiling_tools.py`:**
   - Add `detect_outliers(file_path)`, `find_duplicates(file_path)` and `analyze_correlations(file_path)`. Each returns `{"dataset": {...basic info, no score...}, "<section>": ...}` with its own TypedDict return type.
   - Share one small load-and-map-errors helper.
   - Mark all of them read-only.
   - Update `profile_dataset`'s docstring: null now means "not applicable" (outliers on non-numeric columns), not "not computed".
8. **`tools/resources.py`:** the two static resources (D5). The schema resource is generated from `DatasetReport` with Pydantic's `TypeAdapter(...).json_schema()`, so it can't drift from the code. The score doc is a Markdown string kept next to the formula.
9. **`tools/prompts.py`:** a `review_data_quality(file_path)` prompt. It asks the agent to call `profile_dataset`, triage the recommendations by severity, explain the top issues in plain language, and propose an ordered cleaning plan. Static text only, with no data access in the prompt.
10. **`server.py`:** register the new modules and update `INSTRUCTIONS` to mention the focused tools, resources and prompt.
11. **Fixtures:** keep `messy_sample.csv` unchanged so the M1 expectations stay valid. If correlation tests need a strongly correlated pair, add a small separate fixture (for example `tests/fixtures/correlated_sample.csv`) via the generator script.
12. **Tests:**
    - Unit tests per new core module, including edge cases: constant column (IQR = 0 and std = 0), fewer than 4 values, all-NaN column, single-category column, more columns than the limits, index caps with `indices_truncated`.
    - Fixture expectations: 19 exact and 2 near duplicates; `price` has 15 IQR outliers (2.88%) and 2 z-score outliers; `sqft` has 7 IQR outliers; score 76 (C).
    - **Parity tests with the analyzer:** port the analyzer's own outlier, duplicate, correlation and recommendation test cases (`tests/test_profiler.py`), adapted only where a listed deviation applies. That keeps both projects giving the same answers.
    - Score and grade on the messy sample under the new formula.
    - `json.dumps(report, allow_nan=False)` still passes.
    - `test_tools.py`: the new tools are listed with output schemas and return only their section; `list_resources` and `read_resource` work for both URIs, and the schema resource parses as JSON Schema; `list_prompts` and `get_prompt` work; bad paths give actionable errors for every tool.
    - Coverage stays ≥ 85%.
13. **Docs:**
    - `planning/project-overview.md`: schema example
    - `docs/architecture.md`: score table, outlier methods, index semantics, resources and prompt
    - README tools table, plus a "Resources & prompts" section
    - `planning/milestone-2.md`: status and as-built notes
14. **Verify end-to-end:** the MCP Inspector (`make dev`) shows 4 tools, 2 resources and 1 prompt. Run every tool on the messy sample, then run the prompt with that file's path.

**Out of scope:** tools that accept `inline_content` (M3), cleaning tools (M3), distribution and normality analysis (could be a later addition; not in the schema), HTTP transport (M4).

**CI / ruleset:** no new workflows or job names, so the ruleset's required checks don't change.

**PR title / squash commit message:** `feat: outlier, duplicate and correlation analysis with recommendations, resources and prompt`
