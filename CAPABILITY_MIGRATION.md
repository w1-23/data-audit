# Data-audit capability migration

The standalone Skill is extracted from `w1-23/CitrusScholar@d275430d3ef01bf9558ccd85204d1bdede631989`, under `packages/citrus_audit`. The `citrus-data-audit` distribution retains the `citrus_audit` import and reads snapshot schema `2.0.0`. [Source and license](NOTICE.md).

| Baseline capability | Migrated | Semantically verified | Standalone entry |
|---|---:|---:|---|
| Table rules and coverage diagnostics | 38 | 38 | `audit_table`, `review_bytes`, `data-audit review` |
| Cross-file numeric integrity methods | 8 | 8 | `audit_numeric_reuse`, `compare_snapshots`, `data-audit compare` |
| Bound comparison entries | 3 | 3 | `parse_plan`, `compare_bound`, `compare_snapshots`, `data-audit compare` |

The [entry-by-entry migration map](references/migration.md) names every baseline capability and its test. It also covers the four added row-direction rules and horizontal repeated-block detection. These additions preserve original cell coordinates and are outside the baseline counts.

The Skill operates on explicitly supplied files and saved snapshots. It provides a bounded first reply, a stable full JSON report, and one consolidated annotated workbook from saved findings. Coverage and failure receipts keep unreviewed scopes visible. Numerical relationships are review leads and require scientific context. [Skill entry](SKILL.md) · [Usage and scope](references/usage.md).

The [standalone CI workflow](.github/workflows/ci.yml) verifies the hash-locked Linux CPython 3.12 dependency set, builds and installs the wheel, and runs the full test suite against that installation. The standalone acceptance suite has 70 tests. Host installation and removal, second-host workflow execution, and final Worker acceptance remain `NOT_RUN`. [Standalone evidence and gaps](references/verification.md) · [Host integration](references/host-integration.md).
