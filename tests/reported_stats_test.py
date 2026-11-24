from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from openpyxl import load_workbook

from citrus_audit import (adjust_p_values, audit_p_values, compare_p_value,
                          compare_reported_summaries, compare_summary_values,
                          export_workbook, merge_review_findings, p_from_samples,
                          p_from_statistic, parse_reported_p, recompute_p_value,
                          recompute_summary, review_sheet, summarize_groups)
from citrus_audit.integrity import table_from_rows
sys.path.insert(0, str(Path(__file__).resolve().parent))
from review_sheet_test import REGIONS, _golden_workbook


def test_distribution_reference_values():
    assert p_from_statistic(2.0, "t", df=10) == pytest.approx(0.07338803477074037, abs=1e-12)
    assert p_from_statistic(3.841, "chi2", df=1, tails="greater") == pytest.approx(0.050013683763956804, abs=1e-12)
    assert p_from_statistic(4.0, "f", df=[1, 10], tails="greater") == pytest.approx(0.07338803477074037, abs=1e-12)
    assert p_from_statistic(1.96, "z") == pytest.approx(0.04999579029644087, abs=1e-12)
    assert p_from_statistic(-2.0, "t", df=10, tails="less") == pytest.approx(0.03669401738537018, abs=1e-12)


def test_recompute_summary_is_exact_and_keeps_n_lt_two_honest():
    result = recompute_summary(["1", "2", "3", "4", "5"])
    assert result["n"] == 5
    assert result["sum"] == "15"
    assert float(result["mean"]) == pytest.approx(3.0, abs=1e-15)
    assert float(result["sd"]) == pytest.approx(1.5811388300841897, abs=1e-12)
    assert float(result["se"]) == pytest.approx(0.7071067811865476, abs=1e-12)
    one = recompute_summary(["2.5"])
    assert one["sd"] is None and one["se"] is None and "reason" in one
    with pytest.raises(ValueError):
        recompute_summary([])
    with pytest.raises(ValueError):
        recompute_summary(["nan"])


def test_summarize_groups_and_compare_summary_rounding():
    grouped = summarize_groups({"A": [1, 2, 3], "B": [4, 5]})
    assert grouped["A"]["mean"] == "2"
    assert grouped["B"]["n"] == 2
    computed = recompute_summary([1, 2, 3, 4, 5])
    reported = {"n": 5, "sum": "15", "mean": "3.0", "sd": "1.581", "se": "0.707"}
    decimals = {"sum": 0, "mean": 1, "sd": 3, "se": 3}
    receipt = compare_summary_values(computed, reported, decimals=decimals,
                                     sample_unit_confirmed=True,
                                     same_units_confirmed=True, sd_kind="sample")
    assert receipt["status"] == "checked"
    assert all(item.get("discrepancy") is False for item in receipt["results"])
    wrong = compare_summary_values(computed, {**reported, "mean": "2.9"},
                                   decimals=decimals, sample_unit_confirmed=True,
                                   same_units_confirmed=True, sd_kind="sample")
    assert next(item for item in wrong["results"] if item["metric"] == "mean")["discrepancy"] is True
    unconfirmed = compare_summary_values(computed, reported, decimals=decimals)
    assert unconfirmed["results"][1]["status"] == "not_checked"


def test_p_value_from_samples_matches_reference():
    first, second = [1, 2, 3, 4, 5], [2, 4, 6, 8, 10]
    welch = p_from_samples({"groups": [first, second]}, test="welch_t")
    assert welch["statistic"] == pytest.approx(-1.8973665961010275, abs=1e-12)
    assert welch["df"] == pytest.approx(5.882352941176471, abs=1e-12)
    assert welch["p"] == pytest.approx(0.10753119493062728, abs=1e-12)
    student = p_from_samples({"groups": [first, second]}, test="student_t")
    assert student["p"] == pytest.approx(0.09434977284243762, abs=1e-12)
    paired = p_from_samples({"groups": [first, [2, 4, 5, 8, 9]]}, test="paired_t")
    assert paired["p"] == pytest.approx(0.012317352470240387, abs=1e-12)


