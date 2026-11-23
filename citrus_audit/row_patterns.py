"""Coordinate-preserving comparisons between rows of comparable measurements."""
from fractions import Fraction
from itertools import combinations, islice
from math import comb

from .integrity import IntegrityConfig, _number, _transform

ROW_RULES = {
    'identical_rows': 'exact_column',
    'constant_offset_rows': 'constant_offset',
    'constant_ratio_rows': 'constant_ratio',
    'exact_linear_rows': 'affine',
}
MAX_ROW_PAIRS = 5_000


def check_rows(frame, *, config, values, groups, mapping, row_offset, selected, record, add):
    checks = [check for check in ROW_RULES if check in selected]
    if not checks:
        return
    if config is not None and not isinstance(config, dict):
        for check in checks:
            record(check, status='invalid_input', reason='row_relationships_must_be_object')
        return
    if not config or config.get('same_units_confirmed') is not True:
        for check in checks:
            record(check, reason='requires_explicit_comparable_measurements_with_same_units')
        return
    columns = config.get('columns', values)
    excluded = set(groups) | {column for role, column in mapping.items()
                              if role in {'id', 'sample_id', 'replicate', 'experimental_unit', 'n'}}
    absolute = config.get('absolute_tolerance', '1e-10')
    relative = config.get('relative_tolerance', '1e-9')
    if (not isinstance(columns, list) or len(columns) < 5
            or any(not isinstance(column, str) or column not in values or column in excluded
                   for column in columns)
            or columns != [column for column in frame.columns if column in columns]
            or any(parsed is None or parsed < 0 for parsed, _ in map(_number, (absolute, relative)))):
        for check in checks:
            record(check, status='invalid_input', reason='requires_5_present_ordered_measurements_and_nonnegative_tolerance')
        return
    tolerance = IntegrityConfig(absolute_tolerance=str(absolute), relative_tolerance=str(relative))
    physical = [int(frame.attrs.get('_column_numbers', {}).get(column, frame.columns.get_loc(column) + 1))
                for column in columns]
    view = frame[columns].iloc[:20_000]
    vectors = [tuple(_number(value)[0] for value in row) for row in view.itertuples(index=False, name=None)]
    rows = [int(row) + row_offset for row in view.index]
    for left, right in islice(combinations(range(len(rows)), 2), MAX_ROW_PAIRS):
        pairs = [(column, first, second) for column, first, second in zip(physical, vectors[left], vectors[right])
                 if first is not None and second is not None]
        if len(pairs) < 5 or Fraction(len(pairs), len(columns)) < Fraction(4, 5):
            for check in checks:
                record(check, skipped=1, reason='insufficient_common_numeric_columns')
            continue
        positions, first, second = zip(*pairs)
        matched = False
        for check in checks:
            method = ROW_RULES[check]
            if matched or (method != 'exact_column' and min(len(set(first)), len(set(second))) < 3):
                record(check, skipped=1, reason='stronger_relation_or_low_distinctness')
                continue
            record(check, tested=1, scope={'rows': [rows[left], rows[right]], 'unit': 'row_pairs',
                                         'common_columns': len(positions)})
            relation = ({'exact': True} if first == second else None) if method == 'exact_column' else (
                _transform(first, second, method, tolerance))
            if relation is None:
                continue
            signal = add('review', check, '', '同量纲测量行的数值关系；重复测量、派生公式或共享对照均可能产生该关系。')
            signal['evidence'] = {'direction': 'across_columns', 'relationship': relation,
                                  'cells': [{'row': row, 'column_index': column}
                                            for row in (rows[left], rows[right]) for column in positions]}
            matched = True
    total = comb(len(frame), 2)
    examined = min(comb(len(rows), 2), MAX_ROW_PAIRS)
    for check in checks:
        record(check, status='truncated' if examined < total else '',
               reason='row_pair_search_limit' if examined < total else 'fewer_than_2_rows' if not examined else '',
               pairs_examined=examined, pairs_omitted=total - examined, pair_limit=MAX_ROW_PAIRS,
               measurement_columns=physical, minimum_common_columns=5, minimum_common_fraction=0.8,
               absolute_tolerance=str(absolute), relative_tolerance=str(relative), tolerance_scale='variation')
