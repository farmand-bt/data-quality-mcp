# Milestone 3: Flexible Input + Cleaning Tools

> **Status: complete.** The spec was approved in PR #8 and implemented on `feat/milestone-3`. See **As built** at the end for figures and small differences.
> Shared context (architecture, report schema, design decisions) is in [project-overview.md](project-overview.md). Where this spec changes a decision recorded there (inline encoding, D2), **this spec wins** until the implementation PR updates the overview. The previous milestones are [milestone-1.md](milestone-1.md) and [milestone-2.md](milestone-2.md).

**Goal:** Let every tool take the data inline instead of a file path, and add cleaning: one tool that applies a list of cleaning steps, ported from the analyzer's `cleaner/`, and reports what changed.

**Layer boundary:** cleaning logic is a new pure-Python package, `cleaner_core/`, which may import `profiler_core/` (both are core) but never `mcp`. `tools/` gains one thin module, `cleaning_tools.py`, with its own `register(app)`. The whole cleaning run happens in one core entry point, `cleaner_core.run_cleaning(...) -> CleaningResult`: load, validate, clean, serialise, write or return, reload, and profile before and after. The tool only passes its arguments to that function and returns the result, as the profiling tools do with the report builders. `tests/test_architecture.py` already walks `cleaner_core/`, so it covers the new package without changes and must keep passing. New domain errors subclass `DataQualityError`, so the tools' existing error mapping turns them into `ToolError` messages that say how to fix the call.

---

## Guiding principle: consistent with the Data Quality Analyzer

The cleaning handlers are **ported from `llm-data-quality-analyzer` with the same behaviour**: `cleaner/missing_handler.py`, `duplicate_handler.py`, `type_fixer.py`, `outlier_handler.py` and `pipeline.py`, plus the step-ordering rule and the input restrictions that live in its UI (`app/components/cleaning_panel.py`). That means the same strategies, the same option names and the same log fields. A deviation is allowed only for an MCP-specific constraint (an agent as the caller, no server-side state, result size) or one of this repo's invariants, or to fix a bug found while porting. Every deviation is listed below. Bug fixes are ported back to the analyzer afterwards, as was done after Milestone 2.

## Decisions to confirm in review

