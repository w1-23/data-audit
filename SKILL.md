---
name: data-audit
description: Review one explicitly scoped table or workbook region with deterministic checks, absolute source coordinates, an explicit coverage report, and one annotated XLSX. Use for missing values, declared type/range violations, duplicates, copy/transform relationships, group outliers, and scoped numerical checks.
license: MIT
compatibility: Python 3.11 or newer with the installed citrus-data-audit package and read access to the selected input files.
---

# Data audit

Work from the authorized source bytes or a saved review with the same source hash and scope.

1. **Locate the actual table.** Identify the real sheet name, every requested block, absolute A1 bounds, header rows, measurement columns, ID/sample keys, grouping columns, summary columns, and any token legend. A preview is not the scan scope. Use explicit regions for a workbook with upper/lower or side-by-side blocks.
2. **Run one bounded review.** Call `review_sheet(...)` with the confirmed regions and field roles. The default `balanced` profile batches applicable basic checks and PaperConan-adapted exact copy/transform checks once. Advanced Geng checks require their explicit preconditions. Do not first write an ad-hoc implementation and then run the package again.
3. **Use reported-summary or P-value bindings only when explicitly requested.** Pass `reported_summaries` or `reported_p_values` with real raw rows, reported cells, design, precision and units. A duplicates-only request must not add summary, P-value, outlier or literature work.
4. **Return one artifact.** A normal table review publishes one `<original>_审查标注.xlsx`. Preserve original values, formulas, caches, merges and unreviewed areas. Add one visible `审查结果` sheet with `位置 | 类型 | 结论`, and mark cells/ranges with comments or red borders. A zero-finding review uses the same workbook and has no invented red marks.

Stop when the requested scope has been processed and reported. If a scientific prerequisite is missing, finish the applicable checks and record that item as not applicable/not run. Do not request another full scan merely to eliminate uncertainty. Printed or debug JSON and the private snapshot are not the default user deliverable. A relation or distribution deviation is a review lead, not proof of misconduct.
