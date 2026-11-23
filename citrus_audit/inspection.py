"""Bounded workbook layout, region discovery and source-coordinate pages.

Layout inspection never builds a DataFrame.  Region discovery is a
deliberately conservative partition of the bounded scan window: a full blank
row or a full blank column separates regions, because side-by-side and stacked
tables are otherwise indistinguishable from one table with a gap.  Every
reported coordinate is an absolute 1-based sheet coordinate, so the same
bounds can be handed to envelope construction without re-originating rows.
"""
from contextlib import closing
from hashlib import sha256
import io
import json
import re
from zipfile import ZipFile

from .readers import _missing_sheet_message, open_xlsx_readonly

_MAX_BYTES = 12_000
_MAX_CELLS = 200
_MAX_SCAN_ROW = 100_000
_MAX_LAYOUT_CELLS = 131_072
_MIN_LAYOUT_COLUMNS = 128
_MAX_LAYOUT_COLUMNS = 512
_MAX_REGIONS = 40
_MAX_LAYOUT_BYTES = 23_000
_MAX_FORMULA_CELLS = 80
_MAX_REGION_FORMULAS = 20
_MAX_HIDDEN_SHEETS = 40
_MAX_HINT_ROWS = 3
_XML_NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'

_LIMITATION_NOTES = {
    'scan_window_limited': 'Some cells fall outside the bounded layout scan. '
                           'Use the reported scan bounds when selecting a range to inspect.',
    'regions_truncated': 'Only a bounded subset of non-empty regions is reported (cap 40 and '
                        'an overall layout byte budget).',
    'merged_ranges_truncated': 'More than 80 merged ranges exist; later merges are not reported '
                              'and do not extend an observed region.',
    'sheet_layout_truncated': 'Only the first 40 worksheets are listed with visibility.',
    'hidden_sheet_list_truncated': 'Only 40 hidden worksheets are named; the count is exact.',
    'formula_cells_truncated': 'Only a bounded sample of formula coordinates is reported.',
    'header_rows_outside_scan': 'Requested header rows fall outside the bounded scan window.',
    'region_without_header_rows': 'No requested header row intersects this region; no data rows '
                                 'are claimed and header_hint_rows are heuristic only.',
    'rows_before_first_header': 'Observed rows above the first requested header row are neither '
                               'header nor data and are left unclassified.',
    'noncontiguous_header_rows': 'Requested header rows inside this region are not contiguous.',
    'region_touches_scan_boundary': 'The region reaches the right or bottom scan boundary; its '
                                   'reported bounds may be incomplete.',
    'merged_range_outside_scan': 'A merged range intersecting this region extends outside the '
                                'bounded scan window.',
}


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, default=str).encode('utf-8'))


def _cell_value(cell):
    value = cell.value
    item = {'cell': cell.coordinate, 'type': cell.data_type, 'value': value}
    if _size(item) > 2048:
        text = str(value)
        item.update(value=text[:400], truncated=True, value_characters=len(text))
    return item


def _merges(data, path):
    from openpyxl.xml.functions import iterparse

    result = []
    with ZipFile(io.BytesIO(data)) as archive, archive.open(path) as source:
        sheet = None
        for event, element in iterparse(source, events=('start', 'end')):
            if sheet is None:
                sheet = element
            if event != 'end':
                continue
            if element.tag == _XML_NS + 'mergeCell':
                ref = element.get('ref', '')
                if re.fullmatch(r'[A-Z]{1,3}[1-9][0-9]{0,6}:[A-Z]{1,3}[1-9][0-9]{0,6}', ref):
                    if len(result) == 80:
                        return result, True
                    result.append({'range': ref, 'anchor': ref.split(':', 1)[0]})
            elif element.tag in {_XML_NS + 'row', _XML_NS + 'sheetData'}:
                element.clear()
                sheet.clear()
    return result, False


def _merge_rectangles(merges, scan_rows, scan_columns):
    """Parse merge references into clamped scan rectangles and clipping flags."""
    from openpyxl.utils.cell import range_boundaries

    parsed = []
    for item in merges:
        try:
            left, top, right, bottom = range_boundaries(item['range'])
        except (TypeError, ValueError):
            continue
        if None in (left, top, right, bottom):
            continue
        parsed.append({'range': item['range'],
                       'box': (max(1, top), min(scan_rows, bottom),
                               max(1, left), min(scan_columns, right)),
                       'outside_scan': bottom > scan_rows or right > scan_columns})
    return parsed


