import { fieldIdentity, VARIABLE_NAME_PATTERN } from './pdfFieldGeometry'

// Literal tokens and unambiguous, explicitly mapped source text. Anchored
// ranges stay in the text view when repeated prose makes their location unclear.
const asCodepoints = (value) => Array.from(value)

const anchorFor = (paragraph) => paragraph?.anchor || paragraph?.docx_anchor || paragraph?.selection || paragraph

// Resolve selected page text into one Word source span. Word offsets count
// Unicode codepoints; JavaScript and DOM indexes count UTF-16 code units.
export function resolveWordPageSelection(text, paragraphs = []) {
  if (typeof text !== 'string' || !text || !Array.isArray(paragraphs) || !paragraphs.length) return null
  const selected = asCodepoints(text)
  const selections = []
  for (const paragraph of paragraphs) {
    if (!paragraph || !Number.isInteger(Number(paragraph.ordinal)) || typeof paragraph.text !== 'string' || !paragraph.text) continue
    let from = 0
    while (from <= paragraph.text.length) {
      const position = paragraph.text.indexOf(text, from)
      if (position < 0) break
      const prefixLength = asCodepoints(paragraph.text.slice(0, position)).length
      selections.push({ ordinal: Number(paragraph.ordinal), start: prefixLength, end: prefixLength + selected.length, text })
      if (selections.length > 1) return null
      from = position + 1
    }
  }
  return selections.length === 1 ? selections[0] : null
}

function pageAnchorMatch(text, paragraphs = []) {
  if (typeof text !== 'string' || !Array.isArray(paragraphs) || !paragraphs.length) return null
  const validParagraphs = paragraphs.filter(paragraph => paragraph
    && Number.isInteger(Number(paragraph.ordinal)) && typeof paragraph.text === 'string' && paragraph.text)
  if (!validParagraphs.length) return null
  const anchoredParagraphs = validParagraphs.filter(paragraph => {
    const anchor = anchorFor(paragraph)
    return anchor && (anchor.start !== undefined || anchor.end !== undefined)
  })
  const candidates = anchoredParagraphs.length ? anchoredParagraphs : validParagraphs
  const selections = []
  for (const paragraph of candidates) {
    const selection = resolveWordPageParagraph(text, paragraph)
    if (selection) selections.push(selection)
    if (selections.length > 1) return null
  }
  return selections.length === 1 ? selections[0] : null
}

function resolveWordPageParagraph(text, paragraph) {
  const ordinal = Number(paragraph.ordinal)
  const anchor = anchorFor(paragraph)
  const sourcePoints = asCodepoints(paragraph.text)
  const start = anchor?.start === undefined ? 0 : Number(anchor.start)
  const end = anchor?.end === undefined ? sourcePoints.length : Number(anchor.end)
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start || end > sourcePoints.length) return null

  const occurrences = []
  let from = 0
  while (from <= text.length) {
    const position = text.indexOf(paragraph.text, from)
    if (position < 0) break
    occurrences.push({ start: position, end: position + paragraph.text.length, map: null })
    if (occurrences.length > 1) return null
    from = position + 1
  }

  // pdf.js normally preserves spaces in textContentItemsStr. If a producer
  // split or expanded whitespace between items, permit that exact paragraph
  // once, while retaining a precise source-to-page offset map.
  if (!occurrences.length) {
    const parts = paragraph.text.split(/(\s+)/u).filter(Boolean)
    const pattern = parts.map(part => /\s/u.test(part) ? '\\s+' : part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('')
    const expression = new RegExp(pattern, 'gu')
    let match
    while ((match = expression.exec(text))) {
      occurrences.push({ start: match.index, end: match.index + match[0].length, map: match[0] })
      if (occurrences.length > 1) return null
    }
  }
  if (occurrences.length !== 1) return null

  const occurrence = occurrences[0]
  const pagePoints = asCodepoints(text.slice(0, occurrence.start))
  let selectedStart = pagePoints.length + start
  let selectedEnd = pagePoints.length + end
  if (occurrence.map) {
    const matchedPoints = asCodepoints(occurrence.map)
    const offsets = [0]
    let target = 0
    for (const point of sourcePoints) {
      if (/\s/u.test(point)) {
        while (target < matchedPoints.length && /\s/u.test(matchedPoints[target])) target += 1
      } else {
        if (target >= matchedPoints.length || matchedPoints[target] !== point) return null
        target += 1
      }
      offsets.push(target)
    }
    selectedStart = pagePoints.length + offsets[start]
    selectedEnd = pagePoints.length + offsets[end]
  }
  const selectedText = asCodepoints(text).slice(selectedStart, selectedEnd).join('')
  if (!selectedText || selectedEnd <= selectedStart) return null
  return { ordinal, start: selectedStart, end: selectedEnd, text: selectedText }
}

export function wordPlaceholderMatches(strings, fields, paragraphs = []) {
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
    const anchor = entry.field.docx_anchor
    if (!anchor) continue
    const outline = paragraphs || []
    const paragraph = outline.find(item => Number(item?.ordinal) === Number(anchor.paragraph_ordinal))
    const normalized = paragraph?.text?.replace(/\s+/gu, ' ').trim()
    if (!paragraph || outline.some(item => item !== paragraph && item?.text?.replace(/\s+/gu, ' ').trim() === normalized)) continue
    const anchorStart = Number(anchor.start)
    const anchorEnd = Number(anchor.end)
    const paragraphText = typeof paragraph.text === 'string' ? asCodepoints(paragraph.text) : []
    if (typeof entry.field.source_text === 'string'
      && asCodepoints(entry.field.source_text).join('') !== paragraphText.slice(anchorStart, anchorEnd).join('')) continue
    const selection = pageAnchorMatch(text, [{ ...paragraph, anchor }])
    if (!selection) continue
    const start = asCodepoints(text).slice(0, selection.start).join('').length
    const end = asCodepoints(text).slice(0, selection.end).join('').length
    matches.push({ ...entry, start, end })
  }
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
  return matches.filter((match, index) => !matches.slice(0, index).some(other => other.identity === match.identity && other.start === match.start && other.end === match.end))
    .filter((match, index, unique) => !unique.some((other, otherIndex) => otherIndex !== index
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
