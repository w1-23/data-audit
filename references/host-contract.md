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