def _tiles(occupied):
    """Partition observed cells into rectangles separated by full blank rows/cols.

    The partition intentionally does not bridge a completely blank row or
    column: two tables that look adjacent are kept separate rather than being
    joined into one misleading region.  Ordering is stable (top-left first).
    """
    rows = sorted(occupied)
    columns = sorted({column for cells in occupied.values() for column in cells})
    if not rows or not columns:
        return []
    output = []

    def split(top, bottom, left, right):
        points = {(row, column) for row in range(top, bottom + 1)
                  for column in occupied.get(row, ()) if left <= column <= right}
        if not points:
            return
        seen_rows = {row for row, _ in points}
        seen_columns = {column for _, column in points}
        top, bottom, left, right = min(seen_rows), max(seen_rows), min(seen_columns), max(seen_columns)
        empty_rows = set(range(top, bottom + 1)) - seen_rows
        if empty_rows:
            start = top
            for row in range(top, bottom + 1):
                if row in empty_rows:
                    if start <= row - 1:
                        split(start, row - 1, left, right)
                    start = row + 1
            if start <= bottom:
                split(start, bottom, left, right)
            return
        empty_columns = set(range(left, right + 1)) - seen_columns
        if empty_columns:
            start = left
            for column in range(left, right + 1):
                if column in empty_columns:
                    if start <= column - 1:
                        split(top, bottom, start, column - 1)
                    start = column + 1
            if start <= right:
                split(top, bottom, start, right)
            return
        output.append((top, bottom, left, right))

    split(rows[0], rows[-1], columns[0], columns[-1])
    output.sort()
    return output


