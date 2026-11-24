# Data audit

`data-audit` is a standalone Agent Skill and Python package for reproducible
review of explicitly scoped tabular data. It retains the `citrus_audit` import
path and snapshot schema `2.0.0`. Distribution version: `3.1.0`.

## Install and verify

On Linux x86-64 with CPython 3.12 and glibc, use a fresh environment with pip:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r requirements-linux-cp312.lock
python -m pip install --no-deps --no-build-isolation .
python -m pip check
python -m pytest -q
```

## Default review and output

```sh
data-audit review-sheet input.xlsx --options review-options.json --snapshot reviewed.json
data-audit export reviewed.json --output input_审查标注.xlsx
```

The default is one bounded review and one annotated copy of the original. The
output contains one visible `审查结果` sheet with `位置 | 类型 | 结论`; original
values, formulas, caches, merges and unreviewed sheets remain in place. Findings
are review leads, not misconduct determinations.

PaperConan is the preferred bounded implementation for exact column/row
copy/transform relationships and repeated blocks. Geng specialists are optional
and preconditioned. Source commits and licenses are recorded in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

See [SKILL.md](SKILL.md), [the host contract](references/host-contract.md),
[configuration and Python usage](references/usage.md),
[the migration map](references/migration.md), and
[validation evidence](references/verification.md).
