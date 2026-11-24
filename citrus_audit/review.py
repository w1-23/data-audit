"""One-shot review orchestration for explicitly selected workbook regions."""
from __future__ import annotations

import hashlib
from contextlib import closing
from pathlib import Path
from typing import Any, Mapping, Sequence

from .api import AuditSnapshot, SCHEMA_VERSION, _formula_coordinates, audit_table
from .data_audit_rules import RULES
from .detectors import PAPERCONAN_CHECKS, GENG_CHECKS, detect_geng, detect_paperconan
from .readers import open_xlsx_readonly, read_regions, read_tables
from .reporting import dumps

REVIEW_VERSION = "review-sheet/1"
PROFILES = ("balanced", "duplicates", "relations", "numeric_forensics", "custom")
BASIC_BALANCED = {
    "empty_table", "empty_column", "missing_cell", "non_numeric_cell",
    "constant_field", "duplicate_row",
}
PAPER_CORE = {
    "pc_identical_columns", "pc_constant_offset_columns",
    "pc_constant_ratio_columns", "pc_identical_rows",
    "pc_constant_offset_rows", "pc_constant_ratio_rows",
    "pc_repeated_blocks",
}
ALIASES = {
    "duplicates": {"duplicate_key", "duplicate_row", "pc_identical_columns", "pc_identical_rows", "pc_repeated_blocks"},
    "relations": {"pc_constant_offset_columns", "pc_constant_ratio_columns",
                  "pc_constant_offset_rows", "pc_constant_ratio_rows"},
    "outliers": {"iqr_outlier", "iqr_insufficient"},
}
TOKEN_HINTS = {"nd#", "nd", "n.d.", "<lod", "blq", "na", "-", "#n/a"}


def _checklist_row(check, state, *, tested=0, skipped=0, reason="", method_version=""):
    return {"check": check, "state": state, "tested": int(tested),
            "skipped": int(skipped), "reason": reason,
            "method_version": method_version}


def _selected(requested, profile, roles):
    if requested is not None:
        if not isinstance(requested, (list, tuple, set, frozenset)):
            raise ValueError("requested_checks must be a collection")
        selected = set()
        for item in requested:
            if not isinstance(item, str):
                raise ValueError("requested_checks must contain strings")
            selected |= ALIASES.get(item, {item})
    else:
        if profile not in PROFILES:
            raise ValueError("Unknown review profile")
        selected = set(BASIC_BALANCED) | set(PAPER_CORE)
        if profile == "duplicates":
            selected = {"duplicate_row"} | ALIASES["duplicates"]
        elif profile == "relations":
            selected = set(PAPER_CORE)
        elif profile == "numeric_forensics":
            selected = set(BASIC_BALANCED) | set(PAPER_CORE) | set(GENG_CHECKS)
        elif profile == "custom":
            selected = set()
    # Role-derived additions apply only to an implicit profile. An explicit
    # check list is exact: asking for duplicates must not activate IQR or another
    # unrelated method merely because groups and measurements were declared.
    if requested is None and profile == "balanced":
        if roles.get("sample_keys") or roles.get("key_columns"):
            selected.add("duplicate_key")
        if roles.get("group_columns") and roles.get("value_columns"):
            selected.add("iqr_outlier")
            selected.add("iqr_insufficient")
    supported = set(RULES) | set(PAPERCONAN_CHECKS) | set(GENG_CHECKS) | set(ALIASES)
    unknown = selected - supported
    if unknown:
        raise ValueError("Unsupported review checks: " + ", ".join(sorted(unknown)))
    return selected


def _field_roles(roles):
    if roles is None:
        return {}
    if not isinstance(roles, Mapping):
        raise ValueError("field_roles must be an object")
    result = dict(roles)
    for key in ("value_columns", "group_columns", "sample_keys", "key_columns",
                "required_fields", "optional_fields", "precision_confirmed",
                "benford_confirmed"):
        value = result.get(key, [])
        if value is None:
            value = []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"field_roles.{key} must be a string list")
        result[key] = list(dict.fromkeys(value))
    for key in ("ranges", "units", "special_tokens"):
        value = result.get(key, {})
        if value is None:
            value = {}
        if not isinstance(value, Mapping):
            raise ValueError(f"field_roles.{key} must be an object")
        result[key] = dict(value)
    return result