| # | Decision | Recommendation |
|---|---|---|
| D1 | Which tools accept inline data? | **All of them.** The four profiling tools and the new cleaning tool take `file_path`, or `inline_content` plus `filename_hint`, with exactly one of `file_path` and `inline_content`. `file_path` becomes optional. Inline data is what makes the server usable over HTTP in M4, where it can't see the caller's files. |
| D2 | How is inline data encoded? | **CSV as plain text and XLSX as base64, decided by the `filename_hint` extension.** This changes the overview's "Dual Input Mode" decision, which said base64 for everything. Without a code tool, a model has to produce base64 itself, which costs about a third more tokens and is easy to get wrong. CSV text can be sent as-is. XLSX is binary, so base64 is unavoidable, and in practice only programmatic clients send it. The loader's limits (50 MB, 500,000 rows) apply to inline data too. <br>**Hardening:**<br>• **Size of CSV text:** measured in UTF-8 bytes. A leading BOM is stripped, and `\r\n` line endings work as in files.<br>• **Missing `filename_hint`:** inline input without one is rejected with the loader's existing actionable error ("'filename_hint' is required with 'inline_content' so the format can be determined (e.g. 'sales.csv' or 'report.xlsx')"). So `dataset.name` is always defined.<br>• **`filename_hint`:** reduced to its basename with `PureWindowsPath(hint).name`, which splits on both `/` and `\` whatever OS the server runs on, before it becomes `dataset.name`. Extensions match case-insensitively (`.CSV` works), as the loader already does. An unsupported extension gives the existing actionable error.<br>• **Network and device paths:** `file_path` values that are UNC (`\\server\share\...`) or Windows device paths (`\\?\`, `\\.\`) are rejected. Opening a UNC path makes Windows connect to that host over SMB, which can leak the user's NTLM credentials, and a prompt injection in the data could steer an agent there. The check runs on the raw string of both `file_path` and `output_path`, **before any filesystem call** (`resolve()`, `exists()`, `stat()`, and the parent-directory check of `output_path`), because even a stat on a UNC path opens the SMB connection. It normalises `/` to `\` and rejects any path that then starts with `\\`. One rule covers UNC paths, device paths and their forward-slash forms (`//server/share/x.csv`). It applies on Windows only: on POSIX there is no SMB redirection, and `\\server\x` is an ordinary file name. A new `ALLOW_NETWORK_PATHS = False` config setting opts back in for deliberate use of a network share. The same rule applies to `output_path` (D4).<br>• **Base64 size:** before decoding, the loader estimates the decoded size from the encoded length (length × 3/4) and checks that against the 50 MB limit, so an oversized string is never decoded. The loader already does this.<br>• **XLSX expansion:** an XLSX is a zip, so the loader checks the sum of the entries' uncompressed sizes against a new `MAX_XLSX_UNCOMPRESSED_MB` (default 200; shared strings are held as Python `str` objects, which take several times their XML size, so a higher cap could mean gigabytes of memory; workbooks with more XML than that should be sent as CSV) before parsing, and raises `FileTooLargeError` (mapped to an actionable `ToolError`). This guards against decompression bombs, and applies to file and inline input alike. The sizes come from the zip headers, but a forged header doesn't get past the check: Python's `zipfile`, which openpyxl reads through, stops decompressing an entry at its declared size and fails the CRC check on a mismatch. So the declared total bounds what is actually decompressed. The loader also caps the number of zip entries (`MAX_XLSX_ENTRIES`, default 1,000; a normal workbook has a few dozen). Parsing stays bounded: pandas opens workbooks in openpyxl's read-only mode, which streams the sheet XML, and only the first sheet is read, up to the row limit. The part held in memory in full is the shared-strings table, which the uncompressed cap bounds. |
| D3 | Cleaning tool surface *(MCP-only)* | **One `clean_dataset` tool that takes an ordered list of steps**, not one tool per handler. A single operation is a one-step list. One call means one load, one output and one log, instead of chaining intermediate files. It also mirrors the analyzer's flow: choose strategies, then press Apply once. The step list has an exact JSON schema, a list of objects keyed by `action`, so an agent sees every valid strategy. |
| D4 | Where does the cleaned data go? | **Back the same way it came in.** For `file_path` input, the tool writes a **new file** and returns its path. By default that's `<stem>_cleaned.<ext>` next to the input, then `_cleaned_2`, `_cleaned_3` and so on if a name is taken. The default extension is the input's, in lower case (`DATA.CSV` → `DATA_cleaned.csv`). Every loadable format (`.csv`, `.xlsx`) has a writer, so there is always a default. The naming loop moves to the next name on `FileExistsError` from the exclusive-create open, rather than pre-checking with `exists()`, so two racing calls both succeed under different names. **No existing file is ever overwritten**, and no partial file is left behind:<br>• The data is serialised in memory first, so a CSV or XLSX serialisation error happens before any file exists.<br>• The bytes are then written with exclusive create (`"xb"`), so no overwrite is possible even when two calls race.<br>• If the write itself fails (for example, the disk is full), the partial file is deleted and the error is reported.<br>• A directory that can't be written to, such as a read-only folder next to the input, raises `OutputWriteError` (a `DataQualityError`). Its message names the directory and suggests passing an `output_path` elsewhere.<br>**`output_path`** (optional, file input only):<br>• It must be absolute, end in `.csv` or `.xlsx`, not exist yet, and have an existing parent directory.<br>• On Windows, a drive-relative path such as `C:foo.csv` isn't absolute, so it is rejected. UNC and device paths are rejected as for input (D2), unless `ALLOW_NETWORK_PATHS` is set. The extension check is case-insensitive. Symlinked parent directories are followed.<br>• Its extension sets the output format, so CSV → XLSX conversion is allowed, as the analyzer offers both downloads.<br>• Combining it with inline input is an error.<br>**Serialisation**, the same for files and inline `content`, as in the analyzer's downloads:<br>• **CSV:** `index=False`, UTF-8 without a BOM, and pandas' default datetime format (ISO 8601, date only when no value has a time).<br>• **XLSX:** `index=False`, written with openpyxl. Timezone-aware datetimes are converted to UTC and made naive, because Excel can't store a timezone.<br>• **Round trip:** the output must load back through the loader, and tests check this.<br>**Inline input:** the cleaned data comes back in the result as `content`, with the same encoding rule as D2. It is capped by `MAX_INLINE_OUTPUT_MB` (default 1 MB, measured on the encoded string), because the result goes straight into the model's context, and anything much larger would be truncated or fail at the client. Above the cap the call fails with an error that suggests `file_path`. Inline input keeps the loader's limit: profiling results stay small whatever the input size, and programmatic clients (M4) may send more than a model can. M4 can raise the output cap for such clients through config.<br>**Scope and M4:** no server-side state is involved either way, so this design works unchanged in M4. Over stdio, paths can reach anything the user running the server can, which is the normal trust model for a local server. The README states that trust model explicitly. The data being cleaned may itself be untrusted, and a prompt injection in it could steer the agent toward an unexpected `output_path`; no-overwrite limits what that can do, and the client's approval prompt for this non-read-only tool shows the path. Over HTTP, paths would refer to the server's disk, so M4 will add a switch to turn `file_path` input off. A stdio allowed-root option is deferred to M4 along with that switch.<br>**Not chosen:** embedded resources or resource links. A plain string field in the structured result is what every client passes to the model, and it is validated with the rest of the output. |
| D5 | Step order and outlier indices | **The analyzer's semantics.** Outlier rows are computed server-side by the same core function as the report (M2 D4), on the loaded input and without the 50-index cap. That way they are exactly the rows the agent saw in `detect_outliers` or `profile_dataset`. Steps are then stably reordered by the analyzer's rule: type fixes, fills, `cap_iqr` and `replace_nan` first, then outlier `remove`, then row and column drops (`drop_rows`, `drop_column`, `remove_duplicates`). This keeps the original row indices valid, and steps in the same group keep the caller's order. The log lists steps in the order they actually ran. `cap_iqr` recomputes its fences at step time, as in the analyzer. |
| D6 | Validation *(MCP-only)* | **Nothing is written unless every step succeeds.** Static checks run on the whole step list before the first step, type-dependent checks when their step runs, and any failure is an actionable error. This applies the analyzer UI's restrictions in the core, because an agent has no dropdown to stop it: `fill_mean` and `fill_median` only on numeric/mixed columns, and `lowercase` only on categorical ones. It also adds these guards:<br>• **Columns:** the column exists in the input.<br>• **One step per issue, as in the analyzer's UI**, which offers one choice per column for each of missing values, type and outliers, plus one duplicates choice. So there is at most one `handle_missing`, one `fix_type` and one `handle_outliers` step per column, and at most one `remove_duplicates`. This rules out conflicts that D5's reordering would otherwise hide. For example, `drop_rows` plus `fill_median` on one column would run the fill first and make the drop a silent no-op.<br>• **No steps on a dropped column:** a `fix_type` or `handle_outliers` step on a column that `drop_column` removes is rejected, because it would have no effect.<br>• **Outlier steps** only target columns where detection applies **on the input**, i.e. the column's `outliers` is not `null` in `detect_outliers`. This follows from D5: outlier rows come from the input. The analyzer's UI is stricter still, offering outlier steps only for columns that have outliers in the original report. Here a column with none is a logged no-op. A mixed column such as `sqft` qualifies. A column that only becomes numeric through `fix_type` (e.g. `"$1,200"` strings) does not, and the error says to convert it in this call, then treat outliers in a second `clean_dataset` call on the output.<br>• **`method`** chooses which rows `remove` and `replace_nan` act on. `cap_iqr` always clips to the IQR fences, so `method: "zscore"` with `cap_iqr` is rejected rather than silently ignored.<br>• **`fill_constant`:** it requires a `value`, which is checked and converted when the step runs:<br>&nbsp;&nbsp;– a numeric column needs a finite JSON number. `true`/`false` are rejected even though `bool` is an `int` in Python, and so are `NaN` and `±Infinity` (Python's JSON parser accepts them), so the output stays strictly JSON-safe and loadable<br>&nbsp;&nbsp;– a boolean column needs `true` or `false`<br>&nbsp;&nbsp;– a datetime column needs a date string that `pd.to_datetime` parses. Its timezone-awareness must match the column's: a timezone-aware value for a naive column, or the reverse, is an error rather than a silent conversion<br>&nbsp;&nbsp;– any other column stores the value as a string (the analyzer's behaviour)<br>&nbsp;&nbsp;A value that doesn't fit is an error. Schema validation does no coercion: Pydantic's smart-mode union keeps `1`, `"1"` and `true` as the types they were sent as, and tests pin this.<br>• **Empty result:** steps that would leave no rows or no columns are rejected (for example `drop_column` on the last column, or `drop_rows` on a column that is entirely empty), because an empty file can't be loaded or profiled.<br>• **Columns with no values:** `fill_mean` and `fill_median` on an all-null column fall under the "no numeric values" rule. `fill_mode` there is a logged no-op, as in the analyzer, where the mode is empty.<br>• **Several steps on one column** are allowed if they are different actions, and they run in order within their D5 group. The meaningful combinations are:<br>&nbsp;&nbsp;– `fix_type` then `handle_missing` (a fill), e.g. convert `sqft` to numeric, then `fill_median`<br>&nbsp;&nbsp;– `fix_type` and/or a fill, plus `handle_outliers`<br>&nbsp;&nbsp;Order matters inside the first group: a fill listed before its `fix_type` runs on the unconverted values, as in the analyzer. The docstring says to list `fix_type` first.<br>• **`output_path`** combined with inline input is rejected (D4).<br>Static rules are checked before the first step runs, and type-dependent ones when their step runs. Either way the output is only written after every step has succeeded. |
| D7 | Log entries | **The analyzer's fields, in one uniform shape, plus `values_nulled`.** Fields that don't apply to an action are present and `null`, never omitted. `values_nulled` counts non-null cells in the step's column that became null. That loss is otherwise invisible: on the messy sample, converting `sqft` to numeric silently turns 11 values (`TBD` ×6, `unknown` ×5) into nulls. `original_dtype` and `new_dtype` are raw pandas dtypes, as in the analyzer and in the report's `pandas_dtype`, so they differ between pandas versions: a string column is `object` on pandas 2 and `str` on pandas 3, and datetimes are `datetime64[ns]` or `datetime64[us]`. Tests assert on the dtype kind, not the raw string. |
| D8 | Before/after summary | **The analyzer's before/after panel as data:** rows, columns, missing cells, exact duplicates, quality score and grade, for input and output. Both sides come from `generate_report`, so they use the same definitions. **`after` profiles the written output**, i.e. the serialised bytes loaded back through the loader, not the in-memory frame. It therefore always matches what `profile_dataset` on the output returns. Measured on the plan below: CSV has no types, so `sale_date` reloads as text and is flagged again as dates stored as text. The output then scores **86 (B)**, against 88 for the in-memory frame or an XLSX output, which keeps the dates typed. The analyzer scores the in-memory frame, so its panel can promise a score that the downloaded CSV doesn't reach. The agent can call `profile_dataset` on the output for full detail. |
| D9 | Profiling additions from the Claude Desktop review: missing-value patterns (MCAR/MAR/MNAR), sentinel values, duplicate IDs | **Not in M3; propose them in a separate short spec after M3.** Measured on the messy sample: <br>• **Missing-value patterns:** the analyzer's heuristic labels `school_rating` **MCAR**, although its missingness is driven by `age_years`: 134 of its 160 missing rows have `age_years > 60`, a correlation of 0.66 between the missing indicator and age. The heuristic only checks whether columns go missing together, and calls anything above 60% missing "MNAR", which data alone can't establish. Porting it unchanged would mislabel exactly the case the review spotted, so it needs its own design and then a port back. <br>• **Sentinel values:** the injected price sentinels (−1,000 ×4, 5,000 ×4, 9,999,999 ×2) are all already among the 15 IQR outliers, so `handle_outliers` with `replace_nan` can clean them in M3. <br>• **Duplicate IDs:** `id` repeats 20 times against 19 exact-duplicate rows, so the fixture only shows one extra case. That's too thin to design a heuristic and its tests around. <br>All three are new heuristics with no analyzer behaviour to port. Keeping them separate keeps M3 to input and cleaning. <br>The same follow-up spec should also reconsider one M1 behaviour (shared with the analyzer): CSV dates are always text, so even a column with one consistent ISO format is flagged as a type mismatch (see D8). |

