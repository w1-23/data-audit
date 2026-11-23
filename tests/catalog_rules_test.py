"""One independently executed synthetic trigger for each migrated table rule."""
import hashlib

import pytest

from citrus_audit import TableEnvelope, audit_table, review_bytes
from citrus_audit.data_audit_rules import BASELINE_RULES
from citrus_audit.methods import COVERAGE_CHECKS
from citrus_audit.reporting import dumps


def run(check, columns, rows, **options):
    table = TableEnvelope(columns=tuple(columns), values=tuple(map(tuple, rows)),
                          row_numbers=tuple(range(31, 31 + len(rows))),
                          column_numbers=tuple(range(21, 21 + len(columns))),
                          header_row=30, source_sha256=hashlib.sha256(b'synthetic').hexdigest(),
                          source_name='synthetic.xlsx', sheet='S')
    return audit_table(table, checks=options.pop('checks', [check]), **options)['data']


BASE = [1, 4, 2, 8, 5]
PATTERN_OPTIONS = {'columns': ['x'], 'order_confirmed': True, 'window': 3}
SUMMARY_OPTIONS = {'integer_data_confirmed': True, 'mapping': {'mean': 'mean', 'n': 'n', 'sd': 'sd'}}
STR = {'factors': ['g'], 'unit_column': 'unit', 'expected_cells': [{'g': 'A', 'expected_units': 2}]}

CASES = {
    'duplicate_composite_key': (['a', 'b'], [[1, 2], [1, 2], [1, None]], {'composite_keys': [['a', 'b']]}),
    'raw_numeric_tail': (['x'], [[f'{index}.0100'] for index in range(12)], {}),
    'adjacent_equal_differences': (['x'], [[1], [2], [3]], {}),
    'sliding_equal_differences': (['x'], [[1], [2], [3]], {}),
    'repeated_sequences': (['x'], [[1], [4], [2], [None], [1], [4], [2]], {}),
    'repeated_blocks': (['x', 'y'], [[1, 2], [4, 7], [2, 9], [1, 2], [4, 7], [2, 9]], {}),
    'expected_experiment_structure': (['g', 'unit'], [['A', 'one'], ['A', 'one']], {'expected_structure': STR}),
    'required_field_absent': (['x'], [[1]], {'required_fields': ['absent']}),
    'integer_summary_not_checked': (['mean', 'n', 'sd'], [[1.2, 2, 0.3]], SUMMARY_OPTIONS),
    'empty_table': (['x'], [], {}),
    'empty_column': (['x', 'y'], [[None, 2]], {}),
    'missing_values': (['x'], [[None], [2]], {}),
    'missing_cell': (['x'], [[None], [2]], {'required_fields': ['x']}),
    'constant_field': (['x'], [[1], [1]], {}),
    'mixed_numeric_text': (['x'], [[1], ['text']], {}),
    'non_numeric_cell': (['x'], [['text']], {'values': ['x']}),
    'iqr_outlier': (['g', 'x'], [['A', value] for value in [1, 2, 3, 4, 5, 6, 7, 100]], {'groups': ['g'], 'values': ['x']}),
    'iqr_insufficient': (['g', 'x'], [['A', 1], ['A', 2]], {'groups': ['g'], 'values': ['x']}),
    'out_of_range': (['x'], [[9]], {'ranges': {'x': {'min': 0, 'max': 8}}}),
    'logic_conflict': (['x', 'y'], [[2, 1]], {'relations': [{'left': 'x', 'right': 'y', 'op': '<='}]}),
    'duplicate_key': (['x', 'y'], [['one', 2], ['one', 3]], {'key_columns': ['x']}),
    'duplicate_row': (['x', 'y'], [[1, 2], [1, 2]], {}),
    'multi_header_suspected': (['x', 'y'], [['units', 'units'], *[[v, v] for v in BASE]], {}),
    'optional_column': (['x', 'notes'], [[1, 'explanation']], {}),
    'identical_columns': (['x', 'y'], [[v, v] for v in BASE], {}),
    'constant_offset_columns': (['x', 'y'], [[v, v + 4] for v in BASE], {}),
    'constant_ratio_columns': (['x', 'y'], [[v, v * 3] for v in BASE], {}),
    'exact_linear_columns': (['x', 'y'], [[v, v * 3 + 4] for v in BASE], {}),
    'decimal_ending_concentration': (['x'], [[f'{index}.123400'] for index in range(12)], {}),
    'unequal_group_size': (['g'], [['A'], ['A'], ['B']], {'groups': ['g']}),
    'unequal_replicates': (['g', 'rep'], [['A', 1], ['A', 2], ['B', 1]], {'groups': ['g'], 'mapping': {'replicate': 'rep'}}),
    'integer_mean_inconsistent': (['mean', 'n', 'sd'], [['1.24', 2, '0.00']], SUMMARY_OPTIONS),
    'integer_summary_inconsistent': (['mean', 'n', 'sd'], [['1.00', 3, '0.30']], {**SUMMARY_OPTIONS, 'sd_kind': 'sample'}),
    'design_checks_skipped': (['x'], [[1]], {}),
    'invalid_group_mapping': (['x'], [[1]], {'groups': ['absent']}),
    'invalid_value_mapping': (['x'], [[1]], {'values': ['absent']}),
    'grim_sd_skipped': (['mean', 'n', 'sd'], [['1.00', 3, '0.11']], SUMMARY_OPTIONS),
    'findings_truncated': (['a', 'b', 'c'], [[1, 2, 3]] + [[None, None, None]] * 6667,
                           {'checks': ['missing_cell', 'findings_truncated']}),
}


