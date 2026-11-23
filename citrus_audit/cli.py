"""Inspect, review and compare explicit files. Export directly from a saved review."""
from __future__ import annotations

import argparse
from collections import Counter
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from . import AuditSnapshot, compare_snapshots, discover_regions, export_workbook, review_bytes, rule_manifest
from .data_audit_rules import BASELINE_RULES
from .integrity import METHODS
from .plan import LEADS, QUALITY_DEFAULT
from .reporting import compact_report, dumps, loads
from .row_patterns import ROW_RULES

DEFAULT_CHECKS = (*QUALITY_DEFAULT, 'multi_header_suspected')
MAX_INPUT_BYTES = 30 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024
MAX_BATCH_SNAPSHOT_BYTES = 512 * 1024 * 1024
MAX_CONFIG_BYTES = 1024 * 1024


class UserError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class JsonParser(argparse.ArgumentParser):
    def error(self, _):
        raise UserError('invalid_arguments', 'Check the command syntax with --help.')


def _bounded_read(path, limit):
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise UserError('size_limit', 'The input exceeds the operation byte budget. Select a smaller scope.')
    return data


def _json(path, limit=MAX_CONFIG_BYTES):
    try:
        result = loads(_bounded_read(path, limit).decode('utf-8'))
    except UserError:
        raise
    except (ValueError, UnicodeError, RecursionError) as error:
        raise UserError('invalid_json', 'Expected a UTF-8 JSON object with unique keys and finite numbers.') from error
    if not isinstance(result, dict):
        raise UserError('expected_object', 'Expected a JSON object.')
    return result


def _write(path, data):
    with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
            os.link(temporary, path)
        finally:
            temporary.unlink()


def _write_json(path, value, *, max_bytes=MAX_SNAPSHOT_BYTES):
    data = dumps(value).encode('ascii')
    if len(data) > max_bytes:
        raise UserError('output_size_limit', 'The output exceeds the portable JSON budget. Select a smaller scope.')
    _write(path, data)


def _snapshot(path):
    try:
        return AuditSnapshot.from_dict(_json(path, MAX_SNAPSHOT_BYTES))
    except UserError:
        raise
    except (ValueError, KeyError, AttributeError, TypeError) as error:
        raise UserError('invalid_snapshot', 'Snapshot structure, source hash or report digest could not be verified.') from error


def _parser():
    parser = JsonParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('catalog')
    inspect = sub.add_parser('inspect')
    inspect.add_argument('input', type=Path)
    inspect.add_argument('--output', type=Path, required=True)
    inspect.add_argument('--sheet', default='')
    review = sub.add_parser('review')
    review.add_argument('input', type=Path)
    review.add_argument('--snapshot', type=Path, required=True)
    review.add_argument('--checks', nargs='+', default=list(DEFAULT_CHECKS))
    review.add_argument('--options', type=Path, help='JSON rule parameters, header rows or explicit regions.')
    review.add_argument('--sheet', default='')
    review.add_argument('--header-row', type=int, default=1)
    report = sub.add_parser('report')
    report.add_argument('snapshot', type=Path)
    report.add_argument('--output', type=Path, required=True)
    export = sub.add_parser('export')
    export.add_argument('snapshot', type=Path)
    export.add_argument('--output', type=Path, required=True)
    for flag in ('explain-visible', 'issue-list', 'overview', 'rules'):
        export.add_argument('--' + flag, action='store_true')
    compare = sub.add_parser('compare')
    compare.add_argument('snapshots', nargs='+', type=Path)
    compare.add_argument('--plan', type=Path, required=True)
    compare.add_argument('--integrity-options', type=Path)
    compare.add_argument('--output', type=Path, required=True)
    return parser


