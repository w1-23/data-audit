from __future__ import annotations

import hashlib
from io import BytesIO
import re
from zipfile import ZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import PatternFill

from citrus_audit import AuditSnapshot, export_workbook, review_sheet
from citrus_audit.reporting import dumps, loads


REGIONS = [
    {"sheet": "Measurements", "bounds": "A3:G11", "header_rows": [3], "region_id": "r1"},
    {"sheet": "Measurements", "bounds": "I3:O11", "header_rows": [3], "region_id": "r2"},
    {"sheet": "Measurements", "bounds": "A15:G23", "header_rows": [15], "region_id": "r3"},
    {"sheet": "Measurements", "bounds": "I15:O23", "header_rows": [15], "region_id": "r4"},
    {"sheet": "Measurements", "bounds": "A27:G35", "header_rows": [27], "region_id": "r5"},
    {"sheet": "Measurements", "bounds": "I27:O35", "header_rows": [27], "region_id": "r6"},
]


def _golden_workbook():
    wb = Workbook()
    ws = wb.active
    ws.title = "Measurements"
    headers = ["sample_id", "treatment", "raw", "converted", "shifted", "calc", "note"]
    for top in (3, 15, 27):
        for left in (1, 9):
            for offset, value in enumerate(headers):
                ws.cell(top, left + offset, value)
            for row in range(top + 1, top + 9):
                sample = f"s{row}"
                raw = row - top
                treatment = "CK" if left == 1 else "WL"
                ws.cell(row, left, sample)
                ws.cell(row, left + 1, treatment)
                ws.cell(row, left + 2, raw if not (top == 15 and row == 16) else "ND")
                ws.cell(row, left + 3, raw * 2)
                ws.cell(row, left + 4, raw + 7)
                ws.cell(row, left + 5, f"=C{row}*2")
                ws.cell(row, left + 6, "ND" if row == top + 1 else "")
    block = ((1, 2), (3, 4), (5, 6))
    for index, pair in enumerate(block):
        for offset, value in enumerate(pair):
            ws.cell(4 + index, 12 + offset, value)
            ws.cell(9 + index, 12 + offset, value)
    ws["A4"].comment = Comment("original note", "owner")
    ws["A4"].fill = PatternFill("solid", fgColor="DDEBF7")
    other = wb.create_sheet("Unreviewed")
    other["A1"] = "do not touch"
    stream = BytesIO()
    wb.save(stream)
    source = stream.getvalue()
    output = BytesIO()
    with ZipFile(BytesIO(source)) as archive, ZipFile(output, "w") as copy:
        for member in archive.infolist():
            value = archive.read(member)
            if member.filename == "xl/worksheets/sheet1.xml":
                value, count = re.subn(
                    rb"<f>C4\*2</f>(?:<v></v>|<v\s*/>)",
                    b"<f>C4*2</f><v>2</v>", value, count=1)
                assert count == 1
            copy.writestr(member, value)
    return output.getvalue()


def test_review_sheet_golden_six_regions_and_one_annotated_workbook():
    source = _golden_workbook()
    digest = hashlib.sha256(source).hexdigest()
    snapshot = review_sheet(
        source,
        "golden.xlsx",
        sheet_scope="Measurements",
        regions=REGIONS,
        field_roles={
            "sample_keys": ["sample_id"],
            "group_columns": ["treatment"],
            "value_columns": ["raw", "converted", "shifted"],
            "required_fields": ["sample_id", "raw"],
            "special_tokens": {"ND": "not_detected"},
        },
    )
    report = snapshot.report
    assert report["status"] == "complete"
    assert report["review"]["coverage_gaps"]
    assert report["review"]["contract"] == "review-sheet/1"
    assert report["review"]["detector_sources"]["paperconan"]["commit"] == "8d116455b23e858651250d8ed23ba41c1a87ab0d"
    assert len(report["tables"]) == 6
    checks = {item["check"] for entry in report["tables"] for item in entry["result"]["data"]["findings"]}
    assert "pc_constant_offset_columns" in checks
    assert "pc_constant_ratio_columns" in checks
    assert "pc_repeated_blocks" in checks
    assert "constant_offset_columns" not in checks
    assert any(row["check"] == "pc_constant_offset_columns"
               and row["state"] == "checked_with_findings"
               for entry in report["tables"]
               for row in entry["result"]["data"]["checklist"])
    assert "special_token_undefined" not in checks
    assert all(item.get("row") != 16 for item in report["tables"][2]["result"]["data"]["findings"]
               if item["check"] == "non_numeric_cell")
    formulas = [item for entry in report["tables"] for item in entry.get("formulas", [])]
    assert any(item["formula"] == "=C4*2" and item["cache_status"].startswith("present") for item in formulas)

    restored = AuditSnapshot.from_dict(loads(dumps(snapshot.to_dict())))
    assert restored.source_sha256 == digest and restored.report_sha256 == snapshot.report_sha256
    blob, receipt = export_workbook(restored)
    assert receipt["presentation"] == "annotated"
    assert receipt["summary_sheet"] == "审查结果" and receipt["summary_rows"] >= 3
    assert receipt["data_region_unchanged"] and receipt["scan_reused"]
    assert receipt["marked_locations"] > 0
    assert hashlib.sha256(source).hexdigest() == digest

    exported = load_workbook(BytesIO(blob), data_only=False, rich_text=True)
    try:
        assert exported.sheetnames == ["Measurements", "Unreviewed", "审查结果"]
        assert exported["Unreviewed"]["A1"].value == "do not touch"
        assert exported["Measurements"]["A4"].comment.text.startswith("original note")
        assert exported["Measurements"]["C4"].border.left.style == "thin"
        assert exported["审查结果"]["A2"].value == "位置"
        assert exported["审查结果"]["B2"].value == "类型"
        assert exported["审查结果"]["C2"].value == "结论"
        assert exported["审查结果"]["A3"].hyperlink is not None
        assert exported["Measurements"]["F4"].value.startswith("=")
    finally:
        exported.close()
    cached = load_workbook(BytesIO(blob), data_only=True)
    try:
        assert cached["Measurements"]["F4"].value == 2
    finally:
        cached.close()