def test_p_value_from_anova_pearson_and_contingency():
    anova = p_from_samples({"groups": [[1, 2, 3], [2, 3, 4], [5, 6, 7]]}, test="one_way_anova")
    assert anova["statistic"] == pytest.approx(13.0, abs=1e-12)
    assert anova["p"] == pytest.approx(0.006591796875, abs=1e-12)
    pearson = p_from_samples({"x": [1, 2, 3, 4, 5], "y": [2, 4, 5, 4, 5]}, test="pearson")
    assert pearson["r"] == pytest.approx(0.7745966692414835, abs=1e-12)
    assert pearson["p"] == pytest.approx(0.1240270626575546, abs=1e-12)
    table = [[10, 20], [30, 40]]
    plain = p_from_samples({"table": table}, test="chi2_contingency")
    assert plain["statistic"] == pytest.approx(0.7936507936507936, abs=1e-12)
    assert plain["p"] == pytest.approx(0.3729984836134873, abs=1e-12)
    yates = p_from_samples({"table": table}, test="chi2_contingency", correction=True)
    assert yates["statistic"] == pytest.approx(0.4464285714285714, abs=1e-12)
    assert yates["p"] == pytest.approx(0.5040358664525046, abs=1e-12)
    assert yates["correction"] == "yates"


def test_reported_p_inequalities_and_rounding_intervals():
    assert parse_reported_p("P<0.001")["operator"] == "<"
    assert compare_p_value(0.0004, "P<0.001")["consistent"] is True
    assert compare_p_value(0.001, "P<0.001")["consistent"] is False
    assert compare_p_value(0.001, "P<=0.001")["consistent"] is True
    assert compare_p_value(0.0321, "P = 0.032")["consistent"] is True
    assert compare_p_value(0.0330, "0.032")["consistent"] is False
    assert compare_p_value(0.05, "ns")["status"] == "not_checked"
    assert compare_p_value(0.0, 0.0)["status"] == "not_checked"


def test_adjust_p_values_holm_and_bh():
    assert adjust_p_values([0.01, 0.04, 0.03], method="holm") == pytest.approx([0.03, 0.06, 0.06])
    assert adjust_p_values([0.01, 0.04, 0.03], method="bh") == pytest.approx([0.03, 0.04, 0.04])
    assert adjust_p_values([], method="holm") == []


def test_audit_p_values_reports_mismatch_and_adjustment():
    receipt = audit_p_values([
        {"mode": "statistic", "statistic": 2.0, "distribution": "t", "df": 10,
         "reported_p": "0.05", "reported_decimals": 2},
        {"mode": "samples", "test": "welch_t",
         "groups": [[1, 2, 3, 4, 5], [2, 4, 6, 8, 10]],
         "reported_p": "0.2", "reported_decimals": 1},
    ], correction="holm", alpha=0.05)
    assert receipt["coverage"]["requested"] == 2
    assert receipt["coverage"]["applicable"] == 2
    assert receipt["checks"][0]["comparison"]["consistent"] is False
    assert receipt["checks"][0]["p_adjusted"] <= 1
    assert receipt["findings"][0]["check"] == "reported_p_value_mismatch"
    assert receipt["findings"][0]["severity"] == "review"


def test_recompute_p_value_chi2_defaults_to_upper_tail():
    result = recompute_p_value({"mode": "statistic", "statistic": 3.841,
                                "distribution": "chi2", "df": 1})
    assert result["tails"] == "greater"
    assert result["p"] == pytest.approx(0.050013683763956804, abs=1e-12)


def test_recompute_p_value_dispatch():
    assert recompute_p_value({"mode": "statistic", "statistic": 2.0, "distribution": "t",
                              "df": 10, "reported_p": "0.073"})["comparison"]["consistent"] is True
    samples = recompute_p_value({"mode": "samples", "test": "paired_t",
                                 "groups": [[1, 2, 3], [2, 4, 6]]})
    assert samples["applicable"] is True and samples["mode"] == "samples"
    with pytest.raises(ValueError):
        recompute_p_value({"mode": "unknown"})


def test_compare_reported_summaries_supports_sum_and_reports_mismatch():
    raw = table_from_rows([[1], [2], [3], [4], [5]], ["value"],
                          source_id="attachment:0:raw", source_name="raw.xlsx",
                          source_bytes=b"raw", sheet="Raw")
    report = table_from_rows([[5, 15, 3.0, 1.581, 0.707]],
                             ["n", "sum", "mean", "sd", "se"],
                             source_id="attachment:1:report", source_name="report.xlsx",
                             source_bytes=b"report", sheet="Report")
    binding = {"raw_file_index": 0, "raw_sheet": "Raw",
               "reported_file_index": 1, "reported_sheet": "Report",
               "raw_column": "value", "reported_row": 2,
               "columns": {"n": "n", "sum": "sum", "mean": "mean", "sd": "sd", "se": "se"},
               "decimals": {"sum": 0, "mean": 1, "sd": 3, "se": 3},
               "sample_unit_confirmed": True, "same_units_confirmed": True,
               "sd_kind": "sample"}
    receipt = compare_reported_summaries([raw, report], [binding])
    assert receipt["coverage"]["completed_bindings"] == 1
    assert not receipt["common_findings"]
    assert receipt["checks"][0]["results"][1]["metric"] == "sum"
    wrong = table_from_rows([[5, 15, 2.9, 1.581, 0.707]],
                            ["n", "sum", "mean", "sd", "se"],
                            source_id="attachment:1:report", source_name="report.xlsx",
                            source_bytes=b"report", sheet="Report")
    bad = compare_reported_summaries([raw, wrong], [binding])
    assert bad["common_findings"][0]["rule"] == "reported_mean_differs_from_raw"


