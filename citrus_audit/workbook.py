"""Annotated source copies, without implicit appendices or rescanning."""
from __future__ import annotations
import hashlib
import base64
import datetime
import decimal


def _json_default(value):
    """Encode known scalar types without inventing string identities."""
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):
        return {"duration": [value.days, value.seconds, value.microseconds]}
    if isinstance(value, decimal.Decimal):
        return float(value) if value.is_finite() else str(value)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, TextBlock):
        return {"rich_text_run": tostring(value.to_tree()).decode("utf-8")}
    if isinstance(value, (ArrayFormula, DataTableFormula)):
        return {"formula_type": type(value).__name__, "attributes": dict(value),
                "formula": getattr(value, "text", None)}
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=lambda item: repr(item))
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "item"):
        return value.item()
    raise TypeError("unsupported_audit_json_type:" + type(value).__name__)
import io
import json
import re
from pathlib import Path
from openpyxl import load_workbook, Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Border, PatternFill, Side
from openpyxl.cell.cell import MergedCell
from openpyxl.cell.rich_text import TextBlock
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.formula import ArrayFormula, DataTableFormula
from openpyxl.xml.functions import tostring
from .methods import COVERAGE_CHECKS
def _unique_sheet_title(wb, desired: str) -> str:
    """Choose a workbook metadata title without replacing an uploaded sheet."""
    base = re.sub(r"[\\/*?:\[\]]", "_", str(desired or "审查信息")).strip() or "审查信息"
    base = base[:31]
    existing = {str(name).casefold() for name in wb.sheetnames}
    if base.casefold() not in existing:
        return base
    for index in range(2, 1000):
        suffix = f"_{index}"
        candidate = base[:31 - len(suffix)] + suffix
        if candidate.casefold() not in existing:
            return candidate
    raise ValueError("工作簿中可用的审查工作表名称已耗尽")

_KINDS: dict[str, tuple[str, str, str]] = {
    "missing_cell": ("缺失", "需核查", "核对原始记录或实验台账，确认该值确实未测"),
    "missing_values": ("缺失", "需核查", "确认该列是否允许缺失，补齐或说明"),
    "non_numeric_cell": ("类型错误", "需核查", "确认是单位/符号/文本类别，还是录入错误"),
    "mixed_numeric_text": ("类型错误", "需核查", "分离单位或文本标记，保持数值列纯净"),
    "duplicate_row": ("重复", "需核查", "确认是重复录入还是合法的重复测量"),
    "duplicate_key": ("重复", "需核查", "确认样本/主键是否唯一，或改用分组编号"),
    "duplicate_composite_key": ("复合键重复", "需核查", "核对实验单位与访视/重复编号，确认该组合是否允许重复"),
    "required_field_absent": ("必填字段缺失", "需核查", "核对表头与显式必填字段映射"),
    "raw_numeric_tail": ("原始尾数集中", "需核查", "核对仪器精度、舍入与固定分母；描述频率不能解释为显著性"),
    "adjacent_equal_differences": ("相邻等差", "需核查", "核对原始顺序及计划剂量/时间网格"),
    "sliding_equal_differences": ("滑窗等差", "需核查", "核对原始顺序、窗口与容差；公式序列可合法出现"),
    "repeated_sequences": ("重复片段", "需核查", "对照两个原始片段，确认周期信号或合法重复"),
    "repeated_blocks": ("重复数值块", "需核查", "对照两个原始数值块，核对共享对照与导出过程"),
    "expected_experiment_structure": ("实验结构不符", "需核查", "核对显式设计、实验单位、重复数及排除政策"),
    "out_of_range": ("越界", "需核查", "与实验设计的合理范围比对后修正"),
    "logic_conflict": ("逻辑冲突", "需核查", "核对两列来源与计算关系"),
    "constant_field": ("常量列", "提示", "确认该字段是否有信息量"),
    "identical_columns": ("列重复", "需核查", "确认是否复制粘贴造成的重复列"),
    "constant_offset_columns": ("列关系", "需核查", "确认是否为换算列"),
    "constant_ratio_columns": ("列关系", "需核查", "确认是否为比例换算列"),
    "exact_linear_columns": ("列关系", "需核查", "确认是否为公式派生列"),
    "decimal_ending_concentration": ("末位集中", "提示", "确认是否为舍入或固定分母造成"),
    "unequal_group_size": ("分组不均", "提示", "确认是否按设计"),
    "unequal_replicates": ("重复数不一", "提示", "核对重复定义"),
    "integer_mean_inconsistent": ("均值不一致", "需核查", "核对数据粒度、舍入与 n 定义"),
    "integer_summary_inconsistent": ("统计量不一致", "需核查", "核对 SD 定义与舍入"),
    "invalid_group_mapping": ("字段未匹配", "提示", "修正分组字段名后重跑"),
    "invalid_value_mapping": ("字段未匹配", "提示", "修正测量字段名后重跑"),
    "design_checks_skipped": ("未做分组检查", "提示", "如需分组检查，请说明分组列"),
    "grim_sd_skipped": ("未做 SD 检查", "提示", "确认报告的是样本 SD 还是总体 SD"),
    "empty_table": ("空表", "阻断", "确认文件是否选错或表头行是否正确"),
    "empty_column": ("空列", "提示", "整列无数据，通常是公式列或预留空白列；如该列应有数据请核对导出方式"),
    "missing_row": ("缺行", "需核查", "确认该行是否被合并单元格或公式行占用"),
    "iqr_insufficient": ("样本不足", "提示", "组内样本不足 8，不做离群推断；如需判定请补充重复测量"),
    "iqr_outlier": ("离群值", "需核查", "对照原始记录确认该值是否为录入错误或真实生物学差异"),
    "mixed_numeric_text": ("类型混杂", "需核查", "统一该列的数值格式，或把文本说明移到备注列"),
    # 按行标注路径的结论，不是规则命中：措辞必须让读者看出这是模型意见。
    "row_annotation": ("按行标注", "需核查", "该条为人工/模型注释，请对照原始记录与实验设计确认"),
}

