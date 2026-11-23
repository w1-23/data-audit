# Data audit

`data-audit` is a standalone Agent Skill and Python package for reproducible review of tabular data. It retains the `citrus_audit` import path and the snapshot schema `2.0.0`. The distribution version is `3.0.0`.

## Install and verify

On Linux x86-64 with CPython 3.12 and glibc, the checked lock includes the runtime, build and test dependencies. Use a fresh virtual environment with pip available.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r requirements-linux-cp312.lock
python -m pip install --no-deps --no-build-isolation .
python -m pip check
python -m pytest -q
```

On other supported Python environments, install with `python -m pip install '.[test]'` to resolve from declared ranges. Those environments need their own lock and validation.

## Typical review

```sh
data-audit review input.csv --snapshot reviewed.json --checks empty_table missing_cell duplicate_row
data-audit report reviewed.json --output review-report.json
data-audit export reviewed.json --output annotated.xlsx --issue-list
```

`python scripts/audit.py` and `python -m citrus_audit.cli` provide the same entry points. See [SKILL.md](SKILL.md), [configuration and Python usage](references/usage.md), [the migration map](references/migration.md), [validation evidence](references/verification.md), and [optional host integration](references/host-integration.md).

The compact review receipt contains counts and digests. The full report, input snapshot, and requested workbook may contain sensitive source data. Keep them inside the workspace authorized for the original, and use the output controls of the calling environment when delivering results.
