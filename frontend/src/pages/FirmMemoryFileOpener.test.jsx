import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  createFirmMemoryOpenIntent: vi.fn(),
  getFirmMemoryFile: vi.fn(),
  getMattersV2: vi.fn(),
  searchFirmMemory: vi.fn(),
}))

vi.mock('../api', () => api)

describe('FirmMemoryPage file opener', () => {
  beforeEach(() => {
    vi.spyOn(navigator, 'platform', 'get').mockReturnValue('Win32')
    vi.resetModules()
    vi.stubEnv('VITE_ENABLE_FILE_OPENER', 'true')
    api.getMattersV2.mockResolvedValue({ items: [{ id: 'matter-1', name: 'Acme' }] })
    api.searchFirmMemory.mockResolvedValue({
      hits: [{
        id: 'file-1',
        source_id: 'source-1',
        file_revision: 'r1',
        filename: 'Motion.pdf',
        path: '\\\\server\\share\\Motion.pdf',
      }],
    })
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllEnvs()
    vi.clearAllMocks()
    vi.restoreAllMocks()
  })

  it('creates a source-aware intent and shows safe recovery without a raw file URL', async () => {
    api.createFirmMemoryOpenIntent.mockRejectedValue({
      response: { data: { detail: 'File or share is no longer available' } },
    })
    const { default: FirmMemoryPage } = await import('./FirmMemoryPage')
    render(<FirmMemoryPage />)

    await screen.findByRole('option', { name: 'Acme' })
    fireEvent.change(screen.getByLabelText('Search document text'), { target: { value: 'motion' } })
    fireEvent.change(screen.getByLabelText('Matter'), { target: { value: 'matter-1' } })
    const searchButton = screen.getByRole('button', { name: /search firm memory/i })
    await waitFor(() => expect(searchButton).toBeEnabled())
    fireEvent.click(searchButton)
    await waitFor(() => expect(api.searchFirmMemory).toHaveBeenCalled())
    await screen.findByRole('heading', { name: 'Motion.pdf' })
    fireEvent.click(screen.getByRole('button', { name: /open on this computer/i }))

    await waitFor(() => expect(api.createFirmMemoryOpenIntent).toHaveBeenCalledWith(
      'file-1',
      { matterId: 'matter-1', action: 'open' },
    ))
    expect(await screen.findByRole('alert')).toHaveTextContent(/could not reach|copy network path/i)
    expect(document.querySelector('a[href^="file:"]')).toBeNull()
    expect(document.querySelector('a[href^="smb:"]')).toBeNull()
  })
})