def test_merge_review_findings_keeps_source_and_exports_new_row():
    source = _golden_workbook()
    snapshot = review_sheet(source, "golden.xlsx", regions=REGIONS[:1],
                            requested_checks=["duplicates"],
                            field_roles={"sample_keys": ["sample_id"],
                                         "value_columns": ["raw", "converted", "shifted"]})
    merged = merge_review_findings(
        snapshot,
        [{"severity": "review", "check": "reported_p_value_mismatch",
          "detail": "报告 P 值与复算不一致。", "sheet": "Measurements", "region_id": "r1",
          "row": 4, "column_index": 3, "scope": "cell"}],
        checks=[{"check": "reported_p_value_mismatch", "state": "checked_with_findings",
                 "tested": 1, "skipped": 0, "reason": "", "method_version": "reported-stats/1"}])
    assert merged.source_sha256 == snapshot.source_sha256
    blob, receipt = export_workbook(merged)
    assert receipt["marked_locations"] > 0
    workbook = load_workbook(BytesIO(blob))
    try:
        values = [cell.value for row in workbook["审查结果"].iter_rows() for cell in row]
        assert "P 值不一致" in values
    finally:
        workbook.close()

def _reported_binding_workbook(*, mean=3.0, p=0.073):
    from openpyxl import Workbook
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Stats"
    sheet.append(["sample_id", "group", "value", "", "n", "sum", "mean", "sd", "se", "", "t", "df", "p"])
    for index, value in enumerate([1, 2, 3, 4, 5], start=2):
        sheet.cell(index, 1, f"s{index}")
        sheet.cell(index, 2, "A")
        sheet.cell(index, 3, value)
    sheet.cell(2, 5, 5)
    sheet.cell(2, 6, 15)
    sheet.cell(2, 7, mean)
    sheet.cell(2, 8, 1.581)
    sheet.cell(2, 9, 0.707)
    sheet.cell(2, 11, 2.0)
    sheet.cell(2, 12, 10)
    sheet.cell(2, 13, p)
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


REPORTED_REGIONS = [
    {"sheet": "Stats", "bounds": "A1:C6", "header_rows": [1], "region_id": "r1"},
    {"sheet": "Stats", "bounds": "E1:I2", "header_rows": [1], "region_id": "r2"},
    {"sheet": "Stats", "bounds": "K1:M2", "header_rows": [1], "region_id": "r3"},
]


def test_review_sheet_accepts_explicit_reported_summary_and_p_bindings():
    source = _reported_binding_workbook()
    snapshot = review_sheet(
        source, "reported.xlsx", regions=REPORTED_REGIONS,
        field_roles={"sample_keys": ["sample_id"], "value_columns": ["value"]},
        reported_summaries=[{
            "raw": {"region_id": "r1", "column": "value", "rows": [2, 3, 4, 5, 6]},
            "reported": {"region_id": "r2", "row": 2,
                         "columns": {"n": "n", "sum": "sum", "mean": "mean", "sd": "sd", "se": "se"}},
            "decimals": {"sum": 0, "mean": 1, "sd": 3, "se": 3},
            "sample_unit_confirmed": True, "same_units_confirmed": True, "sd_kind": "sample",
        }],
        reported_p_values=[{
            "mode": "statistic", "distribution": "t", "df": 10, "tails": "two",
            "statistic": {"region_id": "r3", "row": 2, "column": "t"},
            "reported_p": {"region_id": "r3", "row": 2, "column": "p", "decimals": 3},
            "location": {"region_id": "r3", "row": 2, "column": "p"},
        }])
    report = snapshot.report
    assert report["status"] == "complete"
    assert report["review"]["reported_stats"]["summaries"]["coverage"]["completed"] == 1
    assert report["review"]["reported_stats"]["p_values"]["coverage"]["completed"] == 1
    checks = {item["check"] for entry in report["tables"] for item in entry["result"]["data"]["findings"]}
    assert "reported_mean_differs_from_raw" not in checks
    assert "reported_p_value_mismatch" not in checks
    blob, receipt = export_workbook(snapshot)
    assert receipt["source_sha256"] == snapshot.source_sha256


