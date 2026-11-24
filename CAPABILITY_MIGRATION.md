# Data-audit capability migration

The standalone Skill is extracted from `w1-23/CitrusScholar@d275430d3ef01bf9558ccd85204d1bdede631989`, under `packages/citrus_audit`. The `citrus-data-audit` distribution retains the `citrus_audit` import and reads snapshot schema `2.0.0`. [Source and license](NOTICE.md).

| Baseline capability | Migrated | Semantically verified | Standalone entry |
|---|---:|---:|---|
| Table rules and coverage diagnostics | 38 | 38 | `audit_table`, `review_bytes`, `data-audit review` |
| Cross-file numeric integrity methods | 8 | 8 | `audit_numeric_reuse`, `compare_snapshots`, `data-audit compare` |
| Bound comparison entries | 3 | 3 | `parse_plan`, `compare_bound`, `compare_snapshots`, `data-audit compare` |

The [entry-by-entry migration map](references/migration.md) names every baseline capability and its test. It also covers the four added row-direction rules and horizontal repeated-block detection. These additions preserve original cell coordinates and are outside the baseline counts.

The Skill operates on explicitly supplied files and saved snapshots. It provides a bounded first reply, a stable full JSON report, and one consolidated annotated workbook from saved findings. Coverage and failure receipts keep unreviewed scopes visible. Numerical relationships are review leads and require scientific context. [Skill entry](SKILL.md) · [Usage and scope](references/usage.md).

The [standalone CI workflow](.github/workflows/ci.yml) verifies the hash-locked Linux CPython 3.12 dependency set, builds and installs the wheel, and runs the full test suite against that installation. The standalone acceptance suite has 100 tests. Host installation and removal, second-host workflow execution, and final Worker acceptance remain `NOT_RUN`. [Standalone evidence and gaps](references/verification.md) · [Host integration](references/host-integration.md).


## 3.1 default execution organization

The 38/8/3 inventories above remain compatibility and comparison capabilities;
they are not the new default orchestrator. New table reviews start at
`review_sheet()` with explicit regional scope and roles.

| Capability family | Current default | Compatibility surface | Verification |
|---|---|---|---|
| Basic structure, required values, declared types/ranges, duplicate records | Existing deterministic operators, selected once by `balanced` | Same operators and rule IDs through `audit_table`/`review_bytes` | Golden fixture plus inherited rule tests |
| Row/column duplication, constant offset/ratio, repeated blocks | PaperConan-adapted bounded exact detectors | Old relation rules remain explicitly callable | Golden fixture and profile isolation tests |
| Decimal-tail concentration | PaperConan-adapted lexical-token detector, on demand | `decimal_ending_concentration` and integrity method remain compatible | Source-token precondition tests |
| Last digit, Benford, GRIM, decimal consistency | Geng-adapted optional checks with explicit prerequisites and no risk score | Not part of `balanced` | Scoped specialist tests |
| SPRITE / image duplication | `NOT_RUN`: no pinned runtime and no separate authorized image scope in this package | No silent substitute | Explicit coverage gap |
| Reported n/sum/mean/SD/SE | Explicit raw→reported binding; exact count and rounding-interval comparison | `compare_reported_summaries` and legacy integer-summary rules remain compatible | `reported_stats_test.py` and cross-table binding test |
| Reported P values | Pure-Python z/t/χ²/F and raw Welch/Student/paired/ANOVA/Pearson/contingency; inequalities and Holm/BH | `audit_p_values` explicit family adjustment | Distribution-reference and binding tests |
| Annotated original copy | One `审查结果` sheet; one XLSX; no debug appendices | `presentation="legacy"` | Six-region SHA/formula/cache/XML fixture |

A new task must not run the old and new orchestrators in sequence. A relationship
is emitted by one authority only: PaperConan-adapted checks are selected in the
new default; the old column/row relation rule IDs are compatibility surfaces.
