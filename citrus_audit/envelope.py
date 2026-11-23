"""Serializable source coordinates and lexical evidence; no implicit host state.

Version 2 remains the wire format.  Multi-region discovery adds optional,
defaulted coordinates (``header_rows``, ``region_id``, ``region_bounds``,
``region_separation`` and ``source_provenance``) so legacy envelopes and
legacy callers keep the same constructor arguments, table shape and audit API.
All coordinates are absolute 1-based sheet rows/columns.
"""
from __future__ import annotations
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import hashlib
import json
import re
from typing import Any
from .integrity import _number

_REGION_BOUNDS = re.compile(r'[A-Z]{1,3}[1-9][0-9]{0,6}:[A-Z]{1,3}[1-9][0-9]{0,6}')
_SEPARATIONS = {'', 'single', 'horizontal', 'vertical', 'grid'}
_MAX_PROVENANCE_BYTES = 8192


def _scalar(value):
    if value is None or type(value).__name__ in {'NAType','NaTType'}:
        return None
    if hasattr(value,'item'):
        value=value.item()
    if isinstance(value,Decimal):
        return {'decimal':str(value)}
    if isinstance(value,float):
        import math
        if not math.isfinite(value):
            return {'float':repr(value)}
    if type(value) in {str,int,float,bool}:
        return value
    if isinstance(value, (date, time)):
        return {'datetime': value.isoformat()}
    if isinstance(value, timedelta):
        return {'duration': [value.days, value.seconds, value.microseconds]}
    raise ValueError('Unsupported table cell type')


def _restore(value):
    if not isinstance(value,dict):
        return value
    if set(value)=={'decimal'}:return Decimal(value['decimal'])
    if set(value)=={'float'} and value['float'] in {'nan','inf','-inf'}:return float(value['float'])
    if set(value)=={'datetime'}:
        stamp = value['datetime']
        if 'T' in stamp:
            return datetime.fromisoformat(stamp)
        return time.fromisoformat(stamp) if ':' in stamp else date.fromisoformat(stamp)
    if set(value) == {'duration'}:
        days, seconds, micros = value['duration']
        return timedelta(days=days, seconds=seconds, microseconds=micros)
    raise ValueError('Unsupported serialized cell')


def _column_letter(number):
    result=''
    while number:
        number,remainder=divmod(number-1,26)
        result=chr(65+remainder)+result
    return result


def _shift_bounds(bounds,row_offset,column_offset):
    from openpyxl.utils.cell import range_boundaries
    left,top,right,bottom=range_boundaries(bounds)
    if None in (left,top,right,bottom):
        raise ValueError('Region bounds are not a concrete cell range')
    shifted=(left+column_offset,top+row_offset,right+column_offset,bottom+row_offset)
    if min(shifted)<1:
        raise ValueError('Region bounds cannot shift before A1')
    return (f'{_column_letter(shifted[0])}{shifted[1]}:'
            f'{_column_letter(shifted[2])}{shifted[3]}')


def _region_provenance(region):
    keys=('bounds','data_bounds','formula_cells','formula_count',
          'formulas_truncated','sheet_visibility','hidden_sheet','merged_ranges',
          'touches_scan_boundary','limitations')
    provenance={key:region[key] for key in keys if key in region}
    if 'header_rows' in region:
        provenance['header_rows']=list(region['header_rows'])
    return provenance


