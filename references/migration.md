# Migration map

Source is `w1-23/CitrusScholar@d275430d3ef01bf9558ccd85204d1bdede631989`. Source paths below are under `packages/citrus_audit/`. Destination paths are under `citrus_audit/`. Names remain stable; the four new row rules are listed after the three baseline inventories.

## Table rules (38)

Each named row has a parameterized trigger and receipt assertion in `tests/catalog_rules_test.py::test_migrated_rule`. A diagnostic test verifies its diagnostic or coverage effect. Counts exclude both the cross-file methods and plan comparisons.

| Baseline rule | Source → destination | Verified semantics |
|---|---|---|
| `duplicate_composite_key` | `methods.py::_optional_checks` | A repeated complete explicit key tuple triggers. An incomplete key is excluded. |
| `raw_numeric_tail` | `methods.py::_optional_checks`; `readers.py` | Actual decimal tokens ending in zeros retain that lexical tail with original rows. |
| `adjacent_equal_differences` | `methods.py::_optional_checks` | A finite, distinct three-value progression triggers at original positions. |
| `sliding_equal_differences` | `methods.py::_optional_checks` | A configured contiguous window with equal first differences triggers. |
| `repeated_sequences` | `methods.py::_optional_checks` | Two populated disjoint windows match without compressing the missing row between them. |
| `repeated_blocks` | `methods.py::_optional_checks` | Two disjoint contiguous `window × 2` blocks match. Across-row `2 × window` also has an independent coordinate test. |
| `expected_experiment_structure` | `methods.py::_structure_checks` | An explicit expected factor cell lacks a distinct experimental unit. |
| `required_field_absent` | `methods.py::audit_frame` | An explicitly required field missing from the header generates a field diagnostic. |
| `integer_summary_not_checked` | `methods.py::audit_frame`; `summary_consistency.py::check_integer_summary` | Missing reported precision yields an explicit notice. |
| `empty_table` | `methods.py::audit_frame` | A header-only table has an empty-data finding. Other row checks lack inputs. |
| `empty_column` | `methods.py::audit_frame` | An all-blank nonrequired column is identified. |
| `missing_values` | `methods.py::audit_frame` | A partially populated field at the 50% missing threshold gives a column summary. |
| `missing_cell` | `methods.py::audit_frame` | A blank required cell is located and flagged. Global output truncation is also tested. |
| `constant_field` | `methods.py::audit_frame` | One distinct nonempty value yields a field observation. |
| `mixed_numeric_text` | `methods.py::audit_frame` | A mixed numeric/text field yields a column observation. |
| `non_numeric_cell` | `methods.py::audit_frame` | Text in a declared measurement is reported even with a small sample. |
| `iqr_outlier` | `methods.py::_group_outlier_column` | Eight numeric observations in a declared group support an exploratory 1.5×IQR lead. |
| `iqr_insufficient` | `methods.py::_group_outlier_column` | A declared group below the sample floor generates a coverage notice. |
| `out_of_range` | `methods.py::audit_frame` | A numeric value violates its explicit upper bound. |
| `logic_conflict` | `methods.py::audit_frame` | An explicit same-row `left <= right` relation is violated. |
| `duplicate_key` | `methods.py::_duplicate_checks` | Repeated nonmissing declared key values are located. |
| `duplicate_row` | `methods.py::_duplicate_checks` | Identical source rows are retained and flagged for review. |
| `multi_header_suspected` | `methods.py::audit_frame` | Text in multiple otherwise numeric fields marks the first data row as a possible second header. |
| `optional_column` | `methods.py::audit_frame` | An unconfigured notes field is recognized as optional. |
| `identical_columns` | `methods.py::audit_frame` | Exact equality on common finite rows reports both columns. |
| `constant_offset_columns` | `methods.py::audit_frame`; `integrity.py::_transform` | A constant shift reports both columns. A large baseline cannot hide an altered cell. |
| `constant_ratio_columns` | `methods.py::audit_frame`; `integrity.py::_transform` | A fixed multiplier reports both columns. |
| `exact_linear_columns` | `methods.py::audit_frame`; `integrity.py::_transform` | An affine relation reports the coordinate pairs and coefficients. |
| `decimal_ending_concentration` | `methods.py::audit_frame` | Explicit six-decimal formatting detects concentrated endings with enough diverse values. This method uses formatted floats. |
| `unequal_group_size` | `methods.py::audit_frame` | Declared groups with unequal counts yield a descriptive lead. |
| `unequal_replicates` | `methods.py::audit_frame` | Distinct nonempty replicate IDs differ across declared groups. |
| `integer_mean_inconsistent` | `methods.py::audit_frame`; `summary_consistency.py::check_integer_summary` | Rounded mean and sample count imply a sum interval containing no integer. |
| `integer_summary_inconsistent` | `methods.py::audit_frame`; `summary_consistency.py::check_integer_summary` | Confirmed sample SD and reported precision imply a sum-of-squares interval containing no integer. |
| `design_checks_skipped` | `methods.py::audit_frame` | Missing group declarations generate a coverage notice. |
| `invalid_group_mapping` | `methods.py::audit_frame` | A missing group label reports an invalid mapping. |
| `invalid_value_mapping` | `methods.py::audit_frame` | A missing measurement label reports an invalid mapping. |
| `grim_sd_skipped` | `methods.py::audit_frame` | An unconfirmed SD convention yields a coverage notice when mean and n were bound. |
| `findings_truncated` | `methods.py::audit_frame` | More than 20,000 findings produces a coverage notice with the omitted total and a truncated rule receipt. |