def _kind(check: str) -> tuple[str, str, str]:
    return _KINDS.get(check, (check or "其他", "提示", "人工复核"))


_RANK={'error':0,'warning':1,'review':1,'info':2,'ok':3}
_ANNOTATION_COLORS = {'ok': ('E7F4EA', '1F6B33'), 'review': ('FDF1D6', '8A5A00')}


def _sort(f):
    return (_RANK.get(f.get('severity',''),2),str(f.get('check','')),f.get('row') or 0,
            f.get('column_index') or 0,str(f.get('detail','')))


def _comment(findings, previous=''):
    ordered=sorted(findings,key=_sort)
    ref='findings:sha256:'+hashlib.sha256(json.dumps(ordered,ensure_ascii=False,sort_keys=True,default=_json_default).encode()).hexdigest()
    lines=[]
    if previous:lines.append('已有批注：'+' '.join(previous.split())[:250])
    for item in ordered[:10-len(lines)]:
        line='['+str(item.get('severity','review'))+'] '+str(item.get('check',''))+'：'+' '.join(str(item.get('detail','')).split())
        if len(lines)>=9 or sum(len(x)+1 for x in lines)+len(line)>1300:break
        lines.append(line)
    shown=len(lines)-(1 if previous else 0);overflow=len(ordered)-shown
    if overflow:lines.append(f'另有 {overflow} 条；完整发现 {ref}')
    elif len(lines)<10:lines.append('完整发现 '+ref)
    return '\n'.join(lines)[:1500],ref


def _data_signature(wb,bounds):
    state=[]
    for name,(height,width) in bounds.items():
        ws=wb[name]
        state.append([name,[str(x) for x in ws.merged_cells.ranges],
            [[c.coordinate,c.value,c.data_type,c.number_format] for row in ws.iter_rows(max_row=height,max_col=width) for c in row]])
    return hashlib.sha256(json.dumps(state,ensure_ascii=False,default=_json_default,sort_keys=True).encode()).hexdigest()


