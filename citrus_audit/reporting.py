"""Stable JSON output and bounded summaries for review results."""
from collections import Counter
import json
import math

from .methods import COVERAGE_CHECKS


def _json_value(value):
    if type(value) in (str, int, bool, type(None)):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, (tuple, list)):
        return [_json_value(entry) for entry in value]
    if isinstance(value, dict) and all(type(key) is str for key in value):
        return {key: _json_value(entry) for key, entry in value.items()}
    raise ValueError('Value is outside the JSON data model')


def dumps(value):
    return json.dumps(_json_value(value), allow_nan=False, sort_keys=True, ensure_ascii=True, separators=(',', ':'))


def loads(text):
    def reject_constant(_):
        raise ValueError('JSON requires finite numbers')

    def object_pairs(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError('JSON object keys must be unique')
        return result

    return _json_value(json.loads(text, parse_constant=reject_constant, object_pairs_hook=object_pairs))


def scope_checklist(execution):
    requested = set(execution['requested'])
    rows = []
    for check, receipt in sorted(execution['rules'].items()):
        status = receipt['status']
        evidence = receipt['findings_returned']
        if check not in requested:
            state = 'not_executed'
        elif status == 'failed':
            state = 'failed'
        elif status == 'invalid_input':
            state = 'invalid_input'
        elif status == 'truncated' or (receipt['tested'] and receipt['skipped']):
            state = 'partial_coverage'
        elif status == 'executed' and evidence and check in COVERAGE_CHECKS:
            state = 'coverage_notice'
        elif status == 'executed':
            state = 'checked_with_findings' if evidence else 'checked_no_findings'
        else:
            state = 'not_applicable'
        rows.append({'check': check, 'state': state, 'tested': receipt['tested'], 'skipped': receipt['skipped'],
                     'findings_returned': evidence, 'reasons': receipt['reasons']})
    return rows


def compact_report(report):
    """Fixed-size first reply. Raw values and complete coverage stay in the saved report."""
    tables = report['tables']
    checklist = Counter()
    findings = Counter()
    diagnostics = 0
    omitted = 0
    data_rows = 0
    for entry in tables:
        data = entry['result']['data']
        data_rows += len(entry['table']['row_numbers'])
        checklist.update(item['state'] for item in (data.get('checklist') or scope_checklist(data['rule_execution'])))
        omitted += data['rule_execution']['findings_omitted']
        for item in data['findings']:
            if item.get('coverage_only') or item['check'] in COVERAGE_CHECKS:
                diagnostics += 1
            else:
                findings[item['check']] += 1
    return {'version': report['version'], 'status': report['status'], 'source_sha256': report['source_sha256'],
            'tables': len(tables), 'rows_in_reviewed_scopes': data_rows, 'errors': len(report['errors']),
            'findings_returned': sum(findings.values()), 'findings_omitted': omitted,
            'coverage_notices': diagnostics, 'checklist_counts': dict(checklist),
            'leading_checks': [{'check': check, 'count': count}
                               for check, count in sorted(findings.items(), key=lambda item: (-item[1], item[0]))[:8]],
            'interpretation': 'Review leads for checked scopes only.'}
