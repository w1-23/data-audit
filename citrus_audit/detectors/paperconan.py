"""Bounded copy/transform detectors adapted from PaperConan.

Upstream reference:
    https://github.com/zixixr/paperconan
    commit 8d116455b23e858651250d8ed23ba41c1a87ab0d
    files src/paperconan/detectors.py, collisions.py and _audit.py

The upstream project is MIT licensed. This adapter intentionally implements
only the detector semantics needed for an explicitly selected table/region:
exact value comparison, constant offset/ratio, row/column direction, decimal
tail reuse and repeated numeric blocks. It does not run the upstream scanner,
directory walker, network downloads, HTML report or risk scoring.
"""
from __future__ import annotations

from collections import defaultdict
import re
import json
from itertools import combinations, islice
from typing import Any

from openpyxl.utils import get_column_letter

from ..integrity import _number, _rational

METHOD_VERSION = "paperconan-adapt/2"
SOURCE = {
    "repository": "https://github.com/zixixr/paperconan",
    "commit": "8d116455b23e858651250d8ed23ba41c1a87ab0d",
    "license": "MIT",
    "adaptation": "bounded exact table and explicitly aligned snapshot comparisons",
}
CHECKS = (
    "pc_identical_columns",
    "pc_constant_offset_columns",
    "pc_constant_ratio_columns",
    "pc_identical_rows",
    "pc_constant_offset_rows",
    "pc_constant_ratio_rows",
    "pc_repeated_blocks",
    "pc_shared_decimal_tail",
)
CORE_CHECKS = frozenset(CHECKS[:7])
_COLUMN_CHECKS = frozenset(CHECKS[:3])
_ROW_CHECKS = frozenset(CHECKS[3:6])
_MAX_PAIR_ROWS = 20_000
_MAX_COLUMNS = 80
_MAX_ROW_PAIRS = 5_000
_MAX_WINDOW_STARTS = 10_000
_BLOCK_HEIGHT = 3
_BLOCK_WIDTH = 2


def _a1_range(table, rows: list[int], columns: list[int]) -> str:
    if not rows or not columns:
        return ""
    left, right = min(columns), max(columns)
    top, bottom = min(rows), max(rows)
    return f"{get_column_letter(left)}{top}:{get_column_letter(right)}{bottom}"


def _numeric_matrix(table, field_roles=None):
    rows = list(table.row_numbers)[:_MAX_PAIR_ROWS]
    columns = list(table.column_numbers)[:_MAX_COLUMNS]
    names = list(table.columns)[:_MAX_COLUMNS]
    values = {row: {} for row in rows}
    coverage = {}
    roles = field_roles or {}
    declared = list(roles.get('value_columns') or [])
    excluded = set(roles.get('sample_keys') or roles.get('key_columns') or [])
    excluded |= set(roles.get('group_columns') or [])
    excluded_ids = re.compile(r'^(?:id|index|no|number|sample(?:_?id)?|record(?:_?id)?|key|replicate|rep|n|row)$', re.I)
    for index, (name, number) in enumerate(zip(names, columns)):
        if declared and name not in declared:
            continue
        if not declared and (name in excluded or excluded_ids.fullmatch(str(name).strip())):
            continue
        nonempty = 0
        numeric = 0
        for row, source_row in zip(rows, table.values[:len(rows)]):
            value = source_row[index]
            if value is not None and str(value).strip() != "":
                nonempty += 1
            number_value, _ = _number(value)
            if number_value is not None:
                numeric += 1
                values[row][number] = (number_value, value)
        coverage[number] = (name, numeric, nonempty)
    eligible = [number for number, (_, numeric, nonempty) in coverage.items()
                if numeric >= 3 and numeric / max(1, nonempty) >= 0.8]
    return rows, columns, names, values, coverage, eligible


def _pair_evidence(table, left: int, right: int, direction: str, rows: list[int],
                   pairs: list[tuple[int, int, Any, Any]]) -> dict:
    if direction == "columns":
        ranges = []
        for column in (left, right):
            ranges.append(_a1_range(table, rows, [column]))
    else:
        ranges = [_a1_range(table, [row], list(table.column_numbers)) for row in (left, right)]
    return {
        "direction": direction,
        "ranges": [item for item in ranges if item],
        "left": left if direction == "rows" else table.columns[table.column_numbers.index(left)],
        "right": right if direction == "rows" else table.columns[table.column_numbers.index(right)],
        "paired": len(pairs),
        "representative_pairs": [{"left_row": left if direction == "rows" else row,
                   "right_row": right if direction == "rows" else row,
                   "left": str(a), "right": str(b)}
                  for row, _, a, b in pairs[:12]],
    }