def _restore_original_cells(original, exported):
    """Retain original cell XML (including empty inline strings/formula caches).

    openpyxl normalizes some empty cells on save. Overlay only exported style
    references, leaving new comments/relationships and optional explanations intact.
    """
    import copy
    import posixpath
    import zipfile
    import xml.etree.ElementTree as ET
    ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
        'r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
    def sheets(archive):
        relations={node.attrib['Id']:node.attrib['Target'] for node in ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))}
        result={}
        for node in ET.fromstring(archive.read('xl/workbook.xml')).find('m:sheets',ns):
            target=relations[node.attrib['{'+ns['r']+'}id']]
            result[node.attrib['name']]=target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
        return result
    with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(io.BytesIO(exported)) as output:
        source_sheets,output_sheets=sheets(source),sheets(output)
        strings=list(ET.fromstring(source.read('xl/sharedStrings.xml'))) if 'xl/sharedStrings.xml' in source.namelist() else []
        patches={}
        for name,old_path in source_sheets.items():
            new_path=output_sheets[name]
            old_root,new_root=ET.fromstring(source.read(old_path)),ET.fromstring(output.read(new_path))
            old_data,new_data=old_root.find('m:sheetData',ns),new_root.find('m:sheetData',ns)
            if old_data is None or new_data is None:
                continue
            rows={node.attrib['r']:node for node in new_data}
            for old_row in old_data:
                row=rows.get(old_row.attrib['r'])
                if row is None:
                    row=ET.SubElement(new_data,old_row.tag,dict(old_row.attrib))
                cells={node.attrib['r']:node for node in row}
                for old_cell in old_row:
                    existing=cells.get(old_cell.attrib['r'])
                    restored=copy.deepcopy(old_cell)
                    if restored.attrib.get('t')=='s':
                        # openpyxl writes inline strings and removes sharedStrings.xml.
                        # Preserve the text/rich runs rather than dangling source indexes.
                        value=restored.find('m:v',ns)
                        if value is None or value.text is None:
                            raise ValueError('Invalid source shared string')
                        shared=strings[int(value.text)]
                        restored.remove(value);restored.attrib['t']='inlineStr'
                        inline=ET.SubElement(restored,'{'+ns['m']+'}is')
                        for child in shared:inline.append(copy.deepcopy(child))
                    if existing is not None:
                        if 's' in existing.attrib:restored.attrib['s']=existing.attrib['s']
                        else:restored.attrib.pop('s',None)
                        index=list(row).index(existing);row.remove(existing);row.insert(index,restored)
                    else:
                        row.append(restored)
            patches[new_path]=ET.tostring(new_root,encoding='utf-8',xml_declaration=True)
        result=io.BytesIO()
        with zipfile.ZipFile(result,'w',zipfile.ZIP_DEFLATED) as archive:
            for entry in output.infolist():
                archive.writestr(entry,patches.get(entry.filename,output.read(entry.filename)))
        return result.getvalue()


def export_snapshot(snapshot, *, explain_visible=False, issue_list=False, overview=False, rules=False):
    """Render saved findings on reviewed bytes. All appendices opt in."""
    if any(type(v)is not bool for v in (explain_visible,issue_list,overview,rules)):
        raise ValueError('Export switches must be boolean')
    if hashlib.sha256(snapshot.source_bytes).hexdigest()!=snapshot.source_sha256:
        raise ValueError('Source snapshot changed')
    report=snapshot.report
    suffix=Path(snapshot.source_name).suffix.lower()
    if suffix in {'.xlsx','.xlsm'}:
        from ._zip_safety import validate_zip_bytes
        validate_zip_bytes(snapshot.source_bytes)
        wb=load_workbook(io.BytesIO(snapshot.source_bytes),keep_vba=suffix=='.xlsm',rich_text=True)
    else:
        from .envelope import TableEnvelope
        wb=Workbook(); wb.remove(wb.active)
        for entry in report['tables']:
            table=TableEnvelope.from_dict(entry['table'])
            name=_unique_sheet_title(wb,table.sheet or Path(table.source_name).stem or 'Sheet1')
            ws=wb.create_sheet(name);entry['output_sheet']=name
            for col,title in zip(table.column_numbers,table.columns):
                cell=ws.cell(table.header_row,col,title);cell.data_type='s'
            for row,values in zip(table.row_numbers,table.values):
                for col,value in zip(table.column_numbers,values):
                    if type(value)is int and len(str(abs(value)))>15:value=str(value)
                    cell=ws.cell(row,col);cell.value=value
                    if isinstance(value,str):cell.data_type='s'
        if not wb.sheetnames:raise ValueError('No table snapshot available for workbook export')
    return _render_workbook(wb, report['tables'], source_bytes=snapshot.source_bytes,
                            source_sha256=snapshot.source_sha256, status=report['status'],
                            original=suffix in {'.xlsx', '.xlsm'}, keep_vba=suffix == '.xlsm',
                            explain_visible=explain_visible, issue_list=issue_list, overview=overview, rules=rules)


def export_reviewed_workbook(data, *, targets, source_sha256, original=True,
                             status='complete', issue_list=False, overview=False, rules=False):
    """Render absolute coordinates from an existing review on verified workbook bytes."""
    if original and hashlib.sha256(data).hexdigest() != source_sha256:
        raise ValueError('Reviewed workbook hash mismatch')
    wb = load_workbook(io.BytesIO(data), rich_text=True)
    entries = [{'table': {**target, 'sheet': target['sheet_name']},
                'result': {'data': {'findings': target['findings'],
                                    'rule_execution': target.get('rule_execution') or {}}}}
               for target in targets]
    return _render_workbook(wb, entries, source_bytes=data, source_sha256=source_sha256,
                            status=status, original=original, issue_list=issue_list, overview=overview, rules=rules)


