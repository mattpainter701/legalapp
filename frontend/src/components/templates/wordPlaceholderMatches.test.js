import { describe, expect, it } from 'vitest'
import { placeholderBoxes, placeholderRange, wordPlaceholderMatches } from './wordPlaceholderMatches'

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
