# Standalone verification

Baseline is `d275430d3ef01bf9558ccd85204d1bdede631989`. Validation used Linux x86-64, glibc 2.39, CPython 3.12.3 and `requirements-linux-cp312.lock`. All fixtures are generated synthetic data.

| Check | Standalone evidence |
|---|---|
| S01 | Build and install the wheel into a fresh virtual environment from hash-verified dependencies. Run the CLI, run the script entry point from outside the source checkout, and run `pip check`. |
| D01 | `test_regions_snapshot_and_consolidated_workbook` discovers two regions on one sheet at row 31, preserves 26 columns per region and tests chart-ready coordinate/value arrays and distinct formula cache states. |
| D02 | `test_migrated_rule` and `test_rows_preserve_positions_and_require_declared_units` exercise duplicates and legitimate derived relations, report uncertainty and preserve source coordinates. The large-baseline counterexample tests tolerance. |
| D03 | `test_regions_snapshot_and_consolidated_workbook` checks that a single copy marks both regions, retains original and cached formula values, leaves unrelated cells and sheets intact, and avoids rerunning the checks. |
| D04 | `test_zero_empty_skips_and_source_retention`, `test_json_boundary_and_failure_receipts`, and the coverage-rule fixtures check zero findings, an empty table, skipped and invalid rules, explicit truncation, corrupt input, source retention and output collisions. |
| Migration | 38 table rules, 8 integrity methods and 3 bound comparison entries have distinct semantic tests. Four added row rules and the across-row block direction have additional tests. The collected suite has 70 tests, including 14 preserved baseline tests. |

These checks exercise the independent package. S02 and S03, the final Worker image, the real-model orchestration and product-scoped acceptance remain NOT_RUN. XLSM macro round-tripping and the inherited XLS/XLSB, PDF and DOCX reader paths are outside the synthetic acceptance suite. The host will need to bind its own authorization, file delivery and provenance to the installed commit.
