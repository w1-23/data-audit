# Inputs and usage

## Default review

The default path is one explicitly scoped review and one annotated source copy.

```sh
data-audit review-sheet input.xlsx --options review-options.json --snapshot reviewed.json
data-audit export reviewed.json --output input_审查标注.xlsx
```

A minimal options file for two regions is:

```json
{
  "regions": [
    {"sheet": "Measurements", "bounds": "A3:Z25", "header_rows": [3], "region_id": "upper"},
    {"sheet": "Measurements", "bounds": "A27:Z49", "header_rows": [27], "region_id": "lower"}
  ],
  "field_roles": {
    "sample_keys": ["sample_id"],
    "group_columns": ["treatment"],
    "value_columns": ["value", "response"],
    "required_fields": ["sample_id", "value"],
    "special_tokens": {"ND": "not_detected", "<LOD": "below_lod"}
  }
}
```

`review-sheet` defaults to `--profile balanced`. Profiles are `balanced`,
`duplicates`, `relations`, `numeric_forensics` and `custom`; `--checks` can
select explicit rule names. Only requested/preconditioned checks run. The first
reply is compact; the private snapshot keeps full findings and parsed values so
export never rescans.

## Annotated output

Default export writes the selected original sheets unchanged and adds one visible
`审查结果` sheet with `位置 | 类型 | 结论`. Original values, formula expressions,
cached values, merges and unreviewed sheets are retained. The position cell links
to the first source location. A relation links both ranges and marks both sides;
it does not invent which side was copied.

Debug appendices are opt-in:

```sh
data-audit export reviewed.json --output audit.xlsx --issue-list --overview --rules
```

Do not treat an issue list, rule catalogue, HTML page or snapshot JSON as the
default user deliverable. A zero-finding workbook is still one annotated XLSX,
with no false red marks.

## Reading scope

`inspect` supplies bounded layout hints. Explicit regions accept absolute A1
bounds and header rows after row 24. The inherited whole-sheet `--header-row`
path accepts rows 1–20. CSV/TSV positions are logical records and fields. XLS/XLSB
uses Calamine. PDF/DOCX table positions are logical, not Excel cell coordinates.
Formulas are evaluated from the saved cache only; the package never invents a
fresh cache.

## Checks and prerequisites

- `balanced` batches basic structure/type/missing checks and PaperConan-adapted
  exact column/row relations plus repeated blocks. ID, sample, group and replicate
  columns are excluded from numeric relationship comparisons when roles are
  declared.
- `duplicates` activates whole-row duplicates and exact/block copy detectors only.
- `relations` activates constant offset, constant ratio and exact identity in both
  row and column directions. The equation's left/right order is comparison order,
  not a claim about which side was copied.
- `iqr_outlier` requires declared groups with at least eight eligible values and
  positive IQR. It is exploratory, not an automatic deletion recommendation.
- `geng_last_digit` and `geng_decimal_consistency` require confirmed literal
  numeric tokens. `geng_benford` additionally requires a confirmed natural
  cross-magnitude mechanism. `geng_grim` requires a row binding plus mean, n,
  reported decimals and an explicit value range. All return review leads without
  upstream risk scores. SPRITE and image duplication are not part of the locked
  runtime; they remain explicit `NOT_RUN` capabilities.
- A declared special token is retained and is not marked as a numeric error.
  An undeclared `ND`, `<LOD`, `BLQ`, `NA`, `#N/A` or similar value is reported as
  “meaning to confirm”; it is never silently converted to zero, missing or LOD/2.

## Compatibility and snapshots

The pre-3.1 `review` CLI/API, `review_bytes`, `audit_table`, snapshot schema
`2.0.0`, old rule identifiers, compare methods and `presentation="legacy"` remain
available for older hosts and saved reviews. A new task should use `review_sheet`
once, not run the old and new orchestrators in sequence.

The private snapshot includes source bytes for offline rendering and binds them
to the source SHA and canonical report digest. If a host stores only the report,
it must re-bind the currently authorized raw bytes only after verifying the saved
source SHA. Snapshots detect accidental change; they are not signatures.

## Python

```python
from citrus_audit import AuditSnapshot, export_workbook, review_sheet

snapshot = review_sheet(data, "input.xlsx", regions=regions, field_roles=roles)
payload = snapshot.to_dict()                 # private JSON persistence
restored = AuditSnapshot.from_dict(payload)
blob, receipt = export_workbook(restored)    # one annotated XLSX
```

## Explicit reported summaries and P values

```python
snapshot = review_sheet(
    data, "input.xlsx", regions=regions, field_roles=roles,
    reported_summaries=[{
        "raw": {"region_id": "raw", "column": "value", "rows": [4, 5, 6, 7, 8]},
        "reported": {"region_id": "summary", "row": 3,
                     "columns": {"n": "n", "sum": "sum", "mean": "mean", "sd": "sd", "se": "se"}},
        "decimals": {"sum": 0, "mean": 2, "sd": 3, "se": 3},
        "sample_unit_confirmed": True,
        "same_units_confirmed": True,
        "sd_kind": "sample",
    }],
    reported_p_values=[{
        "mode": "statistic", "distribution": "t", "df": 18, "tails": "two",
        "statistic": {"region_id": "stats", "row": 12, "column": "t"},
        "reported_p": {"region_id": "stats", "row": 12, "column": "p", "decimals": 3},
        "location": {"region_id": "stats", "row": 12, "column": "p"},
    }],
)
```

For raw-sample P values use `mode="samples"` and either literal `groups` or
column selectors such as
`{"region_id": "raw", "column": "value", "rows": [4, 5, 6, 7, 8]}`. Supported
tests are `welch_t`, `student_t`, `paired_t`, `one_way_anova`, `pearson` and
`chi2_contingency`; set `correction=True` only when Yates correction is the
declared design. `P<0.001` and `P<=0.05` remain inequalities. Unresolved
bindings are reported as coverage gaps and make the review `partial`; a real
finding does not by itself make execution fail.
