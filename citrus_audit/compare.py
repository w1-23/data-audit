"""Bind comparison methods to frozen, explicitly supplied source snapshots."""
from .envelope import TableEnvelope
from .integrity import IntegrityConfig, audit_numeric_reuse, table_from_rows
from .plan import compare_bound, key_columns, parse_plan
from .reporting import dumps, loads


def compare_snapshots(snapshots, plan, *, integrity_options=None):
    """Run selected cross-file methods and explicitly bound comparison entries."""
    plan = parse_plan(plan)
    snapshots = tuple(snapshots)
    if not 1 <= len(snapshots) <= 20:
        raise ValueError('Supply between 1 and 20 saved source snapshots')
    if integrity_options and not plan['cross_file_methods']:
        raise ValueError('Integrity parameters require selected methods')
    bindings = {}
    numeric_tables = []
    errors = []
    source_issues = []
    key_specs = {}
    for item in plan['comparisons']:
        if item['method'] == 'sample_alignment':
            for side in (item['left'], item['right']):
                key_specs.setdefault((side['file_index'], side['sheet']), set()).add(tuple(side['key_columns']))
    for index, snapshot in enumerate(snapshots):
        report = snapshot.report
        if report['errors']:
            source_issues.append({'file_index': index, 'source_sha256': snapshot.source_sha256,
                                  'errors': report['errors']})
        for entry in report['tables']:
            table = TableEnvelope.from_dict(entry['table'])
            coverage = table.coverage
            source_id = str(index)
            try:
                numeric = table_from_rows(table.values, table.columns, source_id=source_id,
                    source_name=table.source_name, source_bytes=snapshot.source_bytes, sheet=table.sheet,
                    row_numbers=table.row_numbers, column_numbers=table.column_numbers,
                    source_rows=max(len(table.row_numbers), coverage.get('source_rows', 0)),
                    source_columns=max(len(table.column_numbers), coverage.get('source_columns', 0)),
                    truncation_reasons=coverage.get('truncation_reasons') or (
                        ['source_snapshot_truncated'] if coverage.get('truncated') else []),
                    region_id=table.region_id, region_bounds=table.region_bounds)
            except ValueError as error:
                errors.append({'file_index': index, 'sheet': table.sheet, 'region_id': table.region_id,
                               'code': getattr(error, 'code', type(error).__name__), 'message': str(error)})
                continue
            key = (index, table.sheet, table.region_id) if table.region_id else (index, table.sheet)
            if key in bindings:
                raise ValueError('Repeated table identity in snapshot')
            specs = sorted(key_specs.get((index, table.sheet), ()))
            keys = {}
            if specs:
                frame = table.to_frame()
                keys = {spec: key_columns(frame, spec) for spec in specs}
            bindings[key] = (numeric, keys)
            numeric_tables.append(numeric)
    config = IntegrityConfig(selected_methods=tuple(plan['cross_file_methods']), **(integrity_options or {}))
    integrity = audit_numeric_reuse(numeric_tables, config=config) if plan['cross_file_methods'] else None
    comparisons = compare_bound(plan, bindings)
    if errors or source_issues or any(item['status'] in {'partial', 'skipped'} for item in comparisons):
        status = 'partial'
    elif integrity and integrity['coverage_status'] in {'partial', 'not_applicable'}:
        status = integrity['coverage_status']
    else:
        status = 'complete' if integrity or comparisons else 'not_applicable'
    return loads(dumps({'version': plan['schema'], 'status': status, 'integrity': integrity,
                        'comparisons': comparisons, 'errors': errors, 'source_issues': source_issues}))