def _finding(table, check, detail, *, row="", column="", severity="review", evidence=None):
    return {
        "severity": severity,
        "check": check,
        "field": table.columns[table.column_numbers.index(column)] if column in table.column_numbers else "",
        "column_index": column if column else "",
        "row": row,
        "detail": detail,
        "scope": "range",
        "layer": "paperconan_exact_relation",
        "method_version": METHOD_VERSION,
        "upstream": SOURCE,
        "evidence": evidence or {},
    }


def exact_relation(pairs):
    """Classify aligned finite pairs on a common scale using exact rationals."""
    if len(pairs) < 3:
        return None, None
    if all(left == right for left, right in pairs):
        return "identical", None
    if len({left for left, _ in pairs}) < 2 or len({right for _, right in pairs}) < 2:
        return None, None
    offsets = {right - left for left, right in pairs}
    if len(offsets) == 1:
        return "constant_offset", offsets.pop()
    if all(left for left, _ in pairs):
        ratios = {right / left for left, right in pairs}
        if len(ratios) == 1:
            return "constant_ratio", ratios.pop()
    return None, None


def _relation_finding(table, direction, pairs, coordinates, *, left, right, rows, selected):
    relation, constant = exact_relation(pairs)
    check = f"pc_{relation}_{direction}"
    if check not in selected:
        return None
    evidence = _pair_evidence(table, left, right, direction, rows, coordinates)
    evidence.update(relation=relation, pairs=len(pairs))
    if direction == "columns":
        left_label, right_label = evidence['left'], evidence['right']
        row, column = "", right
    else:
        left_label, right_label = f"第 {left} 行", f"第 {right} 行"
        row, column = right, ""
    if constant is not None:
        evidence['constant'] = _rational(constant)
    description = {
        "identical": "数值完全相同",
        "constant_offset": f"逐项相差常数 {evidence.get('constant')}",
        "constant_ratio": f"保持同一比值 {evidence.get('constant')}",
    }[relation]
    return _finding(table, check,
        f"{left_label} 与 {right_label} 在 {len(pairs)} 个共同测量位置上{description}。"
        "请核对公式、单位换算或合法复用，不能据此判定复制方向。",
        row=row, column=column, evidence=evidence)


def _same_column_relation(table, values, left, right, rows, selected):
    common = [row for row in rows if left in values[row] and right in values[row]]
    nonempty = sum(1 for row in rows if left in values[row] or right in values[row])
    receipt = {"left": left, "right": right, "tested": len(common)}
    if len(common) < 3 or len(common) / max(1, nonempty) < 0.8:
        receipt["reason"] = "paired_coverage_below_0.8_or_n_lt_3"
        return None, receipt
    cells = [(row, values[row][left], values[row][right]) for row in common]
    finding = _relation_finding(table, "columns", [(a[0], b[0]) for _, a, b in cells],
        [(row, row, a[1], b[1]) for row, a, b in cells], left=left, right=right,
        rows=common, selected=selected)
    return finding, receipt


def _same_row_relation(table, values, upper, lower, columns, selected):
    common = [column for column in columns if column in values[upper] and column in values[lower]]
    nonempty = sum(1 for column in columns if column in values[upper] or column in values[lower])
    receipt = {"upper": upper, "lower": lower, "tested": len(common)}
    if len(common) < 3 or len(common) / max(1, nonempty) < 0.8:
        receipt["reason"] = "paired_coverage_below_0.8_or_n_lt_3"
        return None, receipt
    cells = [(values[upper][column], values[lower][column]) for column in common]
    finding = _relation_finding(table, "rows", [(a[0], b[0]) for a, b in cells],
        [(upper, lower, a[1], b[1]) for a, b in cells], left=upper, right=lower,
        rows=[upper, lower], selected=selected)
    return finding, receipt


def _repeated_blocks(table, values, rows, columns):
    starts = max(0, len(rows) - _BLOCK_HEIGHT + 1)
    if starts * max(0, len(columns) - _BLOCK_WIDTH + 1) > _MAX_WINDOW_STARTS:
        return [], {"state": "partial", "reason": "window_search_budget", "tested": _MAX_WINDOW_STARTS}
    seen = defaultdict(list)
    tested = 0
    for top_index in range(starts):
        for left_index in range(max(0, len(columns) - _BLOCK_WIDTH + 1)):
            block_rows = rows[top_index:top_index + _BLOCK_HEIGHT]
            block_columns = columns[left_index:left_index + _BLOCK_WIDTH]
            if not all(row in values and all(column in values[row] for column in block_columns)
                       for row in block_rows):
                continue
            signature = tuple(values[row][column][0] for row in block_rows for column in block_columns)
            if len(set(signature)) < 3:
                continue
            seen[signature].append((top_index, left_index, block_rows, block_columns))
            tested += 1
    findings = []
    for occurrences in seen.values():
        if len(occurrences) < 2:
            continue
        a, b = occurrences[0], occurrences[1]
        if a[2] == b[2] and a[3] == b[3]:
            continue
        evidence = {
            "shape": [_BLOCK_HEIGHT, _BLOCK_WIDTH],
            "ranges": [_a1_range(table, a[2], a[3]), _a1_range(table, b[2], b[3])],
            "pairs": _BLOCK_HEIGHT * _BLOCK_WIDTH,
            "first_occurrence": len(occurrences),
            "direction": "block",
        }
        findings.append(_finding(table, "pc_repeated_blocks",
                                 f"发现 {_BLOCK_HEIGHT}×{_BLOCK_WIDTH} 数值块完全重复；需核对共享对照、导出重复或复制关系，不能据此判定方向。",
                                 row=b[2][0], column=b[3][0], evidence=evidence))
        if len(findings) >= 20:
            break
    state = "complete" if tested < _MAX_WINDOW_STARTS else "partial"
    return findings, {"state": state, "tested": tested, "windows": len(seen),
                      "reason": "" if state == "complete" else "window_search_budget"}


