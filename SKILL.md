---
name: data-audit
description: Review supplied tables for data quality and numerical relationships, with original cell coordinates, explicit coverage, and an optional annotated workbook copy. Use for missing values, declared type or range violations, duplicate records, ordered patterns, group outliers, and explicitly scoped numeric comparisons.
license: MIT
compatibility: Python 3.11 or newer with the installed citrus-data-audit package and read access to the selected input files.
---

# Data audit

Use authorized code execution when the user asks to review the quality, structure, or numerical relationships of supplied tabular data. Work from the source bytes or reuse a saved review with the same source and scope. Read [the command and configuration examples](references/usage.md) when selecting an entry point.

1. Identify the sheet, actual header rows, and data regions. In a workbook with multiple blocks, inspect the layout and confirm the absolute rectangles and headers to review. A bounded preview provides candidate regions. Record source SHA-256 and retain absolute rows and columns.
2. Batch the inexpensive applicable checks. Use declared fields and bounds for type and range violations. Confirm the measurement order and comparability before checking row-wise patterns. Run design, group outlier, integer summary, or cross-source checks only when their prerequisites are available. Record which checks and regions were skipped and why.
3. Report grouped findings with representative original coordinates and the checklist. Preserve the full findings, coverage, and parsed values in the report. Distinguish a declared constraint violation, a numerical relationship, and a question requiring source records or experimental context. Repeated measurements, scheduled gradients, shared controls, and formula-derived values can legitimately trigger rules. The calculations establish no inference about intent or misconduct.
4. For a requested workbook, use the saved review to export one annotated copy of the original. Preserve source values, formulas, and cached values. Treat source bytes and the full snapshot as private input data. Share only the requested report or copy through the authorized output path.

Stop once the requested scope has a receipt and available findings. If a missing scientific assumption materially changes the requested conclusion, ask for it after completing independent checks. If a parse limit or missing cache leaves part of the data unreviewed, report the gap and resume only with a narrower selection or additional evidence. A zero-finding result applies to the checks and positions actually evaluated.