def test_review_sheet_accepts_raw_sample_p_binding():
    source = _reported_binding_workbook()
    snapshot = review_sheet(
        source, "reported.xlsx", regions=REPORTED_REGIONS,
        field_roles={"sample_keys": ["sample_id"], "value_columns": ["value"]},
        reported_p_values=[{
            "mode": "samples", "test": "welch_t", "tails": "two",
            "groups": [
                {"region_id": "r1", "column": "value", "rows": [2, 3, 4]},
                {"region_id": "r1", "column": "value", "rows": [5, 6]},
            ],
        }])
    check = snapshot.report["review"]["reported_stats"]["p_values"]["checks"][0]
    assert check["state"] == "checked_no_findings"
    assert snapshot.report["status"] == "complete"


def test_review_sheet_reports_reported_summary_and_p_mismatch():
    source = _reported_binding_workbook(mean=2.9, p=0.05)
    snapshot = review_sheet(
        source, "reported.xlsx", regions=REPORTED_REGIONS,
        field_roles={"sample_keys": ["sample_id"], "value_columns": ["value"]},
        reported_summaries=[{
            "raw": {"region_id": "r1", "column": "value", "rows": [2, 3, 4, 5, 6]},
            "reported": {"region_id": "r2", "row": 2,
                         "columns": {"n": "n", "sum": "sum", "mean": "mean", "sd": "sd", "se": "se"}},
            "decimals": {"sum": 0, "mean": 1, "sd": 3, "se": 3},
            "sample_unit_confirmed": True, "same_units_confirmed": True, "sd_kind": "sample",
        }],
        reported_p_values=[{
            "mode": "statistic", "distribution": "t", "df": 10, "tails": "two",
            "statistic": {"region_id": "r3", "row": 2, "column": "t"},
            "reported_p": {"region_id": "r3", "row": 2, "column": "p", "decimals": 3},
            "location": {"region_id": "r3", "row": 2, "column": "p"},
        }])
    checks = {item["check"] for entry in snapshot.report["tables"]
              for item in entry["result"]["data"]["findings"]}
    assert "reported_mean_differs_from_raw" in checks
    assert "reported_p_value_mismatch" in checks
    assert snapshot.report["status"] == "complete"
    assert not snapshot.report["review"]["reported_stats"]["summaries"]["checks"][0]["reason"]


def test_review_sheet_cli_accepts_reported_bindings(tmp_path):
    source = tmp_path / "reported.xlsx"
    source.write_bytes(_reported_binding_workbook())
    options = tmp_path / "options.json"
    options.write_text(json.dumps({
        "regions": REPORTED_REGIONS,
        "field_roles": {"sample_keys": ["sample_id"], "value_columns": ["value"]},
        "reported_summaries": [{
            "raw": {"region_id": "r1", "column": "value", "rows": [2, 3, 4, 5, 6]},
            "reported": {"region_id": "r2", "row": 2,
                         "columns": {"n": "n", "sum": "sum", "mean": "mean", "sd": "sd", "se": "se"}},
            "decimals": {"sum": 0, "mean": 1, "sd": 3, "se": 3},
            "sample_unit_confirmed": True, "same_units_confirmed": True, "sd_kind": "sample",
        }],
        "reported_p_values": [{
            "mode": "statistic", "distribution": "t", "df": 10, "tails": "two",
            "statistic": {"region_id": "r3", "row": 2, "column": "t"},
            "reported_p": {"region_id": "r3", "row": 2, "column": "p", "decimals": 3},
        }],
    }), encoding="utf-8")
    snapshot_path = tmp_path / "snapshot.json"
    process = subprocess.run(
        [sys.executable, "-m", "citrus_audit.cli", "review-sheet", str(source),
         "--options", str(options), "--snapshot", str(snapshot_path)],
        capture_output=True, text=True, env=os.environ.copy(), check=False)
    assert process.returncode == 0, process.stdout + process.stderr
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert payload["report"]["status"] == "complete"
    assert payload["report"]["review"]["reported_stats"]["summaries"]["coverage"]["completed"] == 1


def test_review_sheet_marks_unresolved_reported_binding_partial():
    source = _reported_binding_workbook()
    snapshot = review_sheet(
        source, "reported.xlsx", regions=REPORTED_REGIONS,
        field_roles={"sample_keys": ["sample_id"], "value_columns": ["value"]},
        reported_summaries=[{"raw": {"region_id": "r1"}}])
    assert snapshot.report["status"] == "partial"
    check = snapshot.report["review"]["reported_stats"]["summaries"]["checks"][0]
    assert check["state"] == "not_applicable" and check["reason"]
    assert any(gap["check"] == "reported_summary_binding"
               for gap in snapshot.report["review"]["coverage_gaps"])