@dataclass(frozen=True)
class TableEnvelope:
    columns: tuple[str,...]
    values: tuple[tuple[Any,...],...]
    row_numbers: tuple[int,...]
    column_numbers: tuple[int,...]
    source_sha256: str
    source_name: str = ''
    sheet: str = ''
    header_row: int = 1
    parser_limits: dict = field(default_factory=dict)
    precision_evidence: tuple[dict,...] = ()
    lexical_coverage: dict = field(default_factory=dict)
    coverage: dict = field(default_factory=dict)
    version: int = 2
    header_rows: tuple[int,...] = ()
    region_id: str = ''
    region_bounds: str = ''
    region_separation: str = ''
    source_provenance: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.version!=2 or not re.fullmatch('[a-f0-9]{64}',self.source_sha256):
            raise ValueError('Envelope requires v2 and original source SHA-256')
        header_rows=(tuple(self.header_rows) if self.header_rows else (self.header_row,))
        if (not header_rows
                or any(type(row)is not int or row<1 for row in header_rows)
                or tuple(sorted(set(header_rows)))!=header_rows
                or header_rows[-1]!=self.header_row):
            raise ValueError('Header rows must be unique ascending original rows ending at header_row')
        object.__setattr__(self,'header_rows',header_rows)
        if (len(self.columns)!=len(self.column_numbers) or len(set(self.columns))!=len(self.columns)
                or any(not isinstance(c,str) or not c for c in self.columns)
                or len(self.values)!=len(self.row_numbers)
                or any(len(row)!=len(self.columns) for row in self.values)):
            raise ValueError('Envelope dimensions or column identities invalid')
        for coords in (self.row_numbers,self.column_numbers):
            if len(set(coords))!=len(coords) or any(type(v)is not int or v<1 for v in coords):
                raise ValueError('Original coordinates must be unique positive integers')
        if type(self.header_row)is not int or self.header_row<1 or any(r<=self.header_row for r in self.row_numbers):
            raise ValueError('Original data rows must follow header')
        if not isinstance(self.region_id,str) or len(self.region_id)>160:
            raise ValueError('Region id must be a bounded string')
        if not isinstance(self.region_bounds,str):
            raise ValueError('Region bounds must be a string')
        if self.region_bounds and not _REGION_BOUNDS.fullmatch(self.region_bounds):
            raise ValueError('Region bounds must be an absolute A1-style range')
        if self.region_separation not in _SEPARATIONS:
            raise ValueError('Invalid region separation')
        if not isinstance(self.source_provenance,dict):
            raise ValueError('Region provenance must be a mapping')
        try:
            encoded=json.dumps(self.source_provenance,ensure_ascii=False,allow_nan=False)
        except (TypeError,ValueError) as exc:
            raise ValueError('Region provenance must be JSON serializable') from exc
        if len(encoded.encode('utf-8'))>_MAX_PROVENANCE_BYTES:
            raise ValueError('Region provenance exceeds the bounded size')
        seen=set()
        row_positions={value:index for index,value in enumerate(self.row_numbers)}
        col_positions={value:index for index,value in enumerate(self.column_numbers)}
        for evidence in self.precision_evidence:
            if not isinstance(evidence,dict) or set(evidence)!={'row','column','raw','places','exp'}:
                raise ValueError('Invalid lexical evidence shape')
            row,col=evidence['row'],evidence['column']
            if row not in row_positions or col not in col_positions or (row,col) in seen:
                raise ValueError('Lexical evidence coordinate invalid')
            seen.add((row,col)); raw=evidence['raw']
            if not isinstance(raw,str) or len(raw)>256 or _number(raw)[0] is None:
                raise ValueError('Lexical evidence must be a bounded actual token')
            match=re.fullmatch(r'[+-]?(?:[0-9]+(?:\.([0-9]*))?|\.([0-9]+))(?:[eE]([+-]?[0-9]+))?',raw.strip())
            if not match or type(evidence['places'])is not int or type(evidence['exp'])is not int:
                raise ValueError('Invalid raw precision')
            if evidence['places']!=len(match[1] or match[2] or '') or evidence['exp']!=int(match[3] or 0):
                raise ValueError('Precision metadata differs from actual token')
            value=self.values[row_positions[row]][col_positions[col]]
            if not isinstance(value,str) or value.strip()!=raw.strip():
                raise ValueError('Raw evidence cannot manufacture digits for numeric cells')

    @property
    def bounds(self):
        """Absolute original bounds including all header rows and data rows."""
        if self.region_bounds:
            return self.region_bounds
        if not self.column_numbers:
            return ''
        top=min(self.header_rows)
        left,right=min(self.column_numbers),max(self.column_numbers)
        bottom=max(self.row_numbers) if self.row_numbers else self.header_row
        return f'{_column_letter(left)}{top}:{_column_letter(right)}{bottom}'

    def to_dict(self):
        payload={'version':self.version,'columns':list(self.columns),
            'values':[[_scalar(v) for v in row] for row in self.values],
            'row_numbers':list(self.row_numbers),'column_numbers':list(self.column_numbers),
            'source_sha256':self.source_sha256,'source_name':self.source_name,'sheet':self.sheet,
            'header_row':self.header_row,'parser_limits':self.parser_limits,
            'precision_evidence':list(self.precision_evidence),'lexical_coverage':self.lexical_coverage,
            'coverage':self.coverage}
        # Emit the additive region coordinates only when they carry information.
        # A legacy envelope serializes exactly as before, so canonical replay of
        # older payloads (strict canonical-field checks elsewhere) still works.
        if (self.header_rows!=(self.header_row,) or self.region_id or self.region_bounds
                or self.region_separation or self.source_provenance):
            payload.update(header_rows=list(self.header_rows),region_id=self.region_id,
                region_bounds=self.region_bounds,region_separation=self.region_separation,
                source_provenance=self.source_provenance)
        return payload

    @classmethod
    def from_dict(cls,data):
        data=dict(data)
        data['values']=tuple(tuple(_restore(v) for v in row) for row in data['values'])
        for key in ('columns','row_numbers','column_numbers','precision_evidence','header_rows'):
            if key in data:
                data[key]=tuple(data[key])
        return cls(**data)

    @classmethod
    def from_region(cls,*,columns,values,source_sha256,region,source_name='',sheet='',
                    parser_limits=None,precision_evidence=(),lexical_coverage=None,
                    coverage=None,source_provenance=None):
        """Build an envelope with absolute coordinates from an inspection region.

        ``region`` is one entry of ``inspect_workbook(..., mode='layout')['regions']``
        (or ``discover_regions(...)['regions']``).  ``values`` must cover exactly
        ``region['data_rows']``; a partial read must pass a correspondingly
        trimmed region or stay outside this constructor.
        """
        if not isinstance(region,dict):
            raise ValueError('A discovered region mapping is required')
        column_numbers=tuple(region.get('column_numbers') or ())
        header_rows=tuple(sorted({row for row in (region.get('header_rows') or ())
                                  if type(row)is int}))
        data_rows=tuple(region.get('data_rows') or ())
        if len(column_numbers)!=len(columns):
            raise ValueError('Discovered region columns do not match envelope columns')
        if len(data_rows)!=len(values):
            raise ValueError('Discovered region data rows do not match envelope values')
        if not header_rows:
            raise ValueError('Discovered region has no explicit header rows')
        provenance=(_region_provenance(region) if source_provenance is None
                    else source_provenance)
        return cls(columns=tuple(columns),values=tuple(tuple(row) for row in values),
            row_numbers=data_rows,column_numbers=column_numbers,
            source_sha256=source_sha256,source_name=source_name,
            sheet=sheet or str(region.get('sheet') or ''),
            header_row=max(header_rows),header_rows=header_rows,
            parser_limits={} if parser_limits is None else parser_limits,
            precision_evidence=tuple(precision_evidence),
            lexical_coverage={} if lexical_coverage is None else lexical_coverage,
            coverage={} if coverage is None else coverage,
            region_id=str(region.get('region_id') or ''),
            region_bounds=str(region.get('bounds') or ''),
            region_separation=str(region.get('separation') or ''),
            source_provenance=provenance)

    def relocate(self,*,row_offset=0,column_offset=0,region_id=None,region_bounds=None,
                 header_rows=None,source_provenance=None):
        """Shift legacy region-local coordinates to absolute original coordinates.

        Offsets are applied to data rows, columns, header rows, lexical evidence
        and the region bounds.  ``header_rows``, when supplied, are pre-offset
        header rows and receive the same row offset.  Zero offsets return an
        equivalent envelope, so callers can use this unconditionally for
        coordinate continuity.
        """
        if type(row_offset)is not int or type(column_offset)is not int:
            raise ValueError('Offsets must be integers')
        new_header_rows=(self.header_rows if header_rows is None else
                         tuple(sorted({row for row in header_rows if type(row)is int})))
        if not new_header_rows:
            raise ValueError('Header rows are required')
        shifted_rows=tuple(row+row_offset for row in self.row_numbers)
        shifted_columns=tuple(column+column_offset for column in self.column_numbers)
        shifted_headers=tuple(row+row_offset for row in new_header_rows)
        evidence=tuple({**item,'row':item['row']+row_offset,'column':item['column']+column_offset}
                       for item in self.precision_evidence)
        if region_bounds is None:
            region_bounds=(_shift_bounds(self.region_bounds,row_offset,column_offset)
                           if self.region_bounds else '')
        provenance=self.source_provenance if source_provenance is not None else dict(self.source_provenance)
        if source_provenance is None and provenance:
            if region_bounds:
                provenance['bounds']=region_bounds
            elif isinstance(provenance.get('bounds'),str) and provenance['bounds']:
                provenance['bounds']=_shift_bounds(provenance['bounds'],row_offset,column_offset)
            if 'header_rows' in provenance:
                provenance['header_rows']=list(shifted_headers)
        return replace(self,row_numbers=shifted_rows,column_numbers=shifted_columns,
            header_rows=shifted_headers,header_row=max(shifted_headers),
            precision_evidence=evidence,
            region_id=self.region_id if region_id is None else region_id,
            region_bounds=region_bounds,source_provenance=provenance)

    def to_frame(self):
        import pandas as pd
        frame=pd.DataFrame(self.values,columns=self.columns,index=self.row_numbers,dtype=object)
        column_names=dict(zip(self.column_numbers,self.columns))
        frame.attrs['_validated_precision_tokens']={(e['row'],column_names[e['column']]):e['raw'] for e in self.precision_evidence}
        frame.attrs['_column_numbers']=dict(zip(self.columns,self.column_numbers))
        frame.attrs['_region_id']=self.region_id
        frame.attrs['_region_bounds']=self.bounds
        return frame