def _legacy_options(roles):
    summary = roles.get("summary_roles") or {}
    if not isinstance(summary, Mapping):
        summary = {}
    if not summary and isinstance(roles.get("summary_columns"), Mapping):
        summary = roles["summary_columns"]
    mapping = {str(role): str(column) for role, column in summary.items()
               if role in {"mean", "sd", "n"} and column}
    return {
        "groups": roles.get("group_columns") or [],
        "values": roles.get("value_columns") or [],
        "required_fields": roles.get("required_fields") or [],
        "optional_fields": roles.get("optional_fields") or [],
        "ranges": roles.get("ranges") or {},
        "key_columns": roles.get("sample_keys") or roles.get("key_columns") or [],
        "mapping": mapping,
        "mean_decimals": roles.get("mean_decimals"),
        "sd_decimals": roles.get("sd_decimals"),
        "integer_data_confirmed": roles.get("integer_data_confirmed") is True,
        "sd_kind": roles.get("sd_kind") or "",
        "composite_keys": roles.get("composite_keys") or [],
        "pattern_checks": roles.get("pattern_checks"),
        "expected_structure": roles.get("expected_structure"),
        "row_relationships": roles.get("row_relationships"),
    }


def _value_at(table, row, column):
    try:
        row_index = table.row_numbers.index(row)
        column_index = table.column_numbers.index(column)
    except ValueError:
        return None, None
    return table.values[row_index][column_index], table.columns[column_index]

def _declared_token(value, special_tokens):
    if value is None:
        return None
    token = str(value).strip()
    for declared, meaning in special_tokens.items():
        if str(declared).strip().casefold() == token.casefold():
            return str(declared), str(meaning)
    return None


def _filter_special_tokens(findings, table, roles):
    selected = set(roles.get("value_columns") or [])
    special = roles.get("special_tokens") or {}
    retained = []
    resolved = 0
    undefined = 0
    for item in findings:
        check = item.get("check")
        row = item.get("row")
        column = item.get("column_index")
        if check not in {"non_numeric_cell", "mixed_numeric_text"} or type(row) is not int or type(column) is not int:
            retained.append(item)
            continue
        value, column_name = _value_at(table, row, column)
        if selected and column_name not in selected:
            retained.append(item)
            continue
        declared = _declared_token(value, special)
        text = str(value).strip() if value is not None else ""
        if declared:
            resolved += 1
            continue
        if text.casefold() in TOKEN_HINTS:
            undefined += 1
            retained.append({
                **item,
                "severity": "info",
                "check": "special_token_undefined",
                "mark_cell": False,
                "detail": f"原始值 {text!r} 是待解释的特殊标记；脚注或数据字典未声明其含义，请先确认，不能自动转成 0、缺失或 LOD/2。",
            })
            continue
        retained.append(item)
    return retained, resolved, undefined


def _paper_pair_rollup(receipts):
    eligible = 0
    skipped = 0
    for receipt in receipts or ():
        if not isinstance(receipt, dict):
            continue
        tested = int(receipt.get("tested", 0) or 0)
        reason = str(receipt.get("reason", ""))
        if tested >= 3 and not reason.startswith("paired_coverage_below"):
            eligible += 1
        else:
            skipped += 1
    return eligible, skipped


