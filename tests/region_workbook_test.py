import hashlib
import io
import unittest

from openpyxl import Workbook, load_workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.comments import Comment
from openpyxl.worksheet.formula import ArrayFormula, DataTableFormula

from citrus_audit.inspection import discover_regions
from citrus_audit.readers import normalize_regions, read_region
from citrus_audit.workbook import export_reviewed_workbook


def _source():
    wb = Workbook()
    ws = wb.active
    ws.title = 'S'
    ws.append(['x', 'y', 'note', 'v', 'w', 'outside'])
    for values in ((1, 2, 'n', 10, 20, 99), (1, 2, 'n', 10, 20, 99),
                   (2, 4, 'n', 30, 40, 99), (2, 4, 'n', 50, 60, 99)):
        ws.append(values)
    ws['A3'].comment = Comment('original', 'owner')
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


class RegionWorkbookTest(unittest.TestCase):
    def test_export_preserves_rich_runs_and_formula_cells(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(['label', 'array', 'table'])
        sheet['A2'] = CellRichText('sample ', TextBlock(InlineFont(b=True), 'one'))
        sheet['B2'] = ArrayFormula('B2:B3', '=D2:D3*2')
        sheet['C2'] = DataTableFormula('C2:C3', r1='D2', dt2D=True)
        stream = io.BytesIO()
        workbook.save(stream)
        data = stream.getvalue()
        target = {'sheet_name': sheet.title, 'header_row': 1,
                  'column_numbers': [1, 2, 3], 'row_numbers': [2, 3],
                  'findings': [{'check': 'row_annotation', 'row': 2,
                                'column_index': 1, 'detail': 'review'}]}

        blob, stats = export_reviewed_workbook(data, targets=[target],
                                               source_sha256=hashlib.sha256(data).hexdigest(),
                                               issue_list=True)

        exported = load_workbook(io.BytesIO(blob), rich_text=True)
        try:
            copied = exported[sheet.title]
            self.assertEqual(copied['A2'].value, sheet['A2'].value)
            self.assertTrue(copied['A2'].value[1].font.b)
            self.assertEqual(copied['B2'].value.text, sheet['B2'].value.text)
            self.assertEqual(dict(copied['B2'].value), dict(sheet['B2'].value))
            self.assertEqual(dict(copied['C2'].value), dict(sheet['C2'].value))
            self.assertIsNotNone(copied['A2'].comment)
            self.assertEqual(exported['问题清单']['D2'].value, 'A2')
        finally:
            exported.close()
        self.assertTrue(stats['data_region_unchanged'])

    def test_small_sheet_scan_includes_second_block_beyond_preview(self):
        workbook = Workbook()
        sheet = workbook.active
        for header, last_row in ((1, 17), (27, 50)):
            for column in range(1, 27):
                sheet.cell(header, column, f'column {column}')
                for row in range(header + 1, last_row + 1):
                    sheet.cell(row, column, row + column)
        stream = io.BytesIO()
        workbook.save(stream)

        result = discover_regions(stream.getvalue(), 'blocks.xlsx', header_rows=[1, 27])

        self.assertEqual([(region['bounds'], region['header_rows']) for region in result['regions']],
                         [('A1:Z17', [1]), ('A27:Z50', [27])])
        self.assertEqual(result['detection']['scan_rows'], 50)
        self.assertEqual(result['detection']['scan_columns'], 26)
        self.assertTrue(result['detection']['scan_complete'])
        self.assertNotIn('scan_window_limited', result['limitations'])

    def test_sparse_data_beyond_budget_marks_layout_incomplete(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet['A1'] = 'within range'
        sheet['A3000'] = 'beyond scan'
        stream = io.BytesIO()
        workbook.save(stream)

        result = discover_regions(stream.getvalue(), 'sparse.xlsx')

        self.assertEqual([region['bounds'] for region in result['regions']], ['A1:A1'])
        self.assertFalse(result['detection']['scan_complete'])
        self.assertEqual(result['detection']['scan_clipped_edges'], ['bottom'])
        self.assertIn('scan_window_limited', result['limitations'])

    def test_region_identity_is_scoped_by_file_and_sheet(self):
        regions = normalize_regions([
            {'file_index': 0, 'sheet': 'S', 'bounds': 'D1:E5', 'header_rows': [1], 'region_id': 'r1'},
            {'file_index': 1, 'sheet': 'S', 'bounds': 'D1:E5', 'header_rows': [1], 'region_id': 'r1'},
        ])
        self.assertEqual(len(regions), 2)
        with self.assertRaisesRegex(ValueError, '重叠'):
            normalize_regions([
                {'file_index': 0, 'sheet': 'S', 'bounds': 'D1:E4', 'header_rows': [1]},
                {'file_index': 0, 'sheet': 'S', 'bounds': 'E2:F5', 'header_rows': [2]},
            ])

    def test_sparse_single_column_explicit_header(self):
        wb = Workbook()
        ws = wb.active
        ws.title = 'One'
        ws['A1'] = 'title'
        ws['A2'] = 'value'
        ws['A3'] = 7
        out = io.BytesIO()
        wb.save(out)
        table = read_region(out.getvalue(), 'one.xlsx', bounds='A1:A3',
                            header_rows=[2], sheet_name='One')
        self.assertEqual(table.columns, ('value',))
        self.assertEqual(table.values, ((7,),))

    def test_row_finding_stays_inside_reviewed_columns_and_issue_list_keeps_audit_fields(self):
        data = _source()
        digest = hashlib.sha256(data).hexdigest()
        target = {'sheet_name': 'S', 'sheet': 'S', 'header_row': 1, 'header_rows': [1],
                  'column_numbers': [4, 5], 'row_numbers': [2, 3, 4, 5],
                  'columns': ['v', 'w'], 'region_id': 'r1',
                  'findings': [{'check': 'duplicate_row', 'severity': 'review', 'detail': 'dup',
                                'row': 3, 'evidence': {'cells': [
                                    {'row': 3, 'column_index': 4},
                                    {'row': 3, 'column_index': 5}]}}]}
        blob, stats = export_reviewed_workbook(data, targets=[target], source_sha256=digest,
                                               original=True, issue_list=True)
        wb = load_workbook(io.BytesIO(blob), rich_text=True)
        self.assertEqual(wb.sheetnames, ['S', '问题清单'])
        self.assertTrue(wb['S']['D3'].comment)
        self.assertTrue(wb['S']['E3'].comment)
        self.assertEqual(wb['S']['A3'].comment.text, 'original')
        headers = [cell.value for cell in wb['问题清单'][1]]
        self.assertEqual(headers[:3], ['序号', '工作表', '区域'])
        self.assertIn('建议核查动作', headers)
        self.assertIn('计算/定位证据（长内容需读完整结果）', headers)
        row = [cell.value for cell in wb['问题清单'][2]]
        self.assertEqual(row[2], 'r1')
        self.assertEqual(row[3], 'D3,E3')
        self.assertEqual(stats["marked_locations"], 1)

    def test_evidence_outside_reviewed_columns_is_rejected(self):
        data = _source()
        digest = hashlib.sha256(data).hexdigest()
        target = {'sheet_name': 'S', 'sheet': 'S', 'header_row': 1, 'column_numbers': [4, 5],
                  'row_numbers': [2, 3, 4, 5], 'region_id': 'r1',
                  'findings': [{'check': 'duplicate_row', 'row': 3,
                                'evidence': {'cells': [{'row': 3, 'column_index': 6}]}}]}
        with self.assertRaisesRegex(ValueError, 'outside reviewed'):
            export_reviewed_workbook(data, targets=[target], source_sha256=digest, original=True)


if __name__ == '__main__':
    unittest.main()