def test_duplicate_profile_does_not_activate_unrequested_methods():
    source = _golden_workbook()
    snapshot = review_sheet(source, "golden.xlsx", regions=REGIONS[:1],
                            requested_checks=["duplicates"],
                            field_roles={"value_columns": ["raw", "converted", "shifted"]})
    selected = set(snapshot.report["review"]["selected_checks"])
    assert "pc_repeated_blocks" in selected
    assert "geng_last_digit" not in selected
    assert "outlier" not in " ".join(selected)
    assert all(not item["check"].startswith("geng_") for item in
               snapshot.report["tables"][0]["result"]["data"]["findings"])


def test_duplicate_profile_does_not_add_iqr_implicitly():
    source = _golden_workbook()
    snapshot = review_sheet(source, "golden.xlsx", regions=REGIONS[:1],
                            profile="duplicates",
                            field_roles={"group_columns": ["treatment"],
                                         "value_columns": ["raw", "converted", "shifted"]})
    selected = set(snapshot.report["review"]["selected_checks"])
    assert "iqr_outlier" not in selected and "iqr_insufficient" not in selected
    assert "pc_repeated_blocks" in selected


def test_optional_geng_checks_are_scoped_and_have_no_risk_score():
    wb = Workbook()
    ws = wb.active
    ws.title = "Numeric"
    ws.append(["mean", "n", "value"])
    for index in range(1, 61):
        ws.append([2.25 if index == 1 else index, 2 if index == 1 else 3, f"{index}.0"])
    stream = BytesIO()
    wb.save(stream)
    snapshot = review_sheet(
        stream.getvalue(), "numeric.xlsx", sheet_scope="Numeric",
        requested_checks=["geng_last_digit", "geng_grim"],
        field_roles={
            "value_columns": ["value"],
            "precision_confirmed": ["value"],
            "grim": [{"row": 2, "mean_column": "mean", "n_column": "n",
                      "decimals": 1, "min_value": 1, "max_value": 4}],
        },
    )
    findings = snapshot.report["tables"][0]["result"]["data"]["findings"]
    checks = {item["check"] for item in findings}
    assert "geng_last_digit" in checks
    assert "geng_grim" in checks
    assert all("risk_score" not in item and "risk_level" not in item for item in findings)
    checklist = snapshot.report["tables"][0]["result"]["data"]["checklist"]
    assert any(item["check"] == "geng_grim" for item in checklist)


def test_export_legacy_presentation_stays_available():
    source = _golden_workbook()
    snapshot = review_sheet(source, "golden.xlsx", regions=REGIONS[:1],
                            requested_checks=["duplicate_row"])
    blob, receipt = export_workbook(snapshot, presentation="legacy")
    assert receipt["presentation"] == "legacy"
    assert receipt["summary_sheet"] == ""
    workbook = load_workbook(BytesIO(blob))
    try:
        assert workbook.sheetnames == ["Measurements", "Unreviewed"]
    finally:
        workbook.close()