def _detector_checklist(selected, paper_receipt, geng_receipt, paper_findings, geng_findings):
    counts = {}
    for item in paper_findings + geng_findings:
        counts[item["check"]] = counts.get(item["check"], 0) + 1
    rows = []
    column_pairs = paper_receipt.get("_column_pairs") if isinstance(paper_receipt, dict) else None
    row_pairs = paper_receipt.get("_row_pairs") if isinstance(paper_receipt, dict) else None
    column_checks = {"pc_identical_columns", "pc_constant_offset_columns", "pc_constant_ratio_columns"}
    row_checks = {"pc_identical_rows", "pc_constant_offset_rows", "pc_constant_ratio_rows"}
    for check in sorted(selected & (set(PAPERCONAN_CHECKS) | set(GENG_CHECKS))):
        if check in column_checks and column_pairs is not None:
            tested, skipped = _paper_pair_rollup(column_pairs)
            state = "checked_with_findings" if counts.get(check) else (
                "checked_no_findings" if tested else "not_applicable")
            reason = ("no column pair met the minimum paired coverage" if not tested
                      else f"{skipped} column pair(s) below minimum paired coverage" if skipped else "")
            rows.append(_checklist_row(check, state, tested=tested, skipped=skipped,
                                       reason=reason, method_version="paperconan-adapt/1"))
        elif check in row_checks and row_pairs is not None:
            tested, skipped = _paper_pair_rollup(row_pairs)
            state = "checked_with_findings" if counts.get(check) else (
                "checked_no_findings" if tested else "not_applicable")
            reason = ("no row pair met the minimum paired coverage" if not tested
                      else f"{skipped} row pair(s) below minimum paired coverage" if skipped else "")
            rows.append(_checklist_row(check, state, tested=tested, skipped=skipped,
                                       reason=reason, method_version="paperconan-adapt/1"))
        elif check in paper_receipt and isinstance(paper_receipt[check], dict):
            receipt = paper_receipt[check]
            state = receipt.get("state", "executed")
            reason = receipt.get("reason", "")
            tested = receipt.get("tested", 0)
            if state == "partial":
                state = "partial_coverage"
            elif state == "not_applicable":
                state = "not_applicable"
            else:
                state = "checked_with_findings" if counts.get(check) else "checked_no_findings"
            rows.append(_checklist_row(check, state, tested=tested, reason=reason,
                                       method_version="paperconan-adapt/1"))
        elif check in geng_receipt and isinstance(geng_receipt[check], dict):
            receipt = geng_receipt[check]
            states = []
            if receipt and all(isinstance(value, dict) for value in receipt.values()):
                states = list(receipt.values())
            elif isinstance(receipt, dict):
                states = [receipt]
            tested = sum(int(value.get("tested", 0)) for value in states if isinstance(value, dict))
            not_applicable = [value.get("reason", "") for value in states
                              if isinstance(value, dict) and value.get("state") == "not_applicable"]
            if not_applicable and len(not_applicable) == len(states):
                state = "not_applicable"
            elif not_applicable:
                state = "checked_with_findings" if counts.get(check) else "partial_coverage"
            elif states:
                state = "checked_with_findings" if counts.get(check) else "checked_no_findings"
            else:
                state = "not_applicable"
            rows.append(_checklist_row(check, state, tested=tested,
                                       skipped=len(not_applicable),
                                       reason="; ".join(filter(None, not_applicable))[:400],
                                       method_version="geng-adapt/1"))
        else:
            rows.append(_checklist_row(check, "not_applicable", reason="no detector output"))
    return rows