def _header_like(values):
    texts = [str(value).strip() for value in values if value is not None and str(value).strip()]
    if len(texts) < 2 or len(set(texts)) < 2:
        return False
    if any(len(text) > 40 for text in texts):
        return False
    numeric = sum(1 for text in texts
                  if re.fullmatch(r'[+-]?[0-9]+(?:[.][0-9]+)?', text))
    return numeric < max(1, len(texts) // 2)


def _header_hint_rows(top, bottom, values_by_row):
    """Conservative hint only; never applied as an actual header selection."""
    hints = []
    previous_distinct = 0
    for row in range(top, min(bottom, top + _MAX_HINT_ROWS - 1) + 1):
        values = values_by_row.get(row, ())
        if not _header_like(values):
            break
        distinct = len({str(value).strip() for value in values if str(value).strip()})
        if hints and distinct <= previous_distinct:
            break
        hints.append(row)
        previous_distinct = distinct
    return hints


def _column_letter(number):
    result = ''
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _bounds(top, bottom, left, right):
    return f'{_column_letter(left)}{top}:{_column_letter(right)}{bottom}'


def _separation(first, second):
    a_top, a_bottom, a_left, a_right = first
    b_top, b_bottom, b_left, b_right = second
    rows_overlap = max(a_top, b_top) <= min(a_bottom, b_bottom)
    columns_overlap = max(a_left, b_left) <= min(a_right, b_right)
    if rows_overlap and not columns_overlap:
        return 'horizontal'
    if columns_overlap and not rows_overlap:
        return 'vertical'
    if rows_overlap and columns_overlap:
        return 'overlap'
    return 'diagonal'


def _regions(sheet, tiles, values_by_row, formulas_by_row, merges, header_rows,
             hidden, clipped_edges):
    requested = sorted({row for row in header_rows if type(row) is int})
    region_rows = []
    for index, (top, bottom, left, right) in enumerate(tiles, 1):
        limitations = []
        headers = [row for row in requested if top <= row <= bottom]
        if headers and headers != list(range(min(headers), max(headers) + 1)):
            limitations.append('noncontiguous_header_rows')
        if headers and min(headers) > top:
            limitations.append('rows_before_first_header')
        header_bottom = max(headers) if headers else None
        data_rows = [row for row in range(header_bottom + 1, bottom + 1)] if headers else []
        formula_cells = sorted(((f'{_column_letter(column)}{row}', row, column)
                                for row in range(top, bottom + 1)
                                for column in formulas_by_row.get(row, ()) if left <= column <= right),
                               key=lambda item: (item[1], item[2]))
        region_formulas = formula_cells[:_MAX_REGION_FORMULAS]
        if len(formula_cells) > len(region_formulas):
            limitations.append('formula_cells_truncated')
        region_merges = [item['range'] for item in merges
                         if max(top, item['box'][0]) <= min(bottom, item['box'][1])
                         and max(left, item['box'][2]) <= min(right, item['box'][3])]
        if any(item['outside_scan'] and max(top, item['box'][0]) <= min(bottom, item['box'][1])
               and max(left, item['box'][2]) <= min(right, item['box'][3]) for item in merges):
            limitations.append('merged_range_outside_scan')
        touches = [edge for edge, boundary in clipped_edges.items()
                   if (right if edge == 'right' else bottom) >= boundary]
        if touches:
            limitations.append('region_touches_scan_boundary')
        if not headers:
            limitations.append('region_without_header_rows')
        hints = [] if headers else _header_hint_rows(top, bottom, values_by_row)
        rows = list(range(top, bottom + 1))
        region = {
            'region_id': f'{sheet.title}!R{index}',
            'sheet': sheet.title,
            'bounds': _bounds(top, bottom, left, right),
            'header_rows': headers,
            'header_bounds': (_bounds(min(headers), max(headers), left, right)
                              if headers else None),
            'data_rows': data_rows,
            'data_bounds': (_bounds(min(data_rows), max(data_rows), left, right)
                            if data_rows else None),
            'rows': rows,
            'column_numbers': list(range(left, right + 1)),
            'header_hint_rows': hints,
            'header_detection': 'requested' if headers else
                                ('heuristic_hint' if hints else 'none'),
            'formula_cells': [cell for cell, _row, _column in region_formulas],
            'formula_count': len(formula_cells),
            'formulas_truncated': len(formula_cells) > len(region_formulas),
            'merged_ranges': region_merges,
            'sheet_visibility': sheet.sheet_state,
            'hidden_sheet': hidden,
            'touches_scan_boundary': touches,
            'separation': 'single',
            'neighbors': [],
            'limitations': limitations,
        }
        region_rows.append((region, (top, bottom, left, right)))

    # Stable pairwise orientation.  Overlapping boxes can only appear when the
    # tile cap truncated the list, and are reported rather than guessed away.
    for index, (region, box) in enumerate(region_rows):
        kinds = set()
        for other_index, (other, other_box) in enumerate(region_rows):
            if index == other_index:
                continue
            kind = _separation(box, other_box)
            if kind in {'horizontal', 'vertical'}:
                kinds.add(kind)
                region['neighbors'].append(other['region_id'])
        region['separation'] = ('single' if not kinds else
                                kinds.pop() if len(kinds) == 1 else 'grid')
    return [region for region, _box in region_rows]


def _layout(workbook, sheet, data, header_rows):
    from openpyxl.utils import get_column_letter

    names = [item.title for item in workbook.worksheets]
    reported = sheet.calculate_dimension() if sheet.max_row and sheet.max_column else None
    column_limit = min(_MAX_LAYOUT_COLUMNS, max(_MIN_LAYOUT_COLUMNS, sheet.max_column or 0))
    row_limit = _MAX_LAYOUT_CELLS // column_limit
    sheet.reset_dimensions()
    headers, occupied = [], {}
    values_by_row, formulas_by_row = {}, {}
    scan_rows = scan_columns = 0
    clipped_edges = {}
    with closing(sheet.iter_rows(min_row=1)) as rows:
        for row_index, cells in enumerate(rows, 1):
            if row_index > row_limit:
                clipped_edges['bottom'] = row_limit
                break
            scan_rows = row_index
            scan_columns = max(scan_columns, min(len(cells), column_limit))
            if any(cell.value is not None for cell in cells[column_limit:]):
                clipped_edges['right'] = column_limit
            present = [cell for cell in cells[:column_limit] if cell.value is not None]
            occupied[row_index] = {cell.column for cell in present}
            values_by_row[row_index] = [cell.value for cell in present]
            formulas = {cell.column for cell in present if cell.data_type == 'f'}
            if formulas:
                formulas_by_row[row_index] = formulas
            if row_index in header_rows:
                headers.extend(_cell_value(cell) for cell in present)
    # Each block is a contiguous rectangle of observed occupied cells. Gaps are
    # meaningful, especially for side-by-side tables and multi-row headings.
    blocks = []
    active = {}
    for row_index, columns in occupied.items():
        runs = []
        for column in sorted(columns):
            if runs and runs[-1][1] == column - 1:
                runs[-1] = (runs[-1][0], column)
            else:
                runs.append((column, column))
        active = {run: active.get(run) or [row_index, row_index, *run] for run in runs}
        for block in active.values():
            if block[0] == row_index:
                blocks.append(block)
            block[1] = row_index
    merges, merges_truncated = _merges(data, sheet._worksheet_path)
    parsed_merges = _merge_rectangles(merges, row_limit, column_limit)
    scan_rows = max([scan_rows, *(item['box'][1] for item in parsed_merges), 1])
    scan_columns = max([scan_columns, *(item['box'][3] for item in parsed_merges), 1])
    result = {'sheet': sheet.title, 'sheets': names[:40], 'sheet_count': len(names),
              'sheet_layout': [{'name': item.title, 'visibility': item.sheet_state}
                               for item in workbook.worksheets[:40]],
              'reported_dimension': reported, 'observed_range': f'A1:{get_column_letter(scan_columns)}{scan_rows}',
              'header_rows': header_rows, 'header_cells': [], 'header_cells_omitted': 0,
              'merged_ranges': merges, 'merged_ranges_truncated': merges_truncated,
              'observed_blocks': [f'{get_column_letter(left)}{top}:{get_column_letter(right)}{bottom}'
                                  for top, bottom, left, right in blocks[:40]],
              'observed_blocks_truncated': len(blocks) > 40}
    for cell in headers:
        result['header_cells'].append(cell)
        if _size(result) > _MAX_BYTES - 1200:
            result['header_cells'].pop()
            break
    result['header_cells_omitted'] = len(headers) - len(result['header_cells'])

    # Region discovery: merge rectangles extend occupancy so a merged group
    # heading keeps its visible width, but only the bounded scan window is used.
    expanded = {row: set(columns) for row, columns in occupied.items()}
    for item in parsed_merges:
        top, bottom, left, right = item['box']
        if top > bottom or left > right:
            continue
        for row in range(top, bottom + 1):
            expanded.setdefault(row, set()).update(range(left, right + 1))
    tiles = _tiles(expanded)
    regions_truncated = len(tiles) > _MAX_REGIONS
    tiles = tiles[:_MAX_REGIONS]
    hidden = sheet.sheet_state != 'visible'
    regions = _regions(sheet, tiles, values_by_row, formulas_by_row, parsed_merges,
                       header_rows, hidden, clipped_edges)
    # Keep the additive view bounded as well: legacy header cells are already
    # trimmed, so the region list may not grow the response without limit.
    kept = []
    for region in regions:
        if _size({**result, 'regions': kept + [region]}) > _MAX_LAYOUT_BYTES:
            regions_truncated = True
            break
        kept.append(region)
    regions = kept
    total_formulas = [f'{_column_letter(column)}{row}'
                      for row, columns in sorted(formulas_by_row.items())
                      for column in sorted(columns)]
    formula_cells = total_formulas[:_MAX_FORMULA_CELLS]
    hidden_names = [item.title for item in workbook.worksheets if item.sheet_state != 'visible']
    limitations = ['scan_window_limited'] if clipped_edges else []
    if regions_truncated:
        limitations.append('regions_truncated')
    if merges_truncated:
        limitations.append('merged_ranges_truncated')
    if len(workbook.worksheets) > 40:
        limitations.append('sheet_layout_truncated')
    if len(hidden_names) > _MAX_HIDDEN_SHEETS:
        limitations.append('hidden_sheet_list_truncated')
    if len(total_formulas) > len(formula_cells):
        limitations.append('formula_cells_truncated')
    if any(type(row) is not int or not 1 <= row <= scan_rows for row in header_rows):
        limitations.append('header_rows_outside_scan')
    for region in regions:
        for code in region['limitations']:
            if code not in limitations:
                limitations.append(code)
    if not regions:
        region_layout = 'none'
    else:
        kinds = {region['separation'] for region in regions}
        if 'grid' in kinds or {'horizontal', 'vertical'} <= kinds:
            region_layout = 'grid'
        elif 'horizontal' in kinds:
            region_layout = 'horizontal'
        elif 'vertical' in kinds:
            region_layout = 'vertical'
        else:
            region_layout = 'single'
    result.update(
        regions=regions,
        region_count=len(regions),
        regions_truncated=regions_truncated,
        region_layout=region_layout,
        limitations=limitations,
        detection={'method': 'blank_row_and_column_partition',
                   'scan_rows': scan_rows, 'scan_columns': scan_columns,
                   'scan_complete': not clipped_edges and not merges_truncated
                                    and not any(item['outside_scan'] for item in parsed_merges),
                   'scan_clipped_edges': list(clipped_edges),
                   'max_regions': _MAX_REGIONS,
                   'max_layout_bytes': _MAX_LAYOUT_BYTES,
                   'formula_scan': 'scan_window_only',
                   'notes': {code: _LIMITATION_NOTES.get(code, code) for code in limitations}},
        formula_cells=formula_cells,
        formula_cells_truncated=len(total_formulas) > len(formula_cells),
        hidden_sheets=hidden_names[:_MAX_HIDDEN_SHEETS],
        hidden_sheet_count=len(hidden_names),
        sheet_visibility=sheet.sheet_state,
        sheet_layout_truncated=len(workbook.worksheets) > 40,
    )
    return result


def _cells(sheet, cell_range, page_rows, cursor, digest):
    from openpyxl.utils.cell import range_boundaries, get_column_letter

    left, top, right, bottom = range_boundaries(cell_range)
    if (None in (left, top, right, bottom) or not 1 <= left <= right <= 16_384
            or not 1 <= top <= bottom <= _MAX_SCAN_ROW):
        raise ValueError('请给出有效的单元格范围，例如 A1:F40，最多读取到第100000行。')
    width = right - left + 1
    stamp = sha256(json.dumps([digest, sheet.title, left, top, right, bottom]).encode()).hexdigest()
    offset = 0
    if cursor:
        scope, separator, value = cursor.partition(':')
        if scope != stamp or not separator or not re.fullmatch(r'[0-9]{1,10}', value):
            raise ValueError('分页游标与来源、工作表或范围不匹配。')
        offset = int(value)
    total = (bottom - top + 1) * width
    if offset >= total:
        raise ValueError('分页游标超出指定范围。')
    end = min(total, offset + _MAX_CELLS, ((offset // width) + page_rows) * width)
    page = {'sheet': sheet.title, 'cell_range': cell_range, 'cells': [], 'next_cursor': None,
            'source_sha256': digest, 'start_offset': offset, 'total_cells': total}
    sheet.reset_dimensions()
    position = offset
    while position < end:
        row = top + position // width
        first_column = left + position % width
        # Read at most one rectangle with a fixed width per page. Pinning a
        # column interval prevents a wide source from expanding a single row.
        columns = min(right - first_column + 1, end - position)
        multirow = first_column == left and columns == width
        last_row = min(bottom, row + (end - position) // width - 1) if multirow else row
        for row_index, values in zip(range(row, last_row + 1), sheet.iter_rows(
                min_row=row, max_row=last_row, min_col=first_column, max_col=first_column + columns - 1)):
            for column, cell in enumerate(values, first_column):
                item = (_cell_value(cell) if cell.value is not None else
                        {'cell': f'{get_column_letter(column)}{row_index}', 'type': 'blank', 'value': None})
                page['cells'].append(item)
                if _size(page) > _MAX_BYTES - 1200:
                    page['cells'].pop()
                    page['next_cursor'] = f'{stamp}:{position}'
                    return page
                position += 1
        # openpyxl can omit trailing empty rows with unknown dimensions.
        while position < end and top + position // width <= last_row:
            row_index, column = top + position // width, left + position % width
            page['cells'].append({'cell': f'{get_column_letter(column)}{row_index}', 'type': 'blank', 'value': None})
            if _size(page) > _MAX_BYTES - 1200:
                page['cells'].pop()
                page['next_cursor'] = f'{stamp}:{position}'
                return page
            position += 1
    if position < total:
        page['next_cursor'] = f'{stamp}:{position}'
    return page


def inspect_workbook(data, name, *, sheet_name, header_rows, mode, cell_range, page_rows, cursor):
    digest = sha256(data).hexdigest()
    with closing(open_xlsx_readonly(data, name, data_only=False)) as workbook:
        names = [sheet.title for sheet in workbook.worksheets]
        chosen = sheet_name.strip() or next((sheet.title for sheet in workbook.worksheets
                                             if sheet.sheet_state == 'visible'), '')
        if chosen not in names:
            raise ValueError(_missing_sheet_message(chosen, names))
        sheet = workbook[chosen]
        result = (_layout(workbook, sheet, data, header_rows) if mode == 'layout'
                  else _cells(sheet, cell_range, page_rows, cursor, digest))
    return {'name': name, 'inspection': mode, 'source_sha256': digest, **result}


def discover_regions(data, name, *, sheet_name='', header_rows=None):
    """Return only the additive multi-region discovery view of a workbook.

    Existing callers keep using ``inspect_workbook``; this helper is the stable
    entry point for region-aware integration.
    """
    result = inspect_workbook(data, name, sheet_name=sheet_name,
                              header_rows=([1] if header_rows is None
                                           else sorted(set(header_rows))),
                              mode='layout', cell_range='', page_rows=10, cursor='')
    return {'name': result['name'], 'sheet': result['sheet'],
            'sheet_visibility': result['sheet_visibility'],
            'source_sha256': result['source_sha256'],
            'region_count': result['region_count'],
            'regions_truncated': result['regions_truncated'],
            'region_layout': result['region_layout'],
            'regions': result['regions'],
            'limitations': result['limitations'],
            'detection': result['detection']}