## Cross-file integrity methods (8)

All map from `integrity.py::audit_numeric_reuse` to the same routine, also callable through `compare_snapshots`. `tests/catalog_integrity_test.py::test_migrated_integrity_method` uses a positive and a nonmatch for each row, and verifies actual execution, matched locations and uncertainty.

| Baseline method | Verified semantics |
|---|---|
| `exact_column` | Exact equality at paired original row ordinals. |
| `reordered_column` | Multiset equality under one value-based row mapping. Tied values keep ambiguity. |
| `constant_offset` | Uniform shift using rational residual comparison. |
| `constant_ratio` | Uniform multiplication with a nonzero pivot. |
| `affine` | Scale and offset from the extrema with rational residual confirmation. |
| `exact_block` | Contiguous complete rectangles match and may extend through contiguous equal neighbors. |
| `reordered_block` | One row permutation applies jointly across all selected columns. Duplicate row tuples keep ambiguity. |
| `shared_decimal_tail` | At least three distinct eligible fixed-scale decimal suffixes match. Evidence is descriptive. |

## Bound comparison entries (3)

All map from `plan.py::parse_plan` and `plan.py::compare_bound` to those same routines. `compare.py::compare_snapshots` builds the standalone bindings from saved snapshots. The inherited `tests/plan_test.py` and `tests/standalone_test.py::test_compare_saved_regions_and_three_distinct_entries` exercise the entries separately and together.

| Baseline entry | Verified semantics |
|---|---|
| `sample_alignment` | Only declared unique keys align. Duplicate keys and missing finite cells retain explicit counts. |
| `finite_rectangle` | Explicit source rows and columns are compared using declared units and tolerances and return original cells. |
| `window_search` | Deterministic bounded search inside the authorized rectangles confirms finite candidates with declared conversion and tolerance. Truncation and unavailable coordinates remain explicit. |

## Additive row direction

`row_patterns.py::check_rows` adds `identical_rows`, `constant_offset_rows`, `constant_ratio_rows`, and `exact_linear_rows`. Each has an independent test for source coordinate retention under asymmetric missingness and for skipping without confirmed units. New horizontal block detection uses the existing `repeated_blocks` ID and reports original 2D coordinates. In all relations, direction describes the axis and equation orientation. The direction of data reuse remains unknown.
