"""Review immutable source bytes and export from the saved review."""
from __future__ import annotations

import base64
from contextlib import closing
from dataclasses import dataclass
from functools import cached_property
import hashlib
from pathlib import Path

from .envelope import TableEnvelope
from .methods import COVERAGE_CHECKS, audit_frame
from .readers import open_xlsx_readonly, read_regions, read_tables
from .reporting import compact_report, dumps, loads, scope_checklist

SCHEMA_VERSION = '2.0.0'


def audit_table(table: TableEnvelope, *, checks=None, **options):
    if not isinstance(table, TableEnvelope):
        raise ValueError('An explicit TableEnvelope is required')
    if set(options) & {'row_offset', 'source_coverage', 'execution_receipt'}:
        raise ValueError('Envelope owns source coordinates and coverage')
    execution = {}
    findings, _ = audit_frame(table.to_frame(), row_offset=0, source_coverage=table.coverage,
                              execution_receipt=execution, checks=checks, **options)
    for item in findings:
        local = item.get('column_index')
        if type(local) is int:
            item['column_index'] = table.column_numbers[local-1]
        if item.get('evidence', {}).get('cells'):
            item['scope'] = 'cell'
        else:
            item['scope'] = 'cell' if item.get('row') else 'column' if item.get('column_index') else 'table'
        item['coverage_only'] = item['check'] in COVERAGE_CHECKS
        item['sheet'] = table.sheet
        if table.region_id:
            item['region_id'] = table.region_id
    checklist = scope_checklist(execution)
    partial = table.coverage.get('truncated') or any(
        item['state'] in {'invalid_input', 'failed', 'partial_coverage'} for item in checklist)
    result = {'status': 'partial' if partial else 'complete',
              'data': {'findings': findings, 'rule_execution': execution, 'checklist': checklist},
              'errors': [], 'coverage': {'requested': execution['requested'], 'actual': execution['actual'],
                  'gap': execution['gap'], 'source': table.coverage, 'source_sha256': table.source_sha256,
                  'lexical_coverage': table.lexical_coverage}, 'version': SCHEMA_VERSION}
    return loads(dumps(result))


@dataclass(frozen=True)
class AuditSnapshot:
    source_bytes: bytes
    source_name: str
    report_json: str
    source_sha256: str

    def __post_init__(self):
        if not isinstance(self.source_bytes, bytes) or hashlib.sha256(self.source_bytes).hexdigest() != self.source_sha256:
            raise ValueError('Reviewed snapshot source hash mismatch')
        report = self.report
        if (report.get('version') != SCHEMA_VERSION or report.get('source_sha256') != self.source_sha256
                or report.get('source_name') != self.source_name or not isinstance(report.get('tables'), list)):
            raise ValueError('Reviewed report source identity mismatch')
        for entry in report['tables']:
            envelope = TableEnvelope.from_dict(entry['table'])
            if envelope.source_sha256 != self.source_sha256 or envelope.source_name != self.source_name:
                raise ValueError('Reviewed envelope source identity mismatch')

    @property
    def report(self):
        return loads(self.report_json)

    @cached_property
    def report_sha256(self):
        return hashlib.sha256(dumps(self.report).encode('ascii')).hexdigest()

    def compact(self):
        return compact_report(self.report)

    def to_dict(self):
        return {'version': SCHEMA_VERSION, 'source_name': self.source_name, 'source_sha256': self.source_sha256,
                'source_base64': base64.b64encode(self.source_bytes).decode('ascii'), 'report': self.report,
                'report_sha256': self.report_sha256}

    @classmethod
    def from_dict(cls, value):
        if value.get('version') != SCHEMA_VERSION:
            raise ValueError('Unsupported snapshot version')
        data = base64.b64decode(value['source_base64'], validate=True)
        snapshot = cls(data, value['source_name'], dumps(value['report']), value['source_sha256'])
        if 'report_sha256' in value and value['report_sha256'] != snapshot.report_sha256:
            raise ValueError('Reviewed report hash mismatch')
        return snapshot


def _formula_coordinates(workbook, table):
    """Index formulas independently of the saved cached values used for calculation."""
    if not table.row_numbers or not table.column_numbers:
        return []
    worksheet = workbook[table.sheet]
    worksheet.reset_dimensions()
    cached = dict(zip(table.row_numbers, table.values))
    columns = {column: index for index, column in enumerate(table.column_numbers)}
    formulas = []
    for row in worksheet.iter_rows(min_row=min(cached), max_row=max(cached),
                                   min_col=min(columns), max_col=max(columns)):
        for cell in row:
            if cell.data_type == 'f' and cell.row in cached and cell.column in columns:
                value = cached[cell.row][columns[cell.column]]
                expression = getattr(cell.value, 'text', cell.value)
                formulas.append({'row': cell.row, 'column_index': cell.column,
                                 'formula': expression if isinstance(expression, str) else None,
                                 'cache_status': 'absent_or_blank' if value is None else 'present_unverified'})
    return formulas


def review_bytes(data: bytes, name: str, *, checks=None, sheet_name='', header_row=1, header_rows=None,
                 regions=None, **options):
    if regions is None:
        tables, errors = read_tables(data, name, sheet_name=sheet_name, header_row=header_row, header_rows=header_rows)
    else:
        if header_row != 1 or header_rows is not None:
            raise ValueError('Explicit regions carry their own absolute header rows')
        tables = read_regions(data, name, regions=regions, sheet_name=sheet_name)
        errors = []
    entries = [{'table': table.to_dict(), 'result': audit_table(table, checks=checks, **options)} for table in tables]
    if entries and Path(name).suffix.lower() in {'.xlsx', '.xlsm'}:
        with closing(open_xlsx_readonly(data, name, data_only=False)) as workbook:
            for table, entry in zip(tables, entries):
                entry['formulas'] = _formula_coordinates(workbook, table)
    report = {'version': SCHEMA_VERSION, 'source_sha256': hashlib.sha256(data).hexdigest(), 'source_name': name,
              'status': 'error' if not entries else 'partial' if errors or any(
                  entry['result']['status'] == 'partial' for entry in entries) else 'complete',
              'tables': entries, 'errors': errors,
              'claims': ['Review covers the recorded scopes. Numerical relationships and rule violations are leads for review.']}
    if not entries and not errors:
        errors.append({'code': 'no_reviewable_tables', 'message': 'No table was parsed.'})
    return AuditSnapshot(data, name, dumps(report), report['source_sha256'])


def export_workbook(snapshot: AuditSnapshot, *, presentation: str = 'annotated',
                    explain_visible: bool = False, issue_list: bool = False,
                    overview: bool = False, rules: bool = False):
    """Render saved findings without rescanning the source.

    ``annotated`` adds one visible review-result sheet and no debug appendices.
    ``legacy`` keeps the pre-review export surface for old callers.
    """
    from .workbook import export_snapshot

    return export_snapshot(snapshot, presentation=presentation,
                           explain_visible=explain_visible, issue_list=issue_list,
                           overview=overview, rules=rules)