@pytest.mark.parametrize('check', BASELINE_RULES)
def test_migrated_rule(check):
    columns, rows, options = CASES[check]
    options = dict(options)
    if check in {'raw_numeric_tail', 'adjacent_equal_differences', 'sliding_equal_differences', 'repeated_sequences', 'repeated_blocks'}:
        options.update(values=columns, pattern_checks={**PATTERN_OPTIONS, 'columns': columns, check: True})
    if check == 'raw_numeric_tail':
        source = ('x\n' + ''.join(f'{row[0]}\n' for row in rows)).encode('ascii')
        data = review_bytes(source, 'tokens.csv', checks=[check], **options).report['tables'][0]['result']['data']
    else:
        data = run(check, columns, rows, **options)
    receipt = data['rule_execution']['rules'][check]
    findings = [item for item in data['findings'] if item['check'] == check]
    assert receipt['tested'] > 0 or receipt['status'] == 'invalid_input'
    assert receipt['findings_returned'] == len(findings) > 0
    assert all(item['coverage_only'] == (check in COVERAGE_CHECKS) for item in findings)
    if check.endswith('_columns'):
        assert [(cell['row'], cell['column_index']) for cell in findings[0]['evidence']['cells']] == [
            (row, column) for row in range(31, 36) for column in (21, 22)]
    if check in {'invalid_group_mapping', 'invalid_value_mapping'}:
        assert receipt['status'] == 'invalid_input'
    if check == 'findings_truncated':
        assert data['rule_execution']['rules']['missing_cell']['status'] == 'truncated'
        assert data['rule_execution']['findings_omitted'] == 1
    assert dumps(data)


@pytest.mark.parametrize(('check', 'right'), [
    ('identical_rows', BASE), ('constant_offset_rows', [v + 4 for v in BASE]),
    ('constant_ratio_rows', [v * 3 for v in BASE]), ('exact_linear_rows', [v * 3 + 4 for v in BASE]),
])
def test_rows_preserve_positions_and_require_declared_units(check, right):
    columns = ['a', 'b', 'skip', 'c', 'd', 'e']
    left = BASE[:2] + [None] + BASE[2:]
    right = right[:2] + [999] + right[2:]
    config = {'same_units_confirmed': True, 'columns': columns}
    result = run(check, columns, [left, right], values=columns, row_relationships=config)
    hit, = result['findings']
    assert hit['severity'] == 'review'
    assert hit['evidence']['direction'] == 'across_columns'
    assert hit['evidence']['cells'] == [{'row': row, 'column_index': column} for row in (31, 32)
                                        for column in (21, 22, 24, 25, 26)]
    skipped = run(check, columns, [left, right], values=columns)
    assert not skipped['findings']
    assert skipped['rule_execution']['rules'][check]['status'] == 'skipped'


def test_across_row_block_and_high_offset_counterexample():
    columns = list('abcdefg')
    options = {'values': columns, 'pattern_checks': {'columns': columns, 'order_confirmed': True,
        'same_units_confirmed': True, 'axis': 'columns', 'window': 3, 'repeated_blocks': True}}
    data = run('repeated_blocks', columns, [[1, 4, 2, None, 1, 4, 2], [2, 7, 9, 99, 2, 7, 9]], **options)
    evidence = data['findings'][0]['evidence']
    assert evidence['shape'] == [2, 3]
    assert evidence['direction'] == 'across_columns'
    assert len(evidence['cells']) == 12
    assert {cell['column_index'] for cell in evidence['cells']} == {21, 22, 23, 25, 26, 27}
    left = [10**18 + value for value in BASE]
    right = [value + 6 for value in left]
    right[-1] += 1
    assert not run('constant_offset_columns', ['x', 'y'], list(zip(left, right)))['findings']
