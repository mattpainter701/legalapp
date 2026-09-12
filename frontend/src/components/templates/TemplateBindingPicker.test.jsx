import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import TemplateBindingPicker, { bindingSummary, customFieldCards } from './TemplateBindingPicker'

const clientCard = {
  key: 'client', label: 'Client', kind: 'person', group: 'Client',
  max_instances: 1, instance_count: null,
  fields: [{ key: 'full_name', label: 'Client name', path: 'client.full_name', value_kind: 'text', supports_all_instances: false }],
}

const customBindings = [
  { path: 'custom.matter.abc', label: 'Marriage date', group: 'Matter details', field_type: 'date' },
  { path: 'custom.contact.def', label: 'Preferred name', group: 'Client details', field_type: 'text' },
  { path: 'client.name', label: 'Client name', group: 'Client' },
]

afterEach(cleanup)

describe('customFieldCards', () => {
  it('groups only custom fields, keeping their real identity', () => {
    const cards = customFieldCards(customBindings)
    expect(cards.map((card) => card.label)).toEqual(['Matter details', 'Client details'])
    // The pseudo-card exists for display; the path must stay the one the
    // server validates, not one derived from the card key.
    expect(cards[0].fields[0].path).toBe('custom.matter.abc')
  })

  it('ignores built-in bindings, which cards already cover', () => {
    expect(customFieldCards([{ path: 'client.name', label: 'x', group: 'Client' }])).toEqual([])
  })

  it('tolerates a catalogue that failed to load', () => {
    expect(customFieldCards()).toEqual([])
    expect(customFieldCards([null, {}])).toEqual([])
  })
})

describe('bindingSummary', () => {
  it('states each choice in plain words', () => {
    expect(bindingSummary('', [clientCard])).toBe('Matched by field name')
    expect(bindingSummary('manual', [clientCard])).toBe('Always typed by hand')
    expect(bindingSummary('client.full_name', [clientCard])).toBe('Client — Client name')
  })

  it('shows an unrecognised path verbatim rather than calling it unknown', () => {
    // An instance path, or one the catalogue no longer describes. The author
    // can see what the template actually asks for and judge whether it holds.
    expect(bindingSummary('defendant.2.full_name', [clientCard])).toBe('defendant.2.full_name')
  })
})

describe('TemplateBindingPicker', () => {
  const setup = (value = '') => {
    const onChange = vi.fn()
    render(<TemplateBindingPicker value={value} cards={[clientCard]} bindings={customBindings} onChange={onChange} />)
    return onChange
  }

  it('stays collapsed until asked to change', async () => {
    setup()
    expect(screen.getByText('Matched by field name')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Client' })).toBeNull()
    await userEvent.click(screen.getByText('Matched by field name'))
    expect(screen.getByRole('button', { name: 'Client' })).toBeTruthy()
  })

  it('emits the card path for a chosen field', async () => {
    const onChange = setup()
    await userEvent.click(screen.getByText('Matched by field name'))
    await userEvent.click(screen.getByRole('button', { name: 'Client' }))
    await userEvent.click(screen.getByRole('button', { name: 'Client name' }))
    expect(onChange).toHaveBeenCalledWith('client.full_name')
  })

  it('emits a custom field under its real path', async () => {
    const onChange = setup()
    await userEvent.click(screen.getByText('Matched by field name'))
    await userEvent.click(screen.getByRole('button', { name: 'Matter details' }))
    await userEvent.click(screen.getByRole('button', { name: 'Marriage date' }))
    expect(onChange).toHaveBeenCalledWith('custom.matter.abc')
  })

  it('restores name matching by removing the binding, not by storing an empty one', async () => {
    // A template with no binding has always been stored without the key; an
    // empty string would be a third state the server has never seen.
    const onChange = setup('manual')
    await userEvent.click(screen.getByText('Always typed by hand'))
    await userEvent.click(screen.getByRole('button', { name: /Matched by field name/ }))
    expect(onChange).toHaveBeenCalledWith(undefined)
  })

  it('suppresses name matching when the field is typed by hand', async () => {
    const onChange = setup()
    await userEvent.click(screen.getByText('Matched by field name'))
    await userEvent.click(screen.getByRole('button', { name: /Always typed by hand/ }))
    expect(onChange).toHaveBeenCalledWith('manual')
  })

  it('closes once a choice is made', async () => {
    setup()
    await userEvent.click(screen.getByText('Matched by field name'))
    await userEvent.click(screen.getByRole('button', { name: 'Client' }))
    await userEvent.click(screen.getByRole('button', { name: 'Client name' }))
    expect(screen.queryByRole('button', { name: 'Client' })).toBeNull()
  })

  it('degrades to name matching when the catalogue could not be loaded', async () => {
    render(<TemplateBindingPicker value="" cards={[]} bindings={[]} onChange={vi.fn()} />)
    await userEvent.click(screen.getByText('Matched by field name'))
    expect(screen.getByText(/could not be loaded/)).toBeTruthy()
  })
})