def finding_coordinates(item, columns):
    """Return exact cells, with a scoped fallback for row-level issues."""
    evidence = item.get('evidence') or {}
    if 'cells' in evidence:
        return [(cell.get('row'), cell.get('column_index')) for cell in evidence['cells']]
    row, column = item.get('row'), item.get('column_index')
    if type(row) is int:
        return [(row, index) for index in ([column] if type(column) is int else columns)]
    if type(column) is int:
        return [(index, column) for index in evidence.get('rows', [])]
    return []


def _render_workbook(wb, entries, *, source_bytes, source_sha256, status, original,
                     keep_vba=False, explain_visible=False, issue_list=False, overview=False, rules=False):
    original_names=list(wb.sheetnames)
    bounds={ws.title:(ws.max_row,ws.max_column) for ws in wb.worksheets}
    if sum(height*width for height,width in bounds.values()) > 2_000_000:
        raise ValueError('Workbook export exceeds 2000000-cell verification budget')
    before=_data_signature(wb,bounds)
    all_findings, marks = [], {}
    for entry in entries:
        table = entry['table']
        name = entry.get('output_sheet', table['sheet'])
        if name not in bounds:
            raise ValueError('Snapshot worksheet missing')
        ws = wb[name]
        height, width = bounds[name]
        header = table['header_row']
        columns = table.get('column_numbers')
        if columns is None:
            columns = range(1, min(width, table.get('column_count', width)) + 1)
        columns = set(columns)
        rows = table.get('row_numbers')
        if rows is None:
            rows = range(header + 1, header + 1 + table.get('row_count', height - header))
        elif not isinstance(rows, range):
            rows = set(rows)
        for original_item in entry['result']['data']['findings']:
            item = {**original_item, 'sheet': name, 'reviewed_columns': sorted(columns),
                    'columns': {int(number): title for number, title in zip(
                        table.get('column_numbers') or (), table.get('columns') or ())}}
            item.setdefault('region_id', table.get('region_id') or '')
            all_findings.append(item)
            if item.get('coverage_only') or item.get('check') in COVERAGE_CHECKS:
                continue
            coords = finding_coordinates(item, sorted(columns))
            for row, column in coords:
                if type(row) is not int or row not in rows or type(column) is not int or column not in columns:
                    raise ValueError('Evidence cell outside reviewed table')
                if row > height or column > width:
                    continue
                cell = ws.cell(row, column)
                if isinstance(cell, MergedCell):
                    merged = next((area for area in ws.merged_cells.ranges if cell.coordinate in area), None)
                    if merged is None or merged.min_row not in rows or merged.min_col not in columns:
                        continue
                    row, column = merged.min_row, merged.min_col
                marks.setdefault((name, row, column), []).append(item)
    references = {}
    for (name, row, column), findings in sorted(marks.items()):
        cell = wb[name].cell(row, column)
        unique = {json.dumps(item, ensure_ascii=False, sort_keys=True, default=_json_default): item for item in findings}
        ordered = sorted(unique.values(), key=_sort)
        previous = cell.comment.text if cell.comment else ''
        text, ref = _comment(ordered)
        references[ref] = ordered
        cell.comment = Comment(previous + '\n\n' + text if previous else text, 'Citrus Audit')
        fill_color, line_color = 'FDE2E1', 'E06666'
        if all(item.get('check') == 'row_annotation' for item in ordered):
            fill_color, line_color = _ANNOTATION_COLORS.get(ordered[0].get('severity'), (fill_color, line_color))
        cell.fill = PatternFill('solid', fgColor=fill_color)
        side = Side(style='thin', color=line_color)
        cell.border = Border(left=side, right=side, top=side, bottom=side)
    explanations=[]
    def put(ws,row,col,value):
        cell=ws.cell(row,col,value)
        if isinstance(value,str):cell.data_type='s'
    if explain_visible:
        reviewed_sheets = {entry.get('output_sheet', entry['table']['sheet']) for entry in entries}
        for name in sorted(reviewed_sheets):
            height, width = bounds[name]
            ws=wb[name];start=width+2
            put(ws,1,start,'审查解释（自动生成，非数据）')
            local=sorted((x for x in all_findings if x['sheet']==name and not x.get('coverage_only')),key=_sort)
            by_row={}
            for item in local:
                row=item.get('row')
                if type(row) is int:
                    by_row.setdefault(row,[]).append(str(item.get('check',''))+'：'+str(item.get('detail','')))
            for row,items in sorted(by_row.items()):
                put(ws,row,start,'\n'.join(items)[:32000])
            explanations.append({'sheet':name,'start_column':start,'rows':sorted(by_row),
                'column_table_findings':[x for x in local if not x.get('row')]})
    added=[]
    def appendix(name,headers,rows):
        ws=wb.create_sheet(_unique_sheet_title(wb,name));added.append(ws.title)
        for r,values in enumerate([headers,*rows],start=1):
            for c,value in enumerate(values,start=1):put(ws,r,c,value)
    if issue_list:
        issue_rows=[]
        for index,x in enumerate(sorted(all_findings,key=lambda x:(x['sheet'],*_sort(x))),start=1):
            coords=[(r,c) for r,c in finding_coordinates(x,x.get('reviewed_columns') or ())
                    if type(r) is int and type(c) is int and 0<r<=1048576 and 0<c<=16384]
            cells=[f'{get_column_letter(c)}{r}' for r,c in coords]
            values=[]
            for r,c in coords:
                value=wb[x['sheet']].cell(r,c).value
                text='' if value is None else str(value)
                if text not in values:values.append(text)
            column_names=[str((x.get('columns') or {}).get(c) or c) for c in
                          dict.fromkeys(c for _,c in coords)]
            label,_,action=_kind(str(x.get('check','')))
            evidence=x.get('evidence') or {}
            issue_rows.append([index,x['sheet'],x.get('region_id',''),','.join(cells)[:32000],
                ','.join(str(v) for v in dict.fromkeys(r for r,_ in coords)),
                ','.join(column_names)[:32000],','.join(str(c) for c in dict.fromkeys(c for _,c in coords)),
                label,x.get('severity',''),' | '.join(values)[:32000],x.get('check',''),
                str(x.get('detail',''))[:32000],action,x.get('layer') or
                ('manual_or_model_annotation' if x.get('check')=='row_annotation' else 'table_rule'),
                json.dumps(evidence,ensure_ascii=False,default=_json_default)[:32000]])
        appendix('问题清单',['序号','工作表','区域','单元格','行','列','列索引','问题类型',
            '严重程度','原值','检测规则','说明','建议核查动作','结果层','计算/定位证据（长内容需读完整结果）'],
            issue_rows)
    if overview:
        appendix('审查概览',['项目','值'],[['原始 SHA-256',source_sha256],['状态',status],
            ['已解析表',len(entries)],['发现',len(all_findings)],['限制','发现仅为复核线索，不是科研不端判定']])
    if rules:
        appendix('审查规则',['工作表','区域','规则','状态','方法版本','已检查','跳过','原因/范围'],
            [[entry['table']['sheet'],entry['table'].get('region_id',''),key,rule['status'],rule.get('method_version',''),rule.get('tested',0),rule.get('skipped',0),
              json.dumps(rule,ensure_ascii=False,default=_json_default)[:32000]]
             for entry in entries for key,rule in entry['result']['data']['rule_execution'].get('rules', {}).items()])
    after=_data_signature(wb,bounds)
    if after!=before:raise ValueError('Export changed original values, types, formats or merges')
    output=io.BytesIO();wb.save(output);blob=output.getvalue()
    if original:
        blob=_restore_original_cells(source_bytes,blob)
    reopened=load_workbook(io.BytesIO(blob),keep_vba=keep_vba,rich_text=True)
    try:
        if _data_signature(reopened,bounds) != before or reopened.sheetnames[:len(original_names)] != original_names:
            raise ValueError('Serialized export changed original data')
        if not explain_visible and any((reopened[name].max_row,reopened[name].max_column)!=extent for name,extent in bounds.items()):
            raise ValueError('Serialized export expanded original data region')
    finally:
        reopened.close()
        wb.close()
    return blob,{'version':'2.0.0','source_sha256':source_sha256,'export_sha256':hashlib.sha256(blob).hexdigest(),
        'original_sheets':original_names,'added_sheets':added,'explanation_blocks':explanations,
        'original_data_sha256':before,'exported_data_sha256':after,'data_region_unchanged':True,
        'marked_locations':len(references),'marked_cells':len(marks),'findings_references':references,'scan_reused':True,
        'source_representation':'original_workbook' if original else 'table_snapshot_conversion'}
