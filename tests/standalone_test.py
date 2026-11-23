"""Standalone acceptance with generated sources, frozen reports and copy-only exports."""
from datetime import time, timedelta
import hashlib
from io import BytesIO
import json
import os
import re
import subprocess
import sys
from zipfile import ZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
import pytest

from citrus_audit import AuditSnapshot, compare_snapshots, discover_regions, export_workbook, review_bytes
from citrus_audit.reporting import dumps, loads

REGIONS = [{'sheet': 'Measurements', 'bounds': 'D31:AC38', 'header_rows': [31], 'region_id': 'left'},
           {'sheet': 'Measurements', 'bounds': 'AF31:BE38', 'header_rows': [31], 'region_id': 'right'}]


def workbook_bytes():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Measurements'
    for start in [4, 32]:
        for offset, label in enumerate(['sample', 'value', 'derived'] + [f'm{index}' for index in range(23)]):
            sheet.cell(31, start + offset, label)
        for row in range(32, 39):
            for offset in range(26):
                sheet.cell(row, start + offset, f'id{row}' if offset == 0 else row + offset)
    for col in range(4, 30):
        sheet.cell(33, col).value = sheet.cell(32, col).value
    sheet['E34'] = 'text'
    sheet['E35'].value = None
    sheet['F34'] = '=40+2'
    sheet['F35'] = '=40+3'
    sheet['G34'] = time(12, 30)
    sheet['G35'] = timedelta(hours=27)
    sheet['AG36'] = 200
    sheet['A34'] = 'unreviewed'
    sheet['A34'].comment = Comment('existing', 'author')
    workbook.create_sheet('Other')['A1'] = 'untouched'
    stream = BytesIO()
    workbook.save(stream)
    output = BytesIO()
    with ZipFile(stream) as archive, ZipFile(output, 'w') as copy:
        for member in archive.infolist():
            data = archive.read(member)
            if member.filename == 'xl/worksheets/sheet1.xml':
                data, count = re.subn(rb'<f>40\+2</f>(?:<v></v>|<v\s*/>)', b'<f>40+2</f><v>42</v>', data)
                assert count == 1
            copy.writestr(member, data)
    return output.getvalue()


def cli(*args, env=None):
    completed = subprocess.run([sys.executable, '-m', 'citrus_audit.cli', *map(str, args)],
                               capture_output=True, check=False, text=True, env=env)
    return completed, loads(completed.stdout)