def test_zero_finding_uses_the_same_single_sheet_contract(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Clean"
    ws.append(["id", "value"])
    ws.append([1, 2])
    ws.append([2, 3])
    stream = BytesIO()
    wb.save(stream)
    snapshot = review_sheet(stream.getvalue(), "clean.xlsx", sheet_scope="Clean",
                            requested_checks=["duplicate_row"],
                            field_roles={"sample_keys": ["id"], "value_columns": ["value"]})
    blob, receipt = export_workbook(snapshot)
    assert receipt["summary_rows"] == 0
    exported = load_workbook(BytesIO(blob))
    try:
        assert exported.sheetnames == ["Clean", "审查结果"]
        assert exported["审查结果"]["B3"].value == "未发现异常"
    finally:
        exported.close()


def test_balanced_not_applicable_is_coverage_not_partial():
    wb = Workbook()
    ws = wb.active
    ws.title = "TwoColumns"
    ws.append(["a", "b"])
    for index in range(1, 9):
        ws.append([index, index + (index % 3)])
    stream = BytesIO()
    wb.save(stream)
    snapshot = review_sheet(stream.getvalue(), "two.xlsx", sheet_scope="TwoColumns",
                            field_roles={"value_columns": ["a", "b"]})
    assert snapshot.report["status"] == "complete"
    assert snapshot.report["review"]["required_checks"] == []
    assert snapshot.report["review"]["coverage_gaps"]


def test_explicit_duplicates_does_not_add_iqr_or_require_unrequested_checks():
    wb = Workbook()
    ws = wb.active
    ws.title = "Values"
    ws.append(["group", "value"])
    for group in ("A", "B"):
        for index in range(8):
            ws.append([group, index + (100 if index == 7 and group == "B" else 0)])
    stream = BytesIO()
    wb.save(stream)
    snapshot = review_sheet(stream.getvalue(), "values.xlsx", sheet_scope="Values",
                            requested_checks=["duplicates"],
                            field_roles={"group_columns": ["group"], "value_columns": ["value"]})
    selected = set(snapshot.report["review"]["selected_checks"])
    assert "iqr_outlier" not in selected and "iqr_insufficient" not in selected
    assert "duplicate_key" in selected  # This is still a duplicate check; no IQR is added.


def test_explicit_specialist_without_prerequisite_is_partial_and_reported():
    wb = Workbook()
    ws = wb.active
    ws.title = "NoTokens"
    ws.append(["value"])
    ws.append([1])
    ws.append([2])
    stream = BytesIO()
    wb.save(stream)
    snapshot = review_sheet(stream.getvalue(), "notokens.xlsx", sheet_scope="NoTokens",
                            requested_checks=["geng_last_digit"],
                            field_roles={"value_columns": ["value"]})
    assert snapshot.report["status"] == "partial"
    assert snapshot.report["review"]["required_checks"] == ["geng_last_digit"]
    assert any(gap["check"] == "geng_last_digit" and gap["state"] == "not_applicable"
               for gap in snapshot.report["review"]["coverage_gaps"])



def test_paperconan_row_direction_is_independently_selected():
    wb = Workbook()
    ws = wb.active
    ws.title = "Rows"
    ws.append(["a", "b", "c"])
    ws.append([1, 2, 3])
    ws.append([11, 12, 13])
    ws.append([4, 5, 6])
    stream = BytesIO()
    wb.save(stream)
    snapshot = review_sheet(stream.getvalue(), "rows.xlsx", sheet_scope="Rows",
                            requested_checks=["relations"],
                            field_roles={"value_columns": ["a", "b", "c"]})
    checks = {item["check"] for item in snapshot.report["tables"][0]["result"]["data"]["findings"]}
    assert "pc_constant_offset_rows" in checks


def test_review_sheet_cli_writes_portable_snapshot(tmp_path):
    import json
    import os
    import subprocess
    import sys
    wb = Workbook()
    ws = wb.active
    ws.title = "Clean"
    ws.append(["id", "value"])
    ws.append([1, 2])
    source = tmp_path / "clean.xlsx"
    wb.save(source)
    options = tmp_path / "options.json"
    options.write_text(json.dumps({"field_roles": {"sample_keys": ["id"], "value_columns": ["value"]},
                                   "requested_checks": ["duplicate_row"]}), encoding="utf-8")
    snapshot_path = tmp_path / "snapshot.json"
    process = subprocess.run(
        [sys.executable, "-m", "citrus_audit.cli", "review-sheet", str(source),
         "--options", str(options), "--snapshot", str(snapshot_path), "--sheet", "Clean"],
        capture_output=True, text=True, env=os.environ.copy(), check=False)
    assert process.returncode == 0, process.stdout + process.stderr
    reply = json.loads(process.stdout)
    assert reply["review_contract"] == "review-sheet/1"
    assert snapshot_path.exists()



def test_explicit_legacy_check_without_prerequisite_is_partial():
    wb = Workbook()
    ws = wb.active
    ws.title = "NoRange"
    ws.append(["value"])
    ws.append([2])
    stream = BytesIO()
    wb.save(stream)
    snapshot = review_sheet(stream.getvalue(), "norange.xlsx", sheet_scope="NoRange",
                            requested_checks=["out_of_range"],
                            field_roles={"value_columns": ["value"]})
    assert snapshot.report["status"] == "partial"
    assert any(gap["check"] == "out_of_range" and gap["state"] == "not_applicable"
               for gap in snapshot.report["review"]["coverage_gaps"])
