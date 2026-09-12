import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({ cancel: vi.fn(), fail: false }))
vi.mock('pdfjs-dist/legacy/build/pdf.mjs', () => ({
  TextLayer: class {
    constructor({ container, textContentSource }) {
      this.textDivs = textContentSource.items.map(item => {
        const div = document.createElement('span')
        div.textContent = item.str
        container.append(div)
        return div
      })
      this.textContentItemsStr = textContentSource.items.map(item => item.str)
    }
    render() { return state.fail ? Promise.reject(new Error('optional text unavailable')) : Promise.resolve() }
    cancel() { state.cancel() }
  },
}))

import WordPlaceholderLayer from './WordPlaceholderLayer'

const fields = [{ name: 'client_name', label: 'Client name' }]
const viewport = { scale: 1 }
const pdf = { getPage: vi.fn(async () => ({ getTextContent: async () => ({ items: [{ str: '{{client_name}}' }] }) })) }

beforeEach(() => {
  state.cancel.mockClear()
  state.fail = false
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 612, height: 792, right: 612, bottom: 792 })
  Object.defineProperty(Range.prototype, 'getClientRects', { configurable: true, value: () => [{ left: 72, top: 72, width: 90, height: 12, right: 162, bottom: 84 }] })
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); delete Range.prototype.getClientRects })

const cards = [{
  key: 'client',
  label: 'Client',
  fields: [{ key: 'full_name', path: 'client.full_name', legacy_paths: ['client.name'] }],
}]

describe('Word placeholder layer', () => {
  it('shows a bound placeholder in its card colour', async () => {
    // The subject a blank belongs to should be legible from the document
    // itself, not only from the properties panel.
    const bound = [{ name: 'client_name', label: 'Client name', binding: 'client.full_name' }]
    render(<WordPlaceholderLayer document={pdf} pageNumber={1} viewport={viewport} fields={bound} cards={cards} />)
    const button = await screen.findByRole('button', { name: 'Select Client name placeholder' })
    expect(button).toHaveAttribute('data-card', 'client')
    expect(button.className).toContain('word-placeholder-carded')
    expect(button.style.getPropertyValue('--card-accent')).toBeTruthy()
  })

  it('leaves an unbound placeholder neutral rather than picking a colour for it', async () => {
    // Nothing yet says where its value comes from, and a colour would imply
    // something does.
    render(<WordPlaceholderLayer document={pdf} pageNumber={1} viewport={viewport} fields={fields} cards={cards} />)
    const button = await screen.findByRole('button', { name: 'Select Client name placeholder' })
    expect(button).not.toHaveAttribute('data-card')
    expect(button.className).toContain('border-amber-600')
  })

  it('selects the matching field from a keyboard-accessible page highlight', async () => {
    const onSelect = vi.fn()
    const user = userEvent.setup()
    render(<WordPlaceholderLayer document={pdf} pageNumber={1} viewport={viewport} fields={fields} selectedIdentity="client_name:0" onSelectField={onSelect} />)
    const button = await screen.findByRole('button', { name: 'Select Client name placeholder' })
    expect(button).toHaveAttribute('aria-pressed', 'true')
    expect(button).toHaveStyle({ left: '72px', width: '90px' })
    await user.tab()
    await user.keyboard('{Enter}')
    expect(onSelect).toHaveBeenCalledWith('client_name:0')
  })

  it('clears old page marks immediately and ignores obsolete extraction completion', async () => {
    let resolvePage
    const delayed = { getPage: vi.fn(() => new Promise(resolve => { resolvePage = resolve })) }
    const view = render(<WordPlaceholderLayer document={pdf} pageNumber={1} viewport={viewport} fields={fields} />)
    await screen.findByRole('button', { name: 'Select Client name placeholder' })
    view.rerender(<WordPlaceholderLayer document={delayed} pageNumber={2} viewport={viewport} fields={fields} />)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(state.cancel).toHaveBeenCalled()
    view.unmount()
    await act(async () => resolvePage({ getTextContent: async () => ({ items: [{ str: '{{client_name}}' }] }) }))
    expect(document.querySelector('.word-placeholder-text')).toBeNull()
  })

  it('keeps extraction failures local to optional highlights', async () => {
    state.fail = true
    const view = render(<WordPlaceholderLayer document={pdf} pageNumber={1} viewport={viewport} fields={fields} />)
    await waitFor(() => expect(view.container.querySelector('span')).not.toBeNull())
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('shows a field name in its box and applies edits without leaving the page', async () => {
    const change = vi.fn()
    render(<WordPlaceholderLayer document={pdf} pageNumber={1} viewport={viewport} fields={fields} onUpdateField={change} />)
    const box = await screen.findByRole('button', { name: 'Select Client name placeholder' })
    expect(box).toHaveTextContent('Client name')
    fireEvent.click(box)
    fireEvent.change(screen.getByLabelText('Document field name'), { target: { value: 'Full client name' } })
    fireEvent.click(screen.getByRole('button', { name: 'Apply changes' }))
    expect(change).toHaveBeenCalledWith('client_name:0', { name: 'client_name', label: 'Full client name', field_type: 'text' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('selects actual rendered text even with no detected fields and creates a named replacement', async () => {
    const create = vi.fn()
    render(<WordPlaceholderLayer document={pdf} pageNumber={1} viewport={viewport} fields={[]} onCreateField={create} />)
    const host = screen.getByLabelText('Select text on document')
    await waitFor(() => expect(host.querySelector('span')).not.toBeNull())
    const range = document.createRange()
    range.selectNodeContents(host.querySelector('span'))
    window.getSelection().removeAllRanges()
    window.getSelection().addRange(range)
    fireEvent.mouseUp(host)
    expect(screen.getByRole('dialog', { name: 'Add document field' })).toBeVisible()
    fireEvent.change(screen.getByLabelText('Document field name'), { target: { value: 'Client name' } })
    fireEvent.change(screen.getByLabelText('Document field type'), { target: { value: 'date' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create field' }))
    expect(create).toHaveBeenCalledWith({ text: '{{client_name}}', label: 'Client name', field_type: 'date' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('keeps a failed edit open with its actionable error and cancels it when changing pages', async () => {
    const view = render(<WordPlaceholderLayer document={pdf} pageNumber={1} viewport={viewport} fields={fields} onUpdateField={() => 'That key is already used.'} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Select Client name placeholder' }))
    fireEvent.click(screen.getByRole('button', { name: 'Apply changes' }))
    expect(screen.getByRole('alert')).toHaveTextContent('That key is already used.')
    view.rerender(<WordPlaceholderLayer document={pdf} pageNumber={2} viewport={viewport} fields={fields} />)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('refuses disconnected selections rather than placing a misleading field box', async () => {
    Object.defineProperty(Range.prototype, 'getClientRects', { configurable: true, value: () => [] })
    const create = vi.fn()
    render(<WordPlaceholderLayer document={pdf} pageNumber={1} viewport={viewport} fields={[]} onCreateField={create} />)
    const host = screen.getByLabelText('Select text on document')
    await waitFor(() => expect(host.querySelector('span')).not.toBeNull())
    const range = document.createRange()
    range.selectNodeContents(host.querySelector('span'))
    window.getSelection().removeAllRanges()
    window.getSelection().addRange(range)
    fireEvent.keyUp(host)
    expect(screen.getByRole('status')).toHaveTextContent('Select words on one line')
    expect(create).not.toHaveBeenCalled()
  })
})
