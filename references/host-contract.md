# Host contract

The stable import surface is:

```python
from citrus_audit import AuditSnapshot, export_workbook, review_sheet
```

## Review

```python
snapshot = review_sheet(
    data, name,
    sheet_scope="Sheet1",
    regions=[{"sheet": "Sheet1", "bounds": "A3:Z25", "header_rows": [3], "region_id": "upper"}],
    field_roles={
        "value_columns": ["value"],
        "group_columns": ["treatment"],
        "sample_keys": ["sample_id"],
        "required_fields": ["sample_id", "value"],
        "optional_fields": ["note"],
        "ranges": {"value": {"min": 0, "max": 100}},
        "special_tokens": {"ND": "not_detected", "<LOD": "below_lod"},
        "precision_confirmed": ["value"],
        "benford_confirmed": [],
        "grim": [],
    },
    requested_checks=None,
    reported_summaries=None,
    reported_p_values=None,
    profile="balanced",
)
```

`data` is bytes and `name` is the original filename. When `regions` is supplied,
`header_row`/`header_rows` must not also be supplied. Returned `AuditSnapshot`
uses schema `2.0.0`; `to_dict()`/`from_dict()` are plain JSON, but contain the
source Base64. A host that omits that field can rebuild the in-memory object from
the authorized raw bytes and the saved report:

```python
snapshot = AuditSnapshot(raw_bytes, name, dumps(report), source_sha256)
```

This constructor revalidates the source SHA, report identity, table envelopes
and canonical report digest. Never render from a report whose source hash does
not match the bytes currently authorized for that account/session.

`requested_checks=None` uses the profile. Explicit names include the legacy
38 rules plus `pc_*` PaperConan-adapted checks and `geng_*` specialists.
Friendly aliases are `duplicates`, `relations` and `outliers`. An explicit list
does not silently add unrelated specialists.

## Reported summaries and P values (3.2.0)

Both lists are opt-in and use the same reviewed entries. Selectors use
`region_id` (preferred) or `sheet`; rows are absolute 1-based sheet rows and
columns are actual header names or absolute column numbers. There is no file
index inside `review_sheet`: it reviews one source. Cross-file/cross-sheet
comparison remains the explicit `compare_snapshots` API.

Summary binding:

```python
reported_summaries=[{
    "raw": {"region_id": "raw", "column": "value", "rows": [4, 5, 6, 7, 8]},
    "reported": {"region_id": "summary", "row": 3,
                 "columns": {"n": "n", "sum": "sum", "mean": "mean", "sd": "sd", "se": "se"}},
    "decimals": {"sum": 0, "mean": 2, "sd": 3, "se": 3},
    "sample_unit_confirmed": True,
    "same_units_confirmed": True,
    "sd_kind": "sample",
}]
```

`missing_policy="exclude"` is required before silently dropping any missing raw
cell; otherwise the binding is `not_applicable` with the excluded rows in the
reason. `n` is exact; sum/mean/SD/SE are checked against the reported rounding
interval. SE is compared only with sample SD (`sd_kind="sample"`).

P-value binding from a reported statistic:

```python
reported_p_values=[{
    "mode": "statistic", "distribution": "t", "df": 18, "tails": "two",
    "statistic": {"region_id": "stats", "row": 12, "column": "t"},
    "reported_p": {"region_id": "stats", "row": 12, "column": "p", "decimals": 3},
    "location": {"region_id": "stats", "row": 12, "column": "p"},
}]
```

P-value binding from raw samples (selectors or literal arrays):

```python
reported_p_values=[{
    "mode": "samples", "test": "welch_t", "tails": "two",
    "groups": [
        {"region_id": "raw", "column": "value", "rows": [4, 5, 6, 7, 8]},
        {"region_id": "raw", "column": "value", "rows": [9, 10, 11, 12, 13]},
    ],
    "reported_p": "P<0.05",
}]
```

Supported distributions are `z`, `t`, `chi2` (requires a one-sided tail) and
`f` (requires `df=[df1, df2]`). Supported sample tests are `welch_t`,
`student_t`, `paired_t`, `one_way_anova`, `pearson` and `chi2_contingency`.
`correction=True` applies Yates correction only to 2×2 contingency tables.
`P<0.001` is tested as a strict inequality, never as an exact P of 0.001.
For a declared comparison family, `audit_p_values(..., correction="holm"|"bh")`
returns adjusted P values; `review_sheet` does not invent a correction family.

The saved review includes `review.reported_stats.summaries.checks` and
`review.reported_stats.p_values.checks`. Unresolved explicit bindings are added
to `review.coverage_gaps` and make the review `partial`; a checked discrepancy is
a finding and does not by itself mark execution as failed. Findings use the
`reported_<metric>_differs_from_raw` or `reported_p_value_mismatch` check names
and are exported through the same annotated workbook.

## Export

```python
blob, receipt = export_workbook(snapshot, presentation="annotated")
```

`annotated` is the default and adds exactly one visible `审查结果` sheet. It
consumes the saved findings and does not rescan. Source cell values, formulas,
cached values, merges and unreviewed sheets are preserved. `presentation="legacy"`
keeps the pre-3.1 export behavior for old callers. The export receipt always
includes `marked_locations` (unique marked source cells), `marked_cells`, and the
`findings_references` map; it does not imply that every reported relation should
be rendered as an individual cell error.

If the host's DB intentionally omits `source_base64`, do not synthesize it from
an old cache. Bind the currently authorized raw bytes after checking the saved
`source_sha256`, then call `export_workbook`.

## Finding contract

Findings retain `severity`, `check`, `field`, `row`, `column_index`, `detail`,
`scope`, and optional absolute `evidence.cells` / `evidence.ranges`. New
relation findings also carry their method version and upstream source metadata.
Rows/columns are absolute one-based sheet coordinates, never region-local.
