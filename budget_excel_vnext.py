"""Dependency-free XLSX export for the read-only budget screen.

The runtime intentionally avoids adding a heavy spreadsheet dependency on the
256 MiB tier. This writer emits a small standards-compliant OOXML workbook with
inline strings and numeric money/percent cells.
"""
from __future__ import annotations

import io
import re
import zipfile
from xml.sax.saxutils import escape

_INVALID_XML = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")


def _clean(value):
    return _INVALID_XML.sub("", str(value or ""))


def _column_name(index):
    value = int(index)
    chars = []
    while value:
        value, remainder = divmod(value - 1, 26)
        chars.append(chr(65 + remainder))
    return "".join(reversed(chars))


def _inline_cell(ref, value, style=0):
    text = escape(_clean(value))
    style_attr = f' s="{int(style)}"' if int(style) else ""
    return (
        f'<c r="{ref}" t="inlineStr"{style_attr}>'
        f'<is><t xml:space="preserve">{text}</t></is></c>'
    )


def _number_cell(ref, value, style=0):
    style_attr = f' s="{int(style)}"' if int(style) else ""
    number = float(value or 0)
    if number.is_integer():
        rendered = str(int(number))
    else:
        rendered = repr(number)
    return f'<c r="{ref}"{style_attr}><v>{rendered}</v></c>'


def _sheet_title(value):
    title = re.sub(r'[:\\/?*\[\]]', " ", _clean(value)).strip() or "예산사업"
    return title[:31]


def build_budget_xlsx(rows, *, sheet_name="예산사업"):
    """Build an XLSX workbook for normalized budget rows."""
    columns = (
        ("연도", "fiscal_year", "number"),
        ("지역", "region_display", "text"),
        ("기관", "org_display", "text"),
        ("담당부서", "dept_name", "text"),
        ("사업명", "project_name", "text"),
        ("사업코드", "project_code", "text"),
        ("분야", "field_name", "text"),
        ("부문", "section_name", "text"),
        ("회계", "account_name", "text"),
        ("분류", "category_label", "text"),
        ("예산액", "budget_amount", "money"),
        ("집행액", "executed_amount", "money"),
        ("잔액", "remaining_amount", "money"),
        ("집행률", "execution_rate", "percent"),
        ("기준일", "snapshot_date", "text"),
    )
    prepared = [dict(row or {}) for row in rows]
    xml_rows = []

    header_cells = [
        _inline_cell(f"{_column_name(index)}1", label, style=1)
        for index, (label, _key, _kind) in enumerate(columns, 1)
    ]
    xml_rows.append(f'<row r="1" ht="22" customHeight="1">{"".join(header_cells)}</row>')

    for row_no, row in enumerate(prepared, 2):
        cells = []
        for col_no, (_label, key, kind) in enumerate(columns, 1):
            ref = f"{_column_name(col_no)}{row_no}"
            value = row.get(key)
            if kind == "money":
                cells.append(_number_cell(ref, int(value or 0), style=2))
            elif kind == "percent":
                cells.append(_number_cell(ref, float(value or 0), style=3))
            elif kind == "number":
                cells.append(_number_cell(ref, int(value or 0)))
            else:
                cells.append(_inline_cell(ref, value))
        xml_rows.append(f'<row r="{row_no}">{"".join(cells)}</row>')

    last_row = max(1, len(prepared) + 1)
    last_col = _column_name(len(columns))
    title = _sheet_title(sheet_name)
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        '</sheetView></sheetViews>'
        '<cols>'
        '<col min="1" max="1" width="8" customWidth="1"/>'
        '<col min="2" max="2" width="15" customWidth="1"/>'
        '<col min="3" max="4" width="24" customWidth="1"/>'
        '<col min="5" max="5" width="42" customWidth="1"/>'
        '<col min="6" max="10" width="18" customWidth="1"/>'
        '<col min="11" max="13" width="18" customWidth="1"/>'
        '<col min="14" max="15" width="14" customWidth="1"/>'
        '</cols>'
        f'<sheetData>{"".join(xml_rows)}</sheetData>'
        f'<autoFilter ref="A1:{last_col}{last_row}"/>'
        '</worksheet>'
    )

    styles_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="2">
<numFmt numFmtId="164" formatCode="#,##0&quot;원&quot;"/>
<numFmt numFmtId="165" formatCode="0.0%"/>
</numFmts>
<fonts count="2">
<font><sz val="11"/><name val="Calibri"/></font>
<font><b/><sz val="11"/><name val="Calibri"/></font>
</fonts>
<fills count="2">
<fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFDCE6F1"/><bgColor indexed="64"/></patternFill></fill>
</fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="4">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="1" borderId="0" xfId="0" applyFont="1" applyFill="1"><alignment horizontal="center"/></xf>
<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{escape(title)}" sheetId="1" r:id="rId1"/></sheets>'
        '</workbook>'
    )
    workbook_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''
    root_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>'''

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/styles.xml", styles_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return output.getvalue()