def _shared_tail(table, rows, columns):
    evidence_tokens = {(item["row"], item["column"]): item["raw"] for item in table.precision_evidence}
    grouped = defaultdict(dict)
    for row in rows:
        for column in columns:
            token = evidence_tokens.get((row, column))
            if not token:
                continue
            mantissa = token.strip().lower().split("e", 1)[0].lstrip("+-")
            digits = "".join(char for char in mantissa if char.isdigit())
            if len(digits) < 2:
                continue
            grouped[column][row] = digits[-2:]
    findings = []
    keys = list(grouped)
    for left_index, left in enumerate(keys):
        for right in keys[left_index + 1:]:
            common = sorted(set(grouped[left]) & set(grouped[right]))
            same = [row for row in common if grouped[left][row] == grouped[right][row]]
            if len(common) < 5 or len(same) / len(common) < 0.8:
                continue
            left_name = table.columns[table.column_numbers.index(left)]
            right_name = table.columns[table.column_numbers.index(right)]
            ranges = [_a1_range(table, same, [left]), _a1_range(table, same, [right])]
            evidence = {"ranges": ranges, "pairs": len(same), "tested": len(common),
                        "tails": sorted({grouped[left][row] for row in same})[:10],
                        "direction": "columns", "precision_source": "lexical_tokens_only"}
            findings.append(_finding(table, "pc_shared_decimal_tail",
                                     f"{left_name} 与 {right_name} 在 {len(same)}/{len(common)} 个可比较原始数值中共享末两位；需先确认仪器精度与舍入规则。",
                                     column=right, evidence=evidence))
    return findings, {"state": "complete", "tested": sum(len(v) for v in grouped.values())}


def detect_table(table, *, checks=None, field_roles=None):
    checks = set(checks if checks is not None else CHECKS)
    selected = checks & set(CHECKS)
    if not selected:
        return [], {}
    rows, columns, names, values, coverage, eligible = _numeric_matrix(table, field_roles)
    findings = []
    receipts = {}
    if "pc_shared_decimal_tail" in selected:
        found, receipt = _shared_tail(table, rows, columns)
        findings.extend(found)
        receipts["pc_shared_decimal_tail"] = receipt
    if selected & _COLUMN_CHECKS:
        receipts["_column_pairs"] = []
        for left, right in combinations(eligible, 2):
            finding, receipt = _same_column_relation(table, values, left, right, rows, selected)
            receipts["_column_pairs"].append(receipt)
            if finding:
                findings.append(finding)
    row_pair_count = 0
    row_pairs_total = 0
    if selected & _ROW_CHECKS:
        receipts["_row_pairs"] = []
        eligible_rows = [row for row in rows if sum(column in values[row] for column in eligible) >= 3]
        row_pairs_total = len(eligible_rows) * (len(eligible_rows) - 1) // 2
        for upper, lower in islice(combinations(eligible_rows, 2), _MAX_ROW_PAIRS):
            finding, receipt = _same_row_relation(table, values, upper, lower, eligible, selected)
            receipts["_row_pairs"].append(receipt)
            if finding:
                findings.append(finding)
        row_pair_count = len(receipts["_row_pairs"])
    if "pc_repeated_blocks" in selected:
        found, receipt = _repeated_blocks(table, values, rows, eligible)
        findings.extend(found)
        receipts["pc_repeated_blocks"] = receipt
    grouped = {}
    for item in findings:
        key = (item["check"], json.dumps(item.get("evidence", {}), ensure_ascii=False,
                                         sort_keys=True, default=str))
        grouped[key] = item
    findings[:] = list(grouped.values())
    receipts["eligible_columns"] = [names[list(table.column_numbers).index(c)] for c in eligible]
    receipts["coverage"] = {"rows": len(rows), "columns": len(columns),
                            "eligible_columns": receipts["eligible_columns"],
                            "row_pairs": row_pair_count, "row_pairs_total": row_pairs_total,
                            "method_version": METHOD_VERSION, "source": SOURCE}
    return findings, receipts
