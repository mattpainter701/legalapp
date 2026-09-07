"""Source display order is independent from persisted paragraph ordinals."""

from docx.oxml.ns import qn
from app.services.docx_templates import docx_story_containers


def display_blocks(document, ordinals):
    def blocks(root):
        result = []
        for child in root:
            if child.tag == qn("w:p"):
                if child in ordinals:
                    result.append({"kind": "paragraph", "ordinal": ordinals[child]})
            elif child.tag == qn("w:tbl"):
                rows, merged = [], {}
                for row in child.findall(qn("w:tr")):
                    cells, column = [], 0
                    for cell in row.findall(qn("w:tc")):
                        span = cell.find(
                            "w:tcPr/w:gridSpan", {"w": qn("w:p").split("}")[0][1:]}
                        )
                        colspan = (
                            max(1, min(100, int(span.get(qn("w:val"), 1))))
                            if span is not None
                            else 1
                        )
                        vertical = cell.find(
                            "w:tcPr/w:vMerge", {"w": qn("w:p").split("}")[0][1:]}
                        )
                        if (
                            vertical is not None
                            and vertical.get(qn("w:val")) != "restart"
                            and column in merged
                        ):
                            merged[column]["rowspan"] += 1
                        else:
                            item = {
                                "blocks": blocks(cell),
                                "colspan": colspan,
                                "rowspan": 1,
                            }
                            cells.append(item)
                            if vertical is not None:
                                merged[column] = item
                            else:
                                merged.pop(column, None)
                        column += colspan
                    rows.append({"cells": cells})
                result.append({"kind": "table", "rows": rows})
            elif child.tag in {qn("w:sdt"), qn("w:sdtContent"), qn("w:customXml")}:
                result.extend(blocks(child))
        return result

    result = blocks(document.element.body)
    seen = set()
    for variants in (False, True):
        for container in docx_story_containers(document, variants=variants):
            if container._element not in seen:
                seen.add(container._element)
                result.extend(blocks(container._element))
    return result


def paragraph_numbering(document, paragraphs):
    """Resolve common Word list labels without inserting them into source text."""
    try:
        numbering = document.part.numbering_part.element
    except (KeyError, NotImplementedError):
        return {}
    nums = {
        int(node.get(qn("w:numId"))): node for node in numbering.findall(qn("w:num"))
    }
    abstracts = {
        int(node.get(qn("w:abstractNumId"))): node
        for node in numbering.findall(qn("w:abstractNum"))
    }
    counters, labels = {}, {}

    def number(value, fmt):
        if not 1 <= value <= 3999:
            return str(value)
        if fmt in {"lowerLetter", "upperLetter"}:
            result = ""
            while value > 0:
                value, rem = divmod(value - 1, 26)
                result = chr(97 + rem) + result
            return result.upper() if fmt == "upperLetter" else result
        if fmt in {"lowerRoman", "upperRoman"}:
            result = ""
            for amount, symbol in [
                (1000, "M"),
                (900, "CM"),
                (500, "D"),
                (400, "CD"),
                (100, "C"),
                (90, "XC"),
                (50, "L"),
                (40, "XL"),
                (10, "X"),
                (9, "IX"),
                (5, "V"),
                (4, "IV"),
                (1, "I"),
            ]:
                count, value = divmod(value, amount)
                result += symbol * count
            return result.lower() if fmt == "lowerRoman" else result
        return str(value)

    for paragraph in paragraphs:
        properties = paragraph._p.pPr
        numpr = properties.numPr if properties is not None else None
        style = paragraph.style
        visited = set()
        while numpr is None and style is not None and style.style_id not in visited:
            visited.add(style.style_id)
            props = style.element.pPr
            numpr = props.numPr if props is not None else None
            style = style.base_style
        if numpr is None or numpr.numId is None:
            continue
        numid = numpr.numId.val
        level = numpr.ilvl.val if numpr.ilvl is not None else 0
        num = nums.get(numid)
        if num is None:
            continue
        abstract_id = num.find(qn("w:abstractNumId"))
        abstract = (
            abstracts.get(int(abstract_id.get(qn("w:val"))))
            if abstract_id is not None
            else None
        )
        if abstract is None:
            continue
        levels = {
            int(node.get(qn("w:ilvl"))): node for node in abstract.findall(qn("w:lvl"))
        }
        lvl = levels.get(level)
        if lvl is None:
            continue
        start = lvl.find(qn("w:start"))
        initial = int(start.get(qn("w:val"), 1)) if start is not None else 1
        for override in num.findall(qn("w:lvlOverride")):
            if int(override.get(qn("w:ilvl"))) == level:
                start_override = override.find(qn("w:startOverride"))
                if start_override is not None:
                    initial = int(start_override.get(qn("w:val")))
        state = counters.setdefault(numid, {})
        state[level] = state.get(level, initial - 1) + 1
        for deeper in list(state):
            if deeper > level:
                del state[deeper]
        text = lvl.find(qn("w:lvlText"))
        label = text.get(qn("w:val"), "") if text is not None else ""
        for index, definition in levels.items():
            fmt = definition.find(qn("w:numFmt"))
            label = label.replace(
                f"%{index + 1}",
                number(
                    state.get(index, 1),
                    fmt.get(qn("w:val"), "decimal") if fmt is not None else "decimal",
                ),
            )
        labels[paragraph._p] = label
    return labels