def _execute(args):
    if args.action == 'catalog':
        return {'status': 'ok', 'rules': rule_manifest(BASELINE_RULES), 'row_extensions': rule_manifest(ROW_RULES),
                'integrity_methods': list(METHODS), 'compare_entries': sorted(LEADS)}
    if args.action == 'inspect':
        layout = discover_regions(_bounded_read(args.input, MAX_INPUT_BYTES), args.input.name, sheet_name=args.sheet)
        _write_json(args.output, layout)
        status = 'partial' if layout['regions_truncated'] or not layout['detection']['scan_complete'] else 'complete'
        return {'status': status, 'source_sha256': layout['source_sha256'], 'region_count': layout['region_count']}
    if args.action == 'review':
        options = _json(args.options) if args.options else {}
        if set(options) & {'checks', 'sheet_name', 'header_row', 'data', 'name'}:
            raise UserError('duplicate_options', 'Specify each input, sheet, header or check option once.')
        snapshot = review_bytes(_bounded_read(args.input, MAX_INPUT_BYTES), args.input.name, checks=args.checks,
                                sheet_name=args.sheet, header_row=args.header_row, **options)
        payload = snapshot.to_dict()
        _write_json(args.snapshot, payload)
        return {**compact_report(payload['report']), 'snapshot_written': True, 'report_sha256': payload['report_sha256']}
    if args.action == 'compare':
        if not 1 <= len(args.snapshots) <= 20 or sum(path.stat().st_size for path in args.snapshots) > MAX_BATCH_SNAPSHOT_BYTES:
            raise UserError('batch_size_limit', 'Supply at most 20 snapshots and 512 MiB total.')
        report = compare_snapshots([_snapshot(path) for path in args.snapshots], _json(args.plan),
                                   integrity_options=_json(args.integrity_options) if args.integrity_options else None)
        _write_json(args.output, report)
        integrity = report['integrity'] or {}
        return {'status': report['status'], 'comparison_states': dict(Counter(item['status'] for item in report['comparisons'])),
                'integrity_coverage': integrity.get('coverage_status'), 'integrity_findings': len(integrity.get('findings', [])),
                'errors': len(report['errors']), 'source_issues': len(report['source_issues']), 'report_written': True}
    snapshot = _snapshot(args.snapshot)
    if args.action == 'report':
        _write_json(args.output, snapshot.report)
        return {'status': snapshot.report['status'], 'source_sha256': snapshot.source_sha256,
                'report_sha256': snapshot.report_sha256, 'report_written': True}
    expected = '.xlsm' if snapshot.source_name.lower().endswith('.xlsm') else '.xlsx'
    if args.output.suffix.lower() != expected:
        raise UserError('output_extension_mismatch', f'Use the {expected} output extension.')
    if not snapshot.report['tables']:
        raise UserError('no_reviewable_tables', 'The saved review has no parsed table to export.')
    content, receipt = export_workbook(snapshot, explain_visible=args.explain_visible, issue_list=args.issue_list,
                                       overview=args.overview, rules=args.rules)
    _write(args.output, content)
    return {key: receipt[key] for key in ('source_sha256', 'export_sha256', 'data_region_unchanged',
                                         'marked_cells', 'scan_reused', 'source_representation')} | {
        'status': 'ok', 'output_written': True}


def main(argv=None):
    try:
        result = _execute(_parser().parse_args(argv))
    except UserError as error:
        result = _error(error.code, str(error))
    except FileExistsError:
        result = _error('output_exists', 'Choose a new output path. Existing files were preserved.')
    except FileNotFoundError:
        result = _error('path_missing', 'Check the input path or the parent directory of the output.')
    except ImportError:
        result = _error('dependency_unavailable', 'Install the declared package dependencies.')
    except (OSError, BadZipFile, ParseError, ValueError, TypeError, AttributeError, KeyError, IndexError, ArithmeticError) as error:
        result = _error('operation_failed', f'Audit could not complete ({type(error).__name__}). Check file format, scope and rule parameters.')
    print(dumps(result))
    return int(result['status'] == 'error')


def _error(code, message):
    return {'status': 'error', 'errors': [{'code': code, 'message': message}], 'coverage': {'status': 'not_completed'}}


if __name__ == '__main__':
    sys.exit(main())