def test_regions_snapshot_and_consolidated_workbook(tmp_path, monkeypatch):
    path = tmp_path / 'measurements.xlsx'
    source = workbook_bytes()
    path.write_bytes(source)
    discovery = discover_regions(source, path.name, sheet_name='Measurements', header_rows=[31])
    assert {item['bounds'] for item in discovery['regions']} >= {item['bounds'] for item in REGIONS}
    snapshot = review_bytes(path.read_bytes(), path.name, regions=REGIONS,
                            checks=['empty_table', 'duplicate_row', 'non_numeric_cell', 'missing_cell', 'out_of_range'],
                            values=['value'], required_fields=['value'], ranges={'value': {'min': 0, 'max': 100}})
    frozen = AuditSnapshot.from_dict(loads(dumps(snapshot.to_dict())))
    assert frozen.report_sha256 == snapshot.report_sha256
    left, right = frozen.report['tables']
    assert [(entry['table']['header_rows'], len(entry['table']['columns'])) for entry in (left, right)] == [([31], 26)] * 2
    assert left['table']['row_numbers'] == list(range(32, 39))
    assert right['table']['column_numbers'] == list(range(32, 58))
    assert right['table']['values'][4][1] == 200
    assert left['table']['values'][2][2] == 42
    assert [(item['row'], item['column_index'], item['cache_status']) for item in left['formulas']] == [
        (34, 6, 'present_unverified'), (35, 6, 'absent_or_blank')]
    hits = {item['check']: item for item in left['result']['data']['findings']}
    assert hits['non_numeric_cell']['row'] == 34 and hits['non_numeric_cell']['column_index'] == 5
    assert any(item['check'] == 'missing_cell' and (item['row'], item['column_index']) == (35, 5)
               for item in left['result']['data']['findings'])
    assert hits['duplicate_row']['severity'] == 'review'
    assert frozen.compact()['findings_returned'] > 0
    assert 'sample' not in dumps(frozen.compact())
    assert len(dumps(frozen.compact()).encode('ascii')) < 2048

    def no_scan(*args, **kwargs):
        pytest.fail('Export reran audit')

    monkeypatch.setattr('citrus_audit.api.audit_frame', no_scan)
    output, receipt = export_workbook(frozen, issue_list=True, overview=True, rules=True)
    assert receipt['scan_reused'] and receipt['data_region_unchanged']
    assert receipt['export_sha256'] == hashlib.sha256(output).hexdigest()
    assert path.read_bytes() == source
    workbook = load_workbook(BytesIO(output))
    sheet = workbook['Measurements']
    assert sheet['E34'].comment and sheet['E35'].comment and sheet['AG36'].comment
    assert sheet['A34'].comment.text == 'existing' and sheet['A34'].value == 'unreviewed'
    assert sheet['AE36'].comment is None and not sheet['AE36'].has_style
    assert sheet['F34'].value == '=40+2' and sheet['F35'].value == '=40+3'
    assert workbook['Other']['A1'].value == 'untouched' and workbook['Other']['A1'].comment is None
    assert load_workbook(BytesIO(output), data_only=True)['Measurements']['F34'].value == 42
    with pytest.raises(ValueError, match='report hash'):
        payload = frozen.to_dict()
        payload['report']['tables'][0]['result']['data']['findings'].clear()
        AuditSnapshot.from_dict(payload)


def test_zero_empty_skips_and_source_retention(tmp_path):
    path = tmp_path / 'clean.csv'
    snapshot = tmp_path / 'snapshot.json'
    report = tmp_path / 'report.json'
    path.write_bytes(b'a,b\n1,4\n2,9\n')
    command = ['review', path, '--snapshot', snapshot, '--checks', 'duplicate_row', 'iqr_outlier']
    process, reply = cli(*command)
    assert process.returncode == 0 and reply['findings_returned'] == 0
    assert reply['checklist_counts']['checked_no_findings'] == 1
    assert reply['checklist_counts']['not_applicable'] == 1
    assert not process.stderr
    assert path.read_bytes() == b'a,b\n1,4\n2,9\n'
    assert AuditSnapshot.from_dict(loads(snapshot.read_text())).source_bytes == path.read_bytes()
    assert cli('report', snapshot, '--output', report)[0].returncode == 0
    assert reply['report_sha256'] == hashlib.sha256(report.read_bytes()).hexdigest()
    assert cli(*command)[1]['errors'][0]['code'] == 'output_exists'
    head_only = review_bytes(b'a,b\n', 'empty.csv', checks=['empty_table', 'duplicate_row']).report
    check = head_only['tables'][0]['result']['data']['checklist']
    assert {row['check']: row['state'] for row in check if row['check'] in {'empty_table', 'duplicate_row'}} == {
        'empty_table': 'checked_with_findings', 'duplicate_row': 'not_applicable'}


def test_json_boundary_and_failure_receipts(tmp_path):
    source = tmp_path / 'PRIVATE-NAME.csv'
    source.write_bytes(b'x,y\n1,private-fixture-value\n')
    options = tmp_path / 'options.json'
    dest = tmp_path / 'saved.json'
    for payload in ['{"ranges":{},"ranges":{}}', '{"private":NaN}', '{"private":1e999}']:
        options.write_text(payload)
        process, reply = cli('review', source, '--snapshot', dest, '--options', options)
        assert process.returncode == 1 and reply['errors'][0]['code'] == 'invalid_json'
        assert 'private' not in process.stdout and 'PRIVATE' not in process.stdout
        assert not process.stderr and not dest.exists()
    process, reply = cli('review', source)
    assert process.returncode == 1 and reply['errors'][0]['code'] == 'invalid_arguments'
    corrupt = tmp_path / 'corrupt.xlsx'
    corrupt.write_bytes(b'text,not a workbook\n')
    process, reply = cli('review', corrupt, '--snapshot', dest)
    assert process.returncode == 1 and reply['coverage']['status'] == 'not_completed'
    assert not dest.exists()
    assert source.read_bytes() == b'x,y\n1,private-fixture-value\n'
    from pandas import DataFrame
    for invalid in [{1: 'text'}, {'object': object()}, {'set': {1, 2}}, {'frame': DataFrame([[1]])}]:
        with pytest.raises(ValueError, match='JSON data model'):
            dumps(invalid)


