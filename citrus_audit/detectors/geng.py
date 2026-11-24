"""Optional specialist checks adapted from the pinned Geng skill scripts.

Upstream: https://github.com/JasonYan-Bio/geng-skills
Commit: 3c637844281a054a8dbe08dd96fea8413f3883c0
License: MIT

This module deliberately removes upstream risk scores, misconduct language and
automatic whole-column scanning. Every check requires an explicit scientific
precondition and returns review leads only.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
import math
from typing import Any

from openpyxl.utils import get_column_letter

from ..integrity import _number

METHOD_VERSION = "geng-adapt/1"
SOURCE = {
    "repository": "https://github.com/JasonYan-Bio/geng-skills",
    "commit": "3c637844281a054a8dbe08dd96fea8413f3883c0",
    "license": "MIT",
    "adaptation": "selected checks only; risk scores and misconduct wording removed",
}
CHECKS = (
    "geng_last_digit",
    "geng_benford",
    "geng_grim",
    "geng_decimal_consistency",
)
DEFAULT_LAST_DIGIT_N = 30
DEFAULT_BENFORD_N = 100
DEFAULT_DECIMAL_N = 50


def _gammaincc(a: float, x: float) -> float:
    """Regularized upper incomplete gamma Q(a,x), sufficient for chi-square."""
    if x < 0 or a <= 0:
        return float("nan")
    if x == 0:
        return 1.0
    gln = math.lgamma(a)
    if x < a + 1:
        term = 1.0 / a
        total = term
        ap = a
        for _ in range(300):
            ap += 1
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 1e-14:
                break
        return max(0.0, 1.0 - total * math.exp(-x + a * math.log(x) - gln))
    b = x + 1 - a
    c = 1.0 / 1e-300
    d = 1.0 / b
    h = d
    for index in range(1, 301):
        an = -index * (index - a)
        b += 2
        d = an * d + b
        if abs(d) < 1e-300:
            d = 1e-300
        c = b + an / c
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return max(0.0, min(1.0, math.exp(-x + a * math.log(x) - gln) * h))


def _chi_square_p(observed, expected):
    statistic = sum((o - e) ** 2 / e for o, e in zip(observed, expected) if e > 0)
    return statistic, _gammaincc((len(observed) - 1) / 2, statistic / 2)


def _range(table, rows, column):
    if not rows:
        return ""
    return f"{get_column_letter(column)}{min(rows)}:{get_column_letter(column)}{max(rows)}"


def _finding(table, check, detail, *, rows, column, evidence):
    name = table.columns[table.column_numbers.index(column)] if column in table.column_numbers else ""
    return {
        "severity": "review",
        "check": check,
        "field": name,
        "column_index": column,
        "row": "",
        "detail": detail,
        "scope": "range",
        "layer": "geng_specialist",
        "method_version": METHOD_VERSION,
        "upstream": SOURCE,
        "evidence": {"ranges": [_range(table, rows, column)], **evidence},
    }


def _tokens_by_column(table, columns):
    mapping = defaultdict(dict)
    for item in table.precision_evidence:
        if item["column"] in columns:
            mapping[item["column"]][item["row"]] = item["raw"]
    return mapping


def _last_digit(token):
    mantissa = str(token).strip().lower().split("e", 1)[0].lstrip("+-")
    digits = "".join(char for char in mantissa if char.isdigit())
    return int(digits[-1]) if digits else None


def _first_digit(token):
    mantissa = str(token).strip().lower().split("e", 1)[0].lstrip("+-")
    digits = "".join(char for char in mantissa if char.isdigit()).lstrip("0")
    return int(digits[0]) if digits else None


def _column_name(table, number):
    try:
        return table.columns[table.column_numbers.index(number)]
    except ValueError:
        return str(number)


def _last_digit_check(table, columns):
    findings, receipts = [], {}
    tokens = _tokens_by_column(table, set(columns))
    for column in columns:
        entries = sorted(tokens.get(column, {}).items())
        digits = [_last_digit(token) for _, token in entries]
        digits = [digit for digit in digits if digit is not None]
        if len(digits) < DEFAULT_LAST_DIGIT_N:
            receipts[str(_column_name(table, column))] = {
                "state": "not_applicable", "tested": len(digits),
                "reason": f"requires_at_least_{DEFAULT_LAST_DIGIT_N}_lexical_tokens"}
            continue
        counts = Counter(digits)
        observed = [counts.get(digit, 0) for digit in range(10)]
        expected = [len(digits) / 10] * 10
        statistic, p_value = _chi_square_p(observed, expected)
        receipt = {"state": "executed", "tested": len(digits), "chi_square": statistic,
                   "p_value": p_value, "degrees_of_freedom": 9,
                   "distribution": observed, "precision_source": "lexical_tokens_only"}
        receipts[str(_column_name(table, column))] = receipt
        if p_value < 0.01:
            max_digit, max_count = max(enumerate(observed), key=lambda item: item[1])
            same_rows = [row for row, token in entries if _last_digit(token) == max_digit]
            findings.append(_finding(
                table, "geng_last_digit",
                f"末位数字分布偏离均匀（χ²={statistic:.3f}, df=9, p={p_value:.6g}）；数字 {max_digit} 出现 {max_count}/{len(digits)} 次。仪器舍入、量程和有限精度可产生相同模式，需先核对原始精度。",
                rows=same_rows, column=column,
                evidence={"distribution": observed, "chi_square": statistic,
                          "p_value": p_value, "decimal_precision_confirmed": True}))
    return findings, receipts


def _benford_check(table, columns):
    findings, receipts = [], {}
    tokens = _tokens_by_column(table, set(columns))
    for column in columns:
        entries = sorted(tokens.get(column, {}).items())
        digits = [_first_digit(token) for _, token in entries]
        digits = [digit for digit in digits if digit is not None]
        if len(digits) < DEFAULT_BENFORD_N:
            receipts[str(_column_name(table, column))] = {
                "state": "not_applicable", "tested": len(digits),
                "reason": f"requires_at_least_{DEFAULT_BENFORD_N}_lexical_tokens_and_mechanism_confirmed"}
            continue
        counts = Counter(digits)
        observed = [counts.get(digit, 0) for digit in range(1, 10)]
        expected = [len(digits) * math.log10(1 + 1 / digit) for digit in range(1, 10)]
        statistic, p_value = _chi_square_p(observed, expected)
        frequencies = [value / len(digits) for value in observed]
        theoretical = [value / len(digits) for value in expected]
        mad = sum(abs(a - b) for a, b in zip(frequencies, theoretical)) / 9
        receipt = {"state": "executed", "tested": len(digits), "chi_square": statistic,
                   "p_value": p_value, "degrees_of_freedom": 8, "mad": mad,
                   "mechanism_confirmed": True}
        receipts[str(_column_name(table, column))] = receipt
        if p_value < 0.01 and mad >= 0.015:
            rows = [row for row, token in entries if _first_digit(token) is not None]
            findings.append(_finding(
                table, "geng_benford",
                f"首位数字分布与本福特定律不符（χ²={statistic:.3f}, df=8, p={p_value:.6g}, MAD={mad:.4f}）；仅适用于明确确认的自然跨数量级数据，不能直接指认具体单元格。",
                rows=rows, column=column, evidence=receipt))
    return findings, receipts


def _decimal_consistency_check(table, columns):
    findings, receipts = [], {}
    tokens = _tokens_by_column(table, set(columns))
    for column in columns:
        entries = sorted(tokens.get(column, {}).items())
        tails = []
        for row, token in entries:
            mantissa = str(token).strip().lower().split("e", 1)[0]
            if "." not in mantissa:
                continue
            tail = mantissa.split(".", 1)[1]
            if tail:
                tails.append((row, tail, tail[-1]))
        if len(tails) < DEFAULT_DECIMAL_N:
            receipts[str(_column_name(table, column))] = {
                "state": "not_applicable", "tested": len(tails),
                "reason": f"requires_at_least_{DEFAULT_DECIMAL_N}_literal_decimal_tokens"}
            continue
        counts = Counter(item[2] for item in tails)
        observed = [counts.get(digit, 0) for digit in range(10)]
        expected = [len(tails) / 10] * 10
        statistic, p_value = _chi_square_p(observed, expected)
        receipt = {"state": "executed", "tested": len(tails),
                   "chi_square": statistic, "p_value": p_value,
                   "degrees_of_freedom": 9, "distribution": observed}
        receipts[str(_column_name(table, column))] = receipt
        if p_value < 0.001:
            dominant = max(range(10), key=lambda digit: observed[digit])
            rows = [row for row, _, last in tails if last == str(dominant)]
            findings.append(_finding(
                table, "geng_decimal_consistency",
                f"原始小数的末位分布高度集中（χ²={statistic:.3f}, df=9, p={p_value:.6g}）；格式化精度、仪器分辨率与真实舍入均可产生，不能据此推定伪造。",
                rows=rows, column=column, evidence=receipt))
    return findings, receipts


def _grim_single(raw_mean, raw_n, decimals, minimum, maximum):
    try:
        n = int(raw_n)
        places = int(decimals)
        mean = Decimal(str(raw_mean))
        minimum = Decimal(str(minimum))
        maximum = Decimal(str(maximum))
    except (ValueError, TypeError, InvalidOperation):
        return {"status": "invalid_input", "reason": "mean_n_decimals_and_scale_required"}
    if n < 1 or places < 0 or places > 12 or minimum > maximum:
        return {"status": "invalid_input", "reason": "invalid_n_decimals_or_scale"}
    step = Decimal(1).scaleb(-places)
    low = (mean - step / 2).scaleb(0)
    high = (mean + step / 2).scaleb(0)
    integer_low = int((low * n).to_integral_value(rounding=ROUND_CEILING))
    integer_high = int((high * n).to_integral_value(rounding=ROUND_FLOOR))
    possible_min = int((minimum * n).to_integral_value(rounding=ROUND_CEILING))
    possible_max = int((maximum * n).to_integral_value(rounding=ROUND_FLOOR))
    lower = max(integer_low, possible_min)
    upper = min(integer_high, possible_max)
    if lower > upper:
        return {"status": "inconsistent", "n": n, "decimals": places,
                "sum_interval": [integer_low, integer_high],
                "possible_scale_sum": [possible_min, possible_max]}
    return {"status": "consistent", "n": n, "decimals": places,
            "sum_interval": [integer_low, integer_high],
            "possible_sum": lower}


def _grim_check(table, specs, value_map):
    findings, receipts = [], {}
    for spec in specs or []:
        if not isinstance(spec, dict):
            receipts[str(spec)] = {"state": "not_applicable", "reason": "grim_spec_must_be_object"}
            continue
        row = spec.get("row")
        mean_column = spec.get("mean_column")
        n_column = spec.get("n_column")
        if not all(key in spec for key in ("row", "mean_column", "n_column", "decimals", "min_value", "max_value")):
            receipts[str(row)] = {"state": "not_applicable", "reason": "grim_requires_row_mean_n_decimals_and_scale"}
            continue
        values = value_map.get(int(row), {})
        mean = values.get(mean_column)
        n_value = values.get(n_column)
        result = _grim_single(mean, n_value, spec["decimals"], spec["min_value"], spec["max_value"])
        receipts[str(row)] = {"state": result["status"], **result}
        if result["status"] == "inconsistent":
            column_number = next((number for number, name in zip(table.column_numbers, table.columns)
                                  if name == mean_column), None)
            findings.append(_finding(
                table, "geng_grim",
                f"报告均值 {mean}、n={n_value} 与 {spec['decimals']} 位小数及声明量程不相容；可能来自舍入口径、加权均值或汇总错误，请核对原始记录。",
                rows=[int(row)], column=column_number,
                evidence={"grim": result, "scale": [spec["min_value"], spec["max_value"]]}))
    return findings, receipts


def detect_specialists(table, *, checks=None, field_roles=None):
    chosen = set(checks if checks is not None else ())
    roles = field_roles or {}
    findings, receipts = [], {}
    precision = list(roles.get("precision_confirmed") or [])
    benford = list(roles.get("benford_confirmed") or [])
    present = {str(name): number for name, number in zip(table.columns, table.column_numbers)}
    precision_numbers = [present[name] for name in precision if name in present]
    benford_numbers = [present[name] for name in benford if name in present]
    absent = lambda reason: {"_all": {"state": "not_applicable", "tested": 0, "reason": reason}}
    if "geng_last_digit" in chosen:
        if not precision_numbers:
            receipts["geng_last_digit"] = absent("precision_confirmed_columns_missing")
        else:
            found, receipt = _last_digit_check(table, precision_numbers)
            findings.extend(found); receipts["geng_last_digit"] = receipt
    if "geng_benford" in chosen:
        if not benford_numbers:
            receipts["geng_benford"] = absent("benford_confirmed_columns_missing")
        else:
            found, receipt = _benford_check(table, benford_numbers)
            findings.extend(found); receipts["geng_benford"] = receipt
    if "geng_decimal_consistency" in chosen:
        if not precision_numbers:
            receipts["geng_decimal_consistency"] = absent("precision_confirmed_columns_missing")
        else:
            found, receipt = _decimal_consistency_check(table, precision_numbers)
            findings.extend(found); receipts["geng_decimal_consistency"] = receipt
    if "geng_grim" in chosen:
        specs = roles.get("grim") or []
        if not specs:
            receipts["geng_grim"] = absent("grim_row_bindings_missing")
        else:
            value_map = {}
            for row, values in zip(table.row_numbers, table.values):
                value_map[row] = dict(zip(table.columns, values))
            found, receipt = _grim_check(table, specs, value_map)
            findings.extend(found); receipts["geng_grim"] = receipt
    receipts["_coverage"] = {
        "precision_confirmed_columns": [name for name in precision if name in present],
        "benford_confirmed_columns": [name for name in benford if name in present],
        "image_specialist": {"state": "not_run", "reason": "separate explicit image scope and dependency not supplied"},
        "sprite": {"state": "not_run", "reason": "no pinned rsprite2 runtime in the locked package environment"},
        "method_version": METHOD_VERSION,
        "source": SOURCE,
    }
    return findings, receipts
