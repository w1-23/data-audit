# Changelog

## 3.1.0

- Add `review_sheet()` as the balanced, explicit-scope default review path.
- Add bounded PaperConan-adapted exact copy/transform detectors with absolute two-sided coordinates and source/SHA/license metadata.
- Add optional Geng-adapted last-digit, Benford, decimal-tail and GRIM checks with scientific preconditions and no risk score.
- Make annotated XLSX the default export: one `审查结果` sheet with `位置 / 类型 / 结论`, source values/formulas/caches preserved, and no debug appendices unless explicitly requested.
- Keep `review_bytes`, `audit_table`, schema `2.0.0`, old rule identifiers and `presentation="legacy"` compatible.
- Keep balanced reports `complete` when optional checks are legitimately `not_applicable`; explicit required checks with missing prerequisites still report `partial` plus coverage gaps.
- Keep explicit profiles exact: `duplicates` and `requested_checks` never add IQR or unrelated methods.
- Add a six-region golden fixture covering declared special tokens, formula caches, unique output, PaperConan relations, source hash retention and annotated navigation.

## 3.0.0

- Package the existing table audit, integrity checks, bound comparisons, region readers, and workbook export under the `citrus_audit` import path.
- Add same-unit row relationship checks, horizontal repeated blocks, and absolute cell evidence for both sides of column relationships.
- Write stable strict JSON reports and private frozen snapshots with bounded stdout receipts. Retain formula expressions and cache status separately from calculation values.
- Add a canonical report digest, multi-region comparison bindings, atomic create-only output, and a tested hash-locked Linux environment.
