import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import FirmMemoryPage from './FirmMemoryPage'
import { getMattersV2, searchFirmMemory } from '../api'

vi.mock('../api', () => ({
  getMattersV2: vi.fn(),
  searchFirmMemory: vi.fn(),
}))

describe('FirmMemoryPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getMattersV2.mockResolvedValue({ items: [{ id: 'matter-1', name: 'Acme v. Northstar' }] })
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockResolvedValue(undefined) } })
    window.history.replaceState({}, '', '/firm-memory')
  })

  it('requires a matter and scopes the request to it', async () => {
    render(<FirmMemoryPage />)
    await waitFor(() => expect(screen.getByRole('option', { name: 'Acme v. Northstar' })).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Search document text'), { target: { value: 'notice of breach' } })
    fireEvent.click(screen.getByRole('button', { name: /search firm memory/i }))
    expect(searchFirmMemory).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('Matter'), { target: { value: 'matter-1' } })
    searchFirmMemory.mockResolvedValue({ hits: [], duration_ms: 420, index_state: 'ready' })
    fireEvent.click(screen.getByRole('button', { name: /search firm memory/i }))
    await waitFor(() => expect(searchFirmMemory).toHaveBeenCalledWith(expect.objectContaining({ matter_id: 'matter-1', query: 'notice of breach' })))
  })

  it('renders safe ranked results and makes partial coverage explicit', async () => {
    searchFirmMemory.mockResolvedValue({ partial: true, hits: [{ id: 'opaque-1', filename: 'Order.pdf', path: '\\\\server\\cases\\Order.pdf', snippet: 'Notice of breach appears here', page_number: 7, score: 0.91 }], duration_ms: 812, indexed_files: 10, pending_files: 2, errors: ['One agent is offline'] })
    render(<FirmMemoryPage />)
    await waitFor(() => screen.getByRole('option', { name: 'Acme v. Northstar' }))
    fireEvent.change(screen.getByLabelText('Search document text'), { target: { value: 'notice' } })
    fireEvent.change(screen.getByLabelText('Matter'), { target: { value: 'matter-1' } })
    fireEvent.click(screen.getByRole('button', { name: /search firm memory/i }))
    expect(await screen.findByText(/coverage is limited/i)).toBeInTheDocument()
    expect(screen.getByText('Order.pdf')).toBeInTheDocument()
    expect(screen.getByText('Page 7')).toBeInTheDocument()
    expect(document.querySelector('a[href^="file:"]')).toBeNull()
    expect(document.querySelector('a[href^="smb:"]')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /copy unc path/i }))
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith('\\\\server\\cases\\Order.pdf'))
  })
})
