# Inputs and CLI

Install the package before using `scripts/audit.py`. The `catalog` command lists the 38 migrated table rules, four row extensions, eight cross-file integrity methods, and three bound comparison entries separately.

```sh
data-audit inspect input.xlsx --sheet Measurements --output layout.json
data-audit review input.xlsx --options review-options.json --snapshot reviewed.json \
  --checks empty_table duplicate_row missing_cell non_numeric_cell out_of_range constant_offset_rows
data-audit report reviewed.json --output full-report.json
data-audit export reviewed.json --output annotated.xlsx --issue-list --overview --rules
```

A possible `review-options.json` for two regions of the same sheet is:

```json
{
  "regions": [
    {"sheet": "Measurements", "bounds": "D31:AC58", "header_rows": [31], "region_id": "batch-a"},
    {"sheet": "Measurements", "bounds": "AF31:BE58", "header_rows": [31], "region_id": "batch-b"}
  ],
  "required_fields": ["value"],
  "values": ["value", "t1", "t2", "t3", "t4", "t5"],
  "ranges": {"value": {"min": 0, "max": 100}},
  "row_relationships": {
    "columns": ["t1", "t2", "t3", "t4", "t5"],
    "same_units_confirmed": true,
    "absolute_tolerance": "0.0001",
    "relative_tolerance": "0"
  }
}
```

Run on the confirmed rectangles. `inspect` uses bounded XLSX/XLSM layout discovery and supplies hints, including regions without confirmed header rows. Explicit regions accept original A1 bounds and header rows anywhere in the supported sheet coordinates, including after row 24. The inherited whole-sheet `--header-row` path accepts rows 1–20. Region options apply to each selected table. CSV/TSV positions are logical records and fields. XLS/XLSB uses Calamine. Text DOCX and extractable PDF tables use logical table coordinates. Scans, OCR, and automatic rebuilding of complex document layouts are outside the table reader.

- `groups` and `values` explicitly select grouping and measurements for `iqr_outlier`. Each group needs at least eight eligible values with positive IQR. An outlier is exploratory.
- `key_columns`, `composite_keys`, `ranges`, `relations`, and `expected_structure` are declarations supplied by the user or data dictionary. See [the migration map](migration.md) and the synthetic test cases for parameter examples.
- `pattern_checks` accepts `columns`, selected booleans such as `repeated_blocks`, `window` (3–100), and `order_confirmed`. The inherited `axis: "rows"` means down a column, and `axis: "columns"` means across a row. Across-row checks also require `same_units_confirmed`. Gaps interrupt windows in either direction.
- `row_relationships` compares distinct rows over declared same-unit columns. It uses the shared exact rational transform implementation, requires at least five common finite columns and 80% common coverage, and caps the search at 5,000 pairs. A stronger selected match can suppress lower-priority relationship checks for a pair. The receipt explains that case. Algebraic `right = scale * left + offset` describes the comparison order.
- OOXML reports give formula expression and cache status independently of the parsed values. The calculations use stored caches when present. A blank or missing cache remains unknown and its freshness is never inferred. Display formats do not establish measurement precision.

## Saved results and comparison

`review` writes the source bytes, source SHA-256, full report, and canonical report SHA-256 into a private snapshot. `report` extracts the exact canonical report bytes. Table entries expose `columns`, `values`, `row_numbers` and `column_numbers` for subsequent analysis or chart input. Deserialized `TableEnvelope` objects provide `to_frame()` for local calculations.

`compare` reads snapshots and a plan. An example for matching the same named measurements by declared unique sample IDs:

```json
{
  "schema": "audit-plan/1",
  "mode": "research_integrity",
  "cross_file_methods": [],
  "comparisons": [
    {
      "method": "sample_alignment",
      "units": ["mg", "mg"],
      "left": {"file_index": 0, "sheet": "Measurements", "region_id": "batch-a", "columns": ["value"], "key_columns": ["sample"]},
      "right": {"file_index": 1, "sheet": "Measurements", "region_id": "batch-b", "columns": ["value"], "key_columns": ["sample"]}
    }
  ]
}
```

```sh
data-audit compare left.json right.json --plan compare-plan.json --output comparisons.json
```

Use `cross_file_methods` for the eight declared searches over the provided snapshots. Compare entries bind their selections explicitly. Different units require a conversion factor. Use `region_id` or `region_bounds` to disambiguate multiple regions on one sheet. Duplicate and missing sample keys are excluded from matching, with counts retained. See `tests/plan_test.py` for finite-rectangle and window search bindings and their limits.

## Python

```python
from pathlib import Path
from citrus_audit import AuditSnapshot, export_workbook, review_bytes
from citrus_audit.reporting import dumps, loads

source = Path('input.csv')
snapshot = review_bytes(source.read_bytes(), source.name, checks=['missing_cell', 'duplicate_row'])
receipt = snapshot.compact()
restored = AuditSnapshot.from_dict(loads(dumps(snapshot.to_dict())))
annotated, export_receipt = export_workbook(restored, issue_list=True)
```

`audit_table`, `review_bytes(...).report`, `compare_snapshots`, and the CLI use plain strict JSON values. At the local Python calculation layer, `TableEnvelope`, `IntegrityTable`, `AuditSnapshot`, and pandas frames retain typed information. Snapshots are trusted workspace files. Their digests detect accidental changes and are not signatures. Exports consume the saved findings and original bytes. XLSX/XLSM exports keep original cell values and caches. Other inputs are rendered as snapshot-derived XLSX. Comments and styles apply only to reviewed coordinates by default. XLSX export checks original cell data and merge integrity. Advanced third-party workbook objects are not covered by this guarantee.

CLI responses use `complete`, `partial`, or `error` for reviews, with individual rule state and coverage in the report. `--help` is human-readable. `error` exits with code 1. A partial receipt exits with code 0 and preserves available material. Files are written only to explicitly requested new paths. Full JSON excludes source Base64 except in the saved snapshot.
