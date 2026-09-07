import { fieldIdentity, VARIABLE_NAME_PATTERN } from './pdfFieldGeometry'

// Literal tokens and unambiguous, explicitly mapped source text. Anchored
// ranges stay in the text view when repeated prose makes their location unclear.
export function wordPlaceholderMatches(strings, fields) {
  const definitions = new Map()
  fields.forEach((field, index) => {
    if (!field || field.included === false || !VARIABLE_NAME_PATTERN.test(field.name || '')) return
    const entries = definitions.get(field.name) || []
    entries.push({ field, identity: fieldIdentity(field, index) })
    definitions.set(field.name, entries)
  })
  const text = strings.join('')
  if (text.length > 500_000) return []
  const matches = [...text.matchAll(/\{\{([A-Za-z][A-Za-z0-9_.-]*)\}\}/g)].slice(0, 500).flatMap(match => {
    const entries = definitions.get(match[1])
    if (entries?.length !== 1) return []
    return [{ ...entries[0], start: match.index, end: match.index + match[0].length }]
  })
  for (const entries of definitions.values()) {
    if (entries.length !== 1 || matches.length >= 500) continue
    const entry = entries[0]
    const source = entry.field.source_text
    if (typeof source !== 'string' || !source.trim() || source.length > 2000 || source.includes('{{') || entry.field.docx_anchor) continue
    // Anchors refer to Word paragraphs, whose page can change after reflow.
    // Their exact location remains available in Fields, never guessed here.
    const first = text.indexOf(source)
    for (let start = first; start >= 0 && matches.length < 500; start = text.indexOf(source, start + source.length)) {
      matches.push({ ...entry, start, end: start + source.length })
    }
  }
  return matches.filter((match, index) => !matches.some((other, otherIndex) => otherIndex !== index
    && other.identity !== match.identity && other.start < match.end && match.start < other.end))
    .sort((a, b) => a.start - b.start)
}

export function placeholderRange(textDivs, start, end, ownerDocument) {
  const ranges = []
  let offset = 0
  for (const div of textDivs) {
    const node = div.firstChild
    const length = node?.nodeType === 3 ? node.textContent.length : 0
    if (length && start < offset + length && end > offset) {
      // One range per text node avoids a browser returning both an intermediate
      // span's element rectangle and its differently sized glyph rectangle.
      const range = ownerDocument.createRange()
      range.setStart(node, Math.max(0, start - offset))
      range.setEnd(node, Math.min(length, end - offset))
      ranges.push(range)
    }
    if (ranges.length && end <= offset + length) {
      return {
        getClientRects: () => ranges.flatMap(range => [...range.getClientRects()]),
        toString: () => ranges.map(range => range.toString()).join(''),
      }
    }
    offset += length
  }
  return null
}

// Bounding rectangles come from pdf.js's laid-out text, not a guessed
// character width. Refuse disconnected fragments (columns / separate lines).
export function placeholderBoxes(range, pageRect) {
  if (!range) return []
  const boxes = [...range.getClientRects()].filter((rect, index, all) => rect.width > 0 && rect.height > 0
    && !all.slice(0, index).some(previous => ['left', 'top', 'width', 'height'].every(key => Math.abs(previous[key] - rect[key]) < 0.25)))
  if (!boxes.length || boxes.length > 30) return []
  const first = boxes[0]
  const valid = boxes.every((box, index) => {
    const previous = boxes[index - 1]
    return [box.left, box.top, box.width, box.height].every(Number.isFinite)
      && box.left >= pageRect.left - 1 && box.top >= pageRect.top - 1
      && box.right <= pageRect.right + 1 && box.bottom <= pageRect.bottom + 1
      && Math.abs(box.top - first.top) <= Math.max(2, first.height * 0.35)
      && (!previous || Math.abs(box.left - previous.right) <= Math.max(4, box.height * 0.5))
  })
  if (!valid) return []
  return boxes.map(box => ({ left: box.left - pageRect.left, top: box.top - pageRect.top, width: box.width, height: box.height }))
}