def review_sheet(data: bytes, name: str, *, sheet_scope: str = "",
                 regions: Sequence[Mapping[str, Any]] | None = None,
                 field_roles: Mapping[str, Any] | None = None,
                 requested_checks: Sequence[str] | None = None,
                 profile: str = "balanced", header_row: int = 1,
                 header_rows: Sequence[int] | None = None) -> AuditSnapshot:
    """Review one explicitly scoped workbook/table and freeze a portable snapshot."""
    if not isinstance(data, bytes) or not data:
        raise ValueError("review_sheet requires non-empty source bytes")
    if not isinstance(name, str) or not name:
        raise ValueError("review_sheet requires a source name")
    roles = _field_roles(field_roles)
    if profile not in PROFILES:
        raise ValueError("Unknown review profile")
    selected = _selected(requested_checks, profile, roles)
    required_checks = set(selected) if requested_checks is not None else set()
    if regions is not None:
        if header_row != 1 or header_rows is not None:
            raise ValueError("Explicit regions carry their own absolute header rows")
        tables = read_regions(data, name, regions=list(regions), sheet_name=sheet_scope)
        errors = []
    else:
        tables, errors = read_tables(data, name, sheet_name=sheet_scope,
                                     header_row=header_row, header_rows=header_rows)
    legacy_selected = selected & set(RULES)
    paper_selected = selected & set(PAPERCONAN_CHECKS)
    geng_selected = selected & set(GENG_CHECKS)
    entries = []
    all_gaps = []
    detector_sources = {}
    for table in tables:
        result = audit_table(table, checks=sorted(legacy_selected), **_legacy_options(roles))
        findings, resolved, undefined = _filter_special_tokens(result["data"]["findings"], table, roles)
        paper_findings, paper_receipt = detect_paperconan(table, checks=paper_selected, field_roles=roles)
        geng_findings, geng_receipt = detect_geng(table, checks=geng_selected, field_roles=roles)
        for item in paper_findings + geng_findings:
            item["sheet"] = table.sheet
            if table.region_id:
                item["region_id"] = table.region_id
            if not item.get("scope"):
                item["scope"] = "range" if item.get("evidence", {}).get("ranges") else "cell"
            findings.append(item)
        # Split old findings by legacy check; new checklists are additive and
        # never counted as the legacy 38-rule catalogue.
        old_rows = [row for row in result["data"]["checklist"] if row.get("check") in RULES]
        new_rows = _detector_checklist(selected, paper_receipt, geng_receipt,
                                       paper_findings, geng_findings)
        if resolved or undefined:
            new_rows.append(_checklist_row(
                "special_token_resolution", "checked_with_findings" if undefined else "checked_no_findings",
                tested=resolved + undefined,
                reason=(f"declared={resolved}; undefined={undefined}" if undefined else f"declared={resolved}")))
        result["data"]["findings"] = findings
        result["data"]["checklist"] = old_rows + new_rows
        known_checks = {row.get("check") for row in new_rows}
        for row in old_rows + new_rows:
            if row["check"] in selected and row["state"] in {"partial_coverage", "not_applicable", "not_executed"}:
                all_gaps.append({"sheet": table.sheet, "region_id": table.region_id,
                                 "check": row["check"], "state": row["state"],
                                 "reason": row.get("reasons", row.get("reason", ""))})
        # A new relation check that ran but had no hits is explicit coverage.
        for check in paper_selected | geng_selected:
            if check not in known_checks:
                all_gaps.append({"sheet": table.sheet, "region_id": table.region_id,
                                 "check": check, "state": "not_applicable",
                                 "reason": "no detector output; inspect scope and prerequisites"})
        if paper_receipt:
            detector_sources["paperconan"] = {
                "repository": "https://github.com/zixixr/paperconan",
                "commit": "8d116455b23e858651250d8ed23ba41c1a87ab0d",
                "license": "MIT",
                "checks": sorted(paper_selected),
            }
        if geng_receipt:
            detector_sources["geng"] = {
                "repository": "https://github.com/JasonYan-Bio/geng-skills",
                "commit": "3c637844281a054a8dbe08dd96fea8413f3883c0",
                "license": "MIT",
                "checks": sorted(geng_selected),
                "not_run": ["sprite", "image_duplicates"],
            }
        check_partial = any(row["state"] == "partial_coverage" for row in new_rows)
        required_missing = any(
            row.get("state") in {"not_applicable", "not_executed", "partial_coverage", "failed"}
            and row.get("check") in required_checks
            for row in old_rows + new_rows
        )
        failed = any(row.get("state") == "failed" for row in old_rows + new_rows)
        truncated_findings = any(item.get("check") == "findings_truncated" for item in findings)
        if (table.coverage.get("truncated") or check_partial or required_missing
                or failed or truncated_findings):
            result["status"] = "partial"
        else:
            result["status"] = "complete"
        entries.append({"table": table.to_dict(), "result": result})
    if entries and Path(name).suffix.lower() in {".xlsx", ".xlsm"}:
        with closing(open_xlsx_readonly(data, name, data_only=False)) as workbook:
            for table, entry in zip(tables, entries):
                entry["formulas"] = _formula_coordinates(workbook, table)
    report = {
        "version": SCHEMA_VERSION,
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "source_name": name,
        "status": "error" if not entries else "partial" if errors or any(
            entry["result"]["status"] == "partial" for entry in entries) else "complete",
        "tables": entries,
        "errors": errors,
        "review": {
            "contract": REVIEW_VERSION,
            "profile": profile,
            "requested_checks": list(requested_checks) if requested_checks is not None else [],
            "selected_checks": sorted(selected),
            "required_checks": sorted(required_checks),
            "detector_sources": detector_sources,
            "coverage_gaps": all_gaps,
            "claims": [
                "Review covers only the recorded regions and executed checks.",
                "Relationships and distribution deviations are review leads, not proof of misconduct.",
            ],
        },
        "claims": ["Review covers the recorded scopes. Numerical relationships are leads for review."],
    }
    if not entries and not errors:
        report["errors"].append({"code": "no_reviewable_tables", "message": "No table was parsed."})
    return AuditSnapshot(data, name, dumps(report), report["source_sha256"])