def test_report_bytes_stable_across_hash_seeds(tmp_path):
    source = tmp_path / 'stable.csv'
    source.write_bytes(b'x,y,z\n,1,\n,2,\n')
    options = tmp_path / 'options.json'
    options.write_text(json.dumps({'required_fields': ['b', 'a'], 'values': ['d', 'c']}))
    results = []
    for seed in ('123', '456'):
        snapshot = tmp_path / f'{seed}.json'
        result, reply = cli('review', source, '--snapshot', snapshot, '--options', options,
                            '--checks', 'empty_column', 'required_field_absent', 'invalid_value_mapping',
                            env={**os.environ, 'PYTHONHASHSEED': seed})
        assert result.returncode == 0 and reply['status'] == 'partial'
        results.append(snapshot.read_bytes())
    assert results[0] == results[1]


def test_compare_saved_regions_and_three_distinct_entries(tmp_path):
    source = workbook_bytes()
    snapshots = [review_bytes(source, 'first.xlsx', regions=REGIONS, checks=['empty_table']),
                 review_bytes(source, 'second.xlsx', regions=REGIONS, checks=['empty_table'])]
    left = {'file_index': 0, 'sheet': 'Measurements', 'region_id': 'left'}
    right = {'file_index': 1, 'sheet': 'Measurements', 'region_id': 'left'}
    bounds = {'units': ['1', '1']}
    rect = {'rows': [36, 37, 38], 'columns': ['m1', 'm2']}
    entries = [
        {**bounds, 'method': 'sample_alignment', 'left': {**left, 'columns': ['value'], 'key_columns': ['sample']},
         'right': {**right, 'columns': ['value'], 'key_columns': ['sample']}},
        {**bounds, 'method': 'finite_rectangle', 'left': {**left, **rect}, 'right': {**right, **rect}},
        {**bounds, 'method': 'window_search', 'left': {**left, 'rectangles': [rect]},
         'right': {**right, 'rectangles': [rect]}, 'window': {'rows': 3, 'columns': 2}},
    ]
    plan = {'schema': 'audit-plan/1', 'mode': 'research_integrity', 'comparisons': entries,
            'cross_file_methods': ['exact_column']}
    result = compare_snapshots(snapshots, plan)
    keyed, finite, search = result['comparisons']
    assert keyed['coverage']['left_nonunique_or_empty'] == 2 and keyed['coverage']['matched_unique_keys'] == 5
    assert keyed['status'] == 'partial' and keyed['coverage']['missing_cells'] == 2
    assert finite['evidence']['finite_rectangle_match'] and finite['locations'][0]['cells'][0] == ['H36', 'I36']
    assert search['status'] == 'complete' and search['evidence']['matches']
    assert {item['region_id'] for item in result['integrity']['sources']} == {'left', 'right'}
    paths = [tmp_path / f'{index}.json' for index in range(2)]
    for path, snapshot in zip(paths, snapshots):
        path.write_text(dumps(snapshot.to_dict()))
    plan_path = tmp_path / 'plan.json'
    plan_path.write_text(dumps(plan))
    output = tmp_path / 'comparisons.json'
    process, reply = cli('compare', *paths, '--plan', plan_path, '--output', output)
    assert process.returncode == 0 and reply['status'] == 'partial'
    assert loads(output.read_text()) == result
