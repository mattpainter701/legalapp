import { describe, expect, it } from 'vitest'
import { placeholderBoxes, placeholderRange, resolveWordPageSelection, wordPlaceholderMatches } from './wordPlaceholderMatches'

describe('literal Word placeholder matching', () => {
  it('highlights explicitly detected source text, including repeated replacements', () => {
    const fields = [{ name: 'client', source_text: 'Ada Lovelace' }]
    expect(wordPlaceholderMatches(['Dear Ada ', 'Lovelace. Ada Lovelace signs.'], fields).map(match => match.start)).toEqual([5, 19])
  })

  it('does not bind ambiguous source text to competing fields or repeated anchors', () => {
    expect(wordPlaceholderMatches(['Amount Amount'], [{ name: 'first', source_text: 'Amount', docx_anchor: { paragraph_ordinal: 0, start: 0, end: 6 } }])).toEqual([])
    expect(wordPlaceholderMatches(['Ada Lovelace'], [{ name: 'first', source_text: 'Ada Lovelace' }, { name: 'second', source_text: 'Ada' }])).toEqual([])
  })
  it('finds split-run and repeated literal tokens without binding ordinary text', () => {
    const fields = [{ name: 'client_name', label: 'Client' }, { name: 'amount' }]
    const matches = wordPlaceholderMatches(['Dear {{cli', 'ent_name}}, {{amount}}; {{client_name}} and unknown {{other}}.'], fields)
    expect(matches.map(match => match.field.name)).toEqual(['client_name', 'amount', 'client_name'])
    expect(matches[0]).toMatchObject({ start: 5, end: 20 })
  })

  it('leaves conflicting definitions, excluded fields and invalid names unbound', () => {
    const fields = [{ name: 'client_name' }, { name: 'client_name' }, { name: 'amount', included: false }, { name: '9invalid' }]
    expect(wordPlaceholderMatches(['{{client_name}} {{amount}} {{9invalid}}'], fields)).toEqual([])
    expect(wordPlaceholderMatches(['{{client.name}} {{due-date}}'], [{ name: 'client.name' }, { name: 'due-date' }]).map(match => match.field.name)).toEqual(['client.name', 'due-date'])
  })

  it('creates exact DOM ranges across text spans, including an empty item', () => {
    const divs = ['Dear {{cli', '', 'ent_name}},'].map(text => {
      const div = document.createElement('span')
      div.textContent = text
      return div
    })
    const host = document.createElement('div')
    host.append(...divs)
    const range = placeholderRange(divs, 5, 20, document)
    expect(range.toString()).toBe('{{client_name}}')
    expect(placeholderRange(divs, 100, 120, document)).toBeNull()
  })

  it('bounds work for enormous or pathological text pages', () => {
    expect(wordPlaceholderMatches(['x'.repeat(500_001)], [{ name: 'x' }])).toEqual([])
    expect(wordPlaceholderMatches(['{{x}}'.repeat(600)], [{ name: 'x' }])).toHaveLength(500)
  })

  it('uses unique paragraph context for anchored fields, including repeated blanks', () => {
    const fields = [{ name: 'signature', source_text: '___', docx_anchor: { paragraph_ordinal: 1, start: 18, end: 21 } }]
    const paragraphs = [{ ordinal: 0, text: 'Signature: ___' }, { ordinal: 1, text: 'Client signature: ___' }]
    expect(wordPlaceholderMatches(['Signature: ___ Client signature: ___'], fields, paragraphs).map(({ start, end }) => [start, end])).toEqual([[33, 36]])
  })

  it('refuses anchored matches when the same outline paragraph is duplicated', () => {
    const fields = [{ name: 'fee', source_text: '___', docx_anchor: { paragraph_ordinal: 1, start: 5, end: 8 } }]
    const paragraphs = [{ ordinal: 0, text: 'Fee: ___' }, { ordinal: 1, text: 'Fee: ___' }]
    expect(wordPlaceholderMatches(['Fee: ___'], fields, paragraphs)).toEqual([])
  })

  it('does not mark a different page whose paragraph contains the target paragraph', () => {
    const fields = [{ name: 'fee', source_text: '___', docx_anchor: { paragraph_ordinal: 0, start: 5, end: 8 } }]
    const paragraphs = [{ ordinal: 0, text: 'Fee: ___' }, { ordinal: 1, text: 'Annual Fee: ___' }]
    expect(wordPlaceholderMatches(['Annual Fee: ___'], fields, paragraphs)).toEqual([])
  })

  it('refuses ambiguous paragraph context and invalid or cross-paragraph anchors', () => {
    const paragraphs = [{ ordinal: 2, text: 'Fee: ___' }, { ordinal: 3, text: 'Fee: ___' }]
    expect(resolveWordPageSelection('Fee: ___', paragraphs)).toBeNull()
    expect(resolveWordPageSelection('Fee: ___\nOther', [{ ordinal: 2, text: 'Fee: ___' }, { ordinal: 3, text: 'Other' }])).toBeNull()
    expect(resolveWordPageSelection('Missing', paragraphs)).toBeNull()
  })

  it('returns Unicode codepoint offsets for an anchored selection', () => {
    expect(resolveWordPageSelection('Fee: ___', [{ ordinal: 4, text: '😀 Fee: ___' }])).toEqual({ ordinal: 4, start: 2, end: 10, text: 'Fee: ___' })
  })

  it('resolves a selected substring uniquely within one paragraph', () => {
    expect(resolveWordPageSelection('Ada Lovelace', [{ ordinal: 2, text: 'Dear Ada Lovelace,' }])).toEqual({ ordinal: 2, start: 5, end: 17, text: 'Ada Lovelace' })
    expect(resolveWordPageSelection('Ada', [{ ordinal: 2, text: 'Ada met Ada.' }])).toBeNull()
  })

  it('resolves Unicode selected substrings after an emoji prefix', () => {
    expect(resolveWordPageSelection('Fee: ___', [{ ordinal: 4, text: '😀 Fee: ___' }])).toEqual({ ordinal: 4, start: 2, end: 10, text: 'Fee: ___' })
  })

  it('maps expanded PDF whitespace and Unicode offsets without moving an anchor', () => {
    const paragraphs = [{ ordinal: 1, text: '😀 Fee: ___' }]
    const fields = [{ name: 'fee', source_text: '___', docx_anchor: { paragraph_ordinal: 1, start: 7, end: 10 } }]
    expect(wordPlaceholderMatches(['😀  Fee: ', '___'], fields, paragraphs).map(({ start, end }) => [start, end])).toEqual([[9, 12]])
    expect(wordPlaceholderMatches(['😀 Fee: ___'], [{ ...fields[0], source_text: 'XYZ' }], paragraphs)).toEqual([])
    expect(wordPlaceholderMatches(['😀 Fee: ___'], [{ ...fields[0], docx_anchor: { paragraph_ordinal: 1, start: -1, end: 10 } }], paragraphs)).toEqual([])
  })

  it('refuses overlapping repeated selections and does not duplicate a literal anchored box', () => {
    expect(resolveWordPageSelection('aaa', [{ ordinal: 0, text: 'aaaa' }])).toBeNull()
    const fields = [{ name: 'fee', source_text: '{{fee}}', docx_anchor: { paragraph_ordinal: 1, start: 0, end: 7 } }]
    expect(wordPlaceholderMatches(['{{fee}}'], fields, [{ ordinal: 1, text: '{{fee}}' }])).toHaveLength(1)
  })
})

const rect = (left, top, width, height) => ({ left, top, width, height, right: left + width, bottom: top + height })
const page = rect(100, 200, 612, 792)
const range = boxes => ({ getClientRects: () => boxes })

describe('rendered placeholder bounds', () => {
  it('uses measured positions for adjacent split runs and ignores duplicate element ranges', () => {
    const first = rect(120, 240, 40, 12)
    expect(placeholderBoxes(range([first, first, rect(160, 240, 30, 12)]), page)).toEqual([
      { left: 20, top: 40, width: 40, height: 12 }, { left: 60, top: 40, width: 30, height: 12 },
    ])
  })

  it.each([
    [rect(120, 240, 40, 12), rect(350, 240, 30, 12)],
    [rect(120, 240, 40, 12), rect(120, 280, 30, 12)],
    [rect(95, 240, 40, 12)],
    [rect(120, 240, NaN, 12)],
  ])('refuses ambiguous or invalid geometry %#', (...boxes) => {
    expect(placeholderBoxes(range(boxes), page)).toEqual([])
  })
})
