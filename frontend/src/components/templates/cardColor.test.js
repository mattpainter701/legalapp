import { describe, expect, it } from 'vitest'

import { cardHue, cardKeyForBinding, cardStyle } from './cardColor'

const cards = [
  {
    key: 'client',
    fields: [{ key: 'full_name', path: 'client.full_name', legacy_paths: ['client.name'] }],
  },
  {
    key: 'defendant',
    fields: [{ key: 'full_name', path: 'defendant.full_name', legacy_paths: ['party.defendant.name', 'party.defendant.names'] }],
  },
]

describe('cardStyle', () => {
  it('exposes one hue as the three values every surface reads', () => {
    const style = cardStyle('client')
    expect(style['--card-hue']).toBe(String(cardHue('client')))
    expect(style['--card-accent']).toContain(String(cardHue('client')))
    expect(style['--card-wash']).toContain(String(cardHue('client')))
  })
})

describe('cardKeyForBinding', () => {
  it('resolves a card path', () => {
    expect(cardKeyForBinding('client.full_name', cards)).toBe('client')
  })

  it('resolves a pre-card path a published template still carries', () => {
    // The server sends legacy_paths precisely so this works without the client
    // keeping a second copy of the legacy table.
    expect(cardKeyForBinding('party.defendant.name', cards)).toBe('defendant')
    expect(cardKeyForBinding('client.name', cards)).toBe('client')
  })

  it('resolves an instance path to the same card as the first instance', () => {
    // The second defendant is the same subject as the first; a different
    // colour would say otherwise.
    expect(cardKeyForBinding('defendant.2.full_name', cards)).toBe('defendant')
    expect(cardKeyForBinding('defendant.*.full_name', cards)).toBe('defendant')
  })

  it('gives no card to a field that is not bound to one', () => {
    expect(cardKeyForBinding('', cards)).toBe('')
    expect(cardKeyForBinding('manual', cards)).toBe('')
    expect(cardKeyForBinding('custom.matter.abc', cards)).toBe('')
    expect(cardKeyForBinding('invented.path', cards)).toBe('')
  })

  it('tolerates a catalogue that failed to load', () => {
    expect(cardKeyForBinding('client.full_name')).toBe('')
    expect(cardKeyForBinding('client.full_name', [{ key: 'x' }])).toBe('')
  })
})
