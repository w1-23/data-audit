"""Semantic cases for all eight migrated numeric reuse methods."""
import pytest

from citrus_audit import compare_snapshots, review_bytes
from citrus_audit.integrity import METHODS

VALUES = [[1, 9], [4, 2], [2, 8], [8, 13], [5, 3], [4, 2]]
REORDERED = [VALUES[index] for index in [3, 2, 1, 5, 0, 4]]


def source(name, rows):
    data = ('x,y\n' + ''.join(','.join(map(str, row)) + '\n' for row in rows)).encode()
    return review_bytes(data, name + '.csv', checks=['empty_table'])


def run(method, left, right):
    return compare_snapshots([source('left', left), source('right', right)],
                             {'schema': 'audit-plan/1', 'mode': 'research_integrity', 'cross_file_methods': [method]})


@pytest.mark.parametrize('method', METHODS)
def test_migrated_integrity_method(method):
    left = VALUES
    right = VALUES
    if method.startswith('reordered'):
        right = REORDERED
    elif method in {'constant_offset', 'constant_ratio', 'affine'}:
        scale, offset = {'constant_offset': (1, 4), 'constant_ratio': (3, 0), 'affine': (3, 4)}[method]
        right = [[value * scale + offset for value in row] for row in VALUES]
    elif method == 'shared_decimal_tail':
        tail = ['1234', '2345', '3456', '4567', '5678', '6789']
        left = [[f'{index}.{suffix}', 'NA'] for index, suffix in enumerate(tail, start=1)]
        right = [[f'{index**2}.{suffix}', 'NA'] for index, suffix in enumerate(tail, start=2)]
    result = run(method, left, right)['integrity']
    receipt = result['coverage']['methods'][method]
    assert receipt['status'] == 'executed'
    assert receipt['matched'] > 0
    assert {case['method'] for case in result['findings']} == {method}
    assert all(case['conclusion'] == 'numeric_relationship_only' for case in result['findings'])
    matched = next(case for case in result['findings'] if case['left']['columns'][0]['index'] == 1
                   and case['right']['columns'][0]['index'] == 1)
    assert matched['left']['rows'][0] == 2
    if method.startswith('reordered'):
        assert matched['mapping_ambiguous_due_to_ties']
        assert matched['right']['rows'][0] == 6
        assert matched['left']['rows'] != matched['right']['rows']
    if method == 'shared_decimal_tail':
        assert matched['relationship']['significance_claimed'] is False
        assert matched['relationship']['tail_integers'] == [234, 345, 456, 567, 678, 789]
    assert result == run(method, left, right)['integrity']
    unrelated = [[100 + index ** 2, None] for index in range(len(VALUES))]
    assert not run(method, left, unrelated)['integrity']['findings']