## Deviations from the analyzer

| Area | Analyzer | Here | Why |
|---|---|---|---|
| Outlier indices for cleaning | taken from the report the UI holds | recomputed server-side by the same function, on the same loaded data, uncapped | No server-side state, and reported index lists are capped (M2 D4). Same rows. |
| Two outlier `remove` steps *(bug)* | each removal resets the row index, so the second step's indices point at shifted rows: it skips them or **removes the wrong row** (reproduced: removing `a`'s outlier, then `b`'s, deletes an ordinary row and keeps `b = 999`) | original row labels are kept until every outlier step has run, then the index is reset once | Correctness; port back |
| `fill_mean` / `fill_median` on a column with no numeric values *(bug)* | crashes with `ValueError: cannot convert float NaN to integer` (the "all integers?" check is true for an empty series, so it calls `round(nan)`) | rejected by validation (D6) with a clear message | Correctness; port back |
| Before/after score | scored on the in-memory cleaned frame | scored on the written output, loaded back (D8) | Matches what `profile_dataset` on the output says (86 vs 88 on the example as CSV) |
| Before/after "Exact Duplicates" *(UI bug)* | before = rows dropped keeping the first copy; after = every row in a duplicate group (`keep=False`), so the delta mixes two definitions | both sides use `exact_count` (D8) | Correctness; port back to the UI |
| Input restrictions | enforced only by the UI's dropdowns | enforced in the core, before running (D6) | An agent has no dropdown |
| Step ordering | done by the UI (`_step_priority`), not the pipeline | done in `cleaner_core`, with the same rule (D5) | The tool is the only caller |
| `fill_constant` value | always a string (from a text box), so filling a numeric column with `0` stores the text `"0"` | a JSON scalar, converted to the column's type when the step runs (number or date); other columns store a string, as in the analyzer (D6) | Agents send typed values; a numeric column stays numeric |
| `method: zscore` with `cap_iqr` | allowed; the z-score rows only feed the log count, and clipping uses the IQR fences | rejected (D6) | The option has no effect, so accepting it would mislead the caller |
| `cap_iqr` `outliers_treated` | the number of report indices, even though the fences are recomputed at step time | the number of values actually clipped | Accurate log, since an earlier fill can move the fences |
| Log fields | different keys per action | one shape, plus `values_nulled` (D7) | Output schema, and the invariant "log how many rows were affected" |
| Output format | download both CSV and XLSX | one file in the format of `output_path` (default: same as input); inline output matches the input format | One output per call |
| UNC and device paths *(this repo's M1/M2 loader, not the analyzer)* | the profiling tools accepted any `file_path` | rejected on Windows for `file_path` and `output_path` (D2). This also blocks `\\?\C:\...` long paths. `ALLOW_NETWORK_PATHS` opts back in; POSIX is unaffected | Avoids NTLM credential leaks over SMB when untrusted data steers an agent |
| Spreadsheet formula injection | cells starting with `=`, `+`, `-` or `@` are written as-is | the same: no escaping (out of scope) | Escaping would alter data, e.g. negative numbers stored as text. The README trust note warns that a cleaned file from untrusted data can contain live formulas when opened in Excel |
| Missing-value pattern classification | present | still not ported (D9) | Measured mislabelling; separate spec |

---

## Schema changes (proposed)

These become the contract in `planning/project-overview.md` and the code once this spec is merged. **The report (`DatasetReport`) is unchanged.** Where the types live:
- **`profiler_core/schema.py`** keeps every output type, the cleaning results included: `CleaningResult`, `CleaningOutput`, `CleaningLogEntry` and the before/after `QualitySnapshot`. It stays the single home of the output contract that CLAUDE.md names, and `CleaningResult` reuses `DatasetSummary` from there instead of a duplicate.
- **`cleaner_core/schema.py`** holds only the cleaning *input* types: the four step TypedDicts and their discriminated union.

**Tool inputs (all five tools):** `file_path: str | None`, `inline_content: str | None` and `filename_hint: str | None`. `file_path` is no longer required. For inline input, `dataset.source` is `"inline_content"` (already in the `InputSource` literal) and `dataset.name` is `filename_hint`.

**`clean_dataset` input:** the above, plus `steps` (1 to 50 items) and an optional `output_path` (file input only). Each step is one of:

```python
{"action": "handle_missing", "column": str,
 "strategy": "drop_rows" | "drop_column" | "fill_mean" | "fill_median" | "fill_mode"
           | "fill_constant" | "ffill" | "bfill",
 "value": str | int | float | bool}               # NotRequired; required (and non-null) for fill_constant only
{"action": "remove_duplicates", "keep": "first" | "last" | "none"}           # the string "none" (not JSON null) = drop every copy
{"action": "fix_type", "column": str,
 "target_type": "numeric" | "datetime" | "categorical" | "string" | "boolean" | "lowercase"}
{"action": "handle_outliers", "column": str,
 "strategy": "remove" | "cap_iqr" | "replace_nan",
 "method": "iqr" | "zscore"}                      # NotRequired, default "iqr"; which rows count as outliers
```

In the step TypedDicts, `value` and `method` are `NotRequired`, and the core enforces the per-strategy rules. A `fill_constant` step without `value`, or with `value: null`, is rejected: filling missing values with null would do nothing. `value` on any other strategy is rejected, by the same principle as `zscore` with `cap_iqr` (D6): an option with no effect is an error, not silently ignored. The docstring points out that `keep: "none"` is the string `"none"`, not JSON `null`. The log labels it `remove_all`, as in the analyzer, and a test pins that mapping.

**`clean_dataset` output** (`CleaningResult`). The values below are the real results of this six-step plan on `tests/fixtures/messy_sample.csv`, produced with the analyzer's pipeline and step order: `remove_duplicates` (first), `fix_type` on `sqft` (numeric), `sale_date` (datetime) and `garage` (boolean), `fill_median` on `price`, and `remove` on `price` outliers (IQR).

```python
{
    "dataset": {"name": "messy_sample.csv", "source": "file_path", "rows": 520, "columns": 10},
    "output": {
        "path": "C:\\data\\messy_sample_cleaned.csv",   # null for inline input
        "content": None,                                # cleaned data for inline input, else null
        "format": "csv",                                # csv | xlsx
        "rows": 487, "columns": 10,
    },
    "before": {"rows": 520, "columns": 10, "missing_cells": 248, "exact_duplicates": 19,
               "quality_score": 77, "quality_grade": "C"},
    "after":  {"rows": 487, "columns": 10, "missing_cells": 202, "exact_duplicates": 0,
               "quality_score": 86, "quality_grade": "B"},   # profiled from the written CSV (D8); 88 as XLSX
    "log": [                                            # in execution order (see D5)
        {"action": "fix_type", "column": "sqft", "strategy": "to_numeric",
         "rows_before": 520, "rows_after": 520, "rows_removed": 0,
         "values_nulled": 11,                           # 'TBD' x6, 'unknown' x5
         "original_dtype": "str", "new_dtype": "float64",  # raw pandas dtypes ("object" on pandas 2)
         "missing_before": None, "missing_after": None, "outliers_treated": None},
        # ... fix_type sale_date, fix_type garage,
        # handle_missing price fill_median (missing 43 -> 0),
        # handle_outliers price remove (15 treated, 520 -> 505 rows),
        {"action": "remove_duplicates", "column": None, "strategy": "keep_first",
         "rows_before": 505, "rows_after": 487, "rows_removed": 18,
         "values_nulled": None, "original_dtype": None, "new_dtype": None,
         "missing_before": None, "missing_after": None, "outliers_treated": None},
    ],
}
```

`strategy` uses the analyzer's labels: `to_<type>` for type fixes, and `keep_first`, `keep_last` or `remove_all` for duplicates. In the example, deduplication removes 18 rows rather than 19 because the outlier step had already removed both copies of one duplicated row. The remaining issues show up when the output is profiled: `school_rating` is 30.4% missing, and the cleaned data has new IQR outliers against its narrower fences (12 in `price`, 6 in `sqft`).

## Invariant change (needs explicit approval)

Merging this spec also approves one change to `CLAUDE.md`. The cleaning rule under **Behavior** becomes:

> Cleaning is non-destructive: never mutate the input in place, **never overwrite an existing file (the input included), and never leave a partial output file behind**, and log every transformation (what was done, which column, how many rows were affected).

---

## Tasks

0. **Before writing code (plan mode):** check against the installed SDK (2.3.0) and current docs:
   - how optional `str | None` parameters and the `Annotated[..., Field(discriminator="action")]` union of step TypedDicts appear in the tool's input schema, and that the Inspector accepts them
   - the `ToolAnnotations` for a tool that writes new files (`read_only_hint=False`, `destructive_hint=False`, `idempotent_hint=False`; the fields are confirmed to exist)
   - how a large string field in a structured result is passed to clients
1. **Loader (`profiler_core/loader.py`):**
   - Treat CSV `inline_content` as text and XLSX as base64 (D2), with the size check in UTF-8 bytes and the BOM stripped.
   - Reduce `filename_hint` to its basename.
   - Check the uncompressed size of XLSX before parsing it, for both input modes.
   - Update the existing base64-CSV tests.
2. **`cleaner_core/`:**
   - Port the four handlers unchanged, apart from the listed deviations.
   - `pipeline.py`: validation (D6), the stable priority order (D5), original row labels kept through the outlier steps, a uniform log (D7), never mutating the input.
   - `cleaning.py`: `run_cleaning`, the single entry point the tool calls (see **Layer boundary**).
   - `writer.py`: output naming, path checks, in-memory serialisation, an exclusive-create write with cleanup on failure, and the inline output cap (D4).
   - `schema.py`: step and result TypedDicts.
   - Domain errors go in `profiler_core/exceptions.py`, for example `OutputExistsError` and `OutputWriteError`.
3. **Config:** `MAX_CLEANING_STEPS = 50`, `MAX_INLINE_OUTPUT_MB = 1`, `MAX_XLSX_UNCOMPRESSED_MB = 200`, `MAX_XLSX_ENTRIES = 1000`, `ALLOW_NETWORK_PATHS = False`, and the output suffix `_cleaned`. As with every existing limit, the loader, the writer and `run_cleaning` take these as keyword parameters that default to the config values, and never read them as globals.
4. **`profiler_core/report.py`:** expose uncapped outlier rows for the cleaner (same function, `max_indices=None`), and a before/after snapshot builder (D8).
5. **`tools/profiling_tools.py`:** add `inline_content` / `filename_hint` to all four tools through the shared `_run` helper, and update the docstrings (D1, D2).
6. **`tools/cleaning_tools.py`:** `clean_dataset`, with the annotations from task 0. It is a thin adapter: it passes its arguments to `run_cleaning` and maps `DataQualityError` to `ToolError`. The docstring and the over-cap error both say that the 1 MB inline-output cap applies to the encoded string, so for XLSX (base64) it allows about 0.75 MB of file data. Its docstring explains the step schema, the ordering rule, that outlier rows are the ones `detect_outliers` reports, the output naming, the no-overwrite guarantee, inline output, and common errors.
7. **`server.py` / `tools/prompts.py`:** register the cleaning tools and update `INSTRUCTIONS`. `review_data_quality` keeps saying "don't modify without asking", and adds that, once the user agrees, the plan can be applied with `clean_dataset`.
8. **Tests:**
   - **Analyzer parity:** port `tests/test_cleaner.py` (handlers, pipeline, step priority, `cap_iqr` with infinite values) into `TestAnalyzerParity` classes, adapted only where a deviation applies.
   - **Bug fixes:** two outlier removals delete the right rows; `fill_mean` on a column with no numbers is rejected; the before/after duplicates use one definition.
   - **Validation:** each rule in D6 gives an actionable error, and nothing is written. That includes:
     - an outlier step on a column that only becomes numeric through `fix_type`, whose error points to a second call
     - `zscore` combined with `cap_iqr`
     - a `fill_constant` value that doesn't fit the column
     - `output_path` combined with inline input
   - **Fixture plan:** the six-step plan above gives 487 rows and `values_nulled = 11` for `sqft`. The score goes from 77 (C) to 86 (B) for CSV output and to 88 (B) for XLSX output, and `after` equals `profile_dataset` on the written file.
   - **Validation edge cases:** an empty result, `fill_mode` on an all-null column, a second step of the same action on one column (e.g. `drop_rows` plus `fill_median`), a step on a column that `drop_column` removes, allowed combinations running in order, and `fill_constant` typing (`true`, `"1"` and `NaN` for a numeric column, a missing or null `value`, `value` on another strategy, no coercion by the schema, and the `none` → `remove_all` label).
   - **pandas 2 and 3:** `original_dtype` / `new_dtype` are asserted by dtype kind, and `values_nulled` is checked on a pandas 3 `str` column as well as a pandas 2 `object` column (CI runs both).
   - **Output:**
     - default naming and the `_2` suffix
     - two writers racing for the default name both succeed under different names
     - an existing `output_path`, a relative path, a drive-relative Windows path (`C:foo.csv`, Windows only), a missing parent directory or an unsupported extension is refused
     - CSV output has no index column and no BOM, and XLSX output with timezone-aware datetimes loads back as naive UTC
     - CSV → XLSX conversion works
     - a write that fails midway leaves no file behind
     - the input file is unchanged byte for byte
     - CSV and XLSX round-trip
     - inline output decodes to the same data, and output over the cap gives an error that suggests `file_path`
   - **Inline input:**
     - every profiling tool returns the same report for inline and file input (apart from `source`)
     - XLSX base64, invalid base64, a BOM, and `\r\n` line endings
     - a missing `filename_hint`, a `filename_hint` with `/` or `\` directories (reduced to the basename on every OS), an upper-case extension, and an unsupported extension
     - UNC and device paths, including forward-slash forms, rejected for `file_path` and `output_path` on Windows, and accepted with `ALLOW_NETWORK_PATHS`
     - mixed-separator forms (`/\server\share\x.csv`)
     - the rejection happens before any filesystem call, for `output_path` too: the test makes `Path.resolve`, `exists`, `is_file` and `stat` fail if called
     - a read-only output directory gives `OutputWriteError` with the `output_path` hint
     - an upper-case input extension gets a lower-case default output extension
     - both and neither of `file_path` and `inline_content`, at tool level
     - an XLSX over the uncompressed limit or the entry limit
   - **Step schema bounds:** `steps: []` and 51 steps are rejected, `keep: null` is rejected rather than read as `"none"`, and a timezone-aware `fill_constant` value on a naive datetime column is an error.
   - **Tools:** five tools listed, with correct annotations and output schemas, plus the stdio subprocess test.
   - Coverage stays ≥ 85%.
9. **Performance:** time `clean_dataset` on a synthetic 500,000 × 10 file (load, two reports and the steps) and report the number in the PR.
10. **Docs:**
    - `planning/project-overview.md`: both places that say inline data is base64 (the scaffolding note on `loader.py` and **Dual Input Mode**, per D2), tool list, `CleaningResult`, roadmap
    - `README.md`: also the trusted-local note for file paths (D4) and the formula-injection warning for cleaned files
    - `docs/architecture.md`: the cleaning layer, step order, output rules
    - `README.md`: tools table, inline input, a cleaning example
    - `CLAUDE.md`: the cleaning invariant as worded under **Invariant change**
    - this spec: status and as-built notes
    - version 0.3.0
11. **Verify end-to-end:**
    - MCP Inspector: five tools, the plan above on the messy example, inline input.
    - Claude Desktop (after `uv tool install --editable . --reinstall`): profile, then clean, then profile the output.
12. **Port back to the analyzer** after this merges, as a separate PR there: the pipeline row-label fix, the `fill_mean` / `fill_median` guard, and the before/after duplicates metric in the UI.

**Out of scope:** the profiling additions in D9 (separate spec after M3), HTTP transport and switching `file_path` off (M4), near-duplicate removal (the analyzer has no handler for it), and a dry-run mode (the output never replaces anything, so a preview would just be the same call).

**CI / ruleset:** no new workflows or job names, so the ruleset's required checks don't change.

**PR title / squash commit message:** `feat: inline data input and clean_dataset tool`

---

## As built

- **Figures:** the six-step plan reproduces exactly:
  - 520 → 487 rows, and `values_nulled = 11` for `sqft`
  - 77 (C) → 86 (B) as CSV and 88 (B) as XLSX
  - the log as shown under Schema changes
  - `profile_dataset` on the written file also reports 86 (B)
- **Performance:** at 500,000 rows × 10 columns (a 23 MB CSV), `clean_dataset` takes about 7 s, covering the load, two reports, five steps, serialisation and the reload. `profile_dataset` alone takes about 3 s.
- **Small addition to D2:** a single-line CSV payload that is valid base64 and decodes to multi-line text is rejected with "send CSV as plain text". That is the format inline CSV used before this milestone, and without the check it would parse silently as a one-column table.
- **Layering:** the step TypedDicts in `cleaner_core/schema.py` form a plain union. The Pydantic discriminator annotation is added in `tools/inputs.py`, so the core needs no schema library. Shared tool argument types live in `tools/inputs.py`.
- **Handlers** raise the domain `InvalidInputError` for unknown strategies, where the analyzer raises `ValueError`. The schema makes these unreachable from the tool; the parity tests are adapted for it.
- **Type-dependent checks run at step time:** `fill_mean`/`fill_median` check the column's *current* type, so `fix_type` to numeric followed by `fill_median` on the same column works in one call. Outlier steps still require detection on the input (D5, D6).
- **CSV output** uses `\n` line endings on every OS, for deterministic output.
- **Known pandas 2 warning:** filling a boolean `object` column triggers pandas 2's `FutureWarning` about downcasting. This comes from the analyzer's `fillna` call, ported as is, and is harmless.
- **Tests:** 409 pass on Python 3.12 / pandas 3 and on Python 3.10 / pandas 2, with 98% coverage. They include the analyzer's cleaner tests in `TestAnalyzerParity*` classes, the three bug fixes, every D6 rule, the writer guarantees and inline input.
- **Version:** 0.3.0.
