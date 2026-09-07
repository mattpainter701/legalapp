import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
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

describe('Word placeholder layer', () => {
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
})
