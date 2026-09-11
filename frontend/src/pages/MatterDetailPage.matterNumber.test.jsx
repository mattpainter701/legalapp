import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'

// vi.mock is hoisted above the file's own statements, so the spy has to be
// created inside vi.hoisted to exist by the time the factory runs.
const { getMatterByNumber } = vi.hoisted(() => ({ getMatterByNumber: vi.fn() }))

// Stub every api export. The page pulls in ~40 of them and, once the resolver
// redirects, the workspace mounts and calls many at once -- none of which this
// test is about, and all of which would otherwise attempt a request.
vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal()
  const stubbed = Object.fromEntries(
    Object.entries(actual).map(([key, value]) => [
      key,
      typeof value === 'function' ? vi.fn().mockResolvedValue({}) : value,
    ]),
  )
  return { ...stubbed, getMatterByNumber }
})

// The workspace reads the signed-in user; the resolver does not.
vi.mock('../App', () => ({ useAuth: () => ({ user: { hidden_matter_panels: [] } }) }))
vi.mock('../components/toast/useToast', () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }) }))

import MatterDetailPage, { MatterNumberBadge } from './MatterDetailPage'

// Surfaces the current path so a redirect is observable without reaching into
// router internals.
function LocationProbe() {
  const location = useLocation()
  return <div data-testid="path">{location.pathname}</div>
}

function renderAt(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/matters/:id" element={<MatterDetailPage />} />
        <Route path="/matters" element={<div>Matter Portfolio</div>} />
      </Routes>
      <LocationProbe />
    </MemoryRouter>,
  )
}

describe('matter number in the URL', () => {
  beforeEach(() => {
    getMatterByNumber.mockReset()
  })

  it('resolves a matter number to the matter and swaps the URL for the UUID', async () => {
    getMatterByNumber.mockResolvedValue({ id: '550e8400-e29b-41d4-a716-446655440000' })
    renderAt('/matters/SMIT0001')

    expect(await screen.findByText('Opening matter…')).toBeInTheDocument()
    await waitFor(() => expect(getMatterByNumber).toHaveBeenCalledWith('SMIT0001'))
    await waitFor(() =>
      expect(screen.getByTestId('path')).toHaveTextContent(
        '/matters/550e8400-e29b-41d4-a716-446655440000',
      ),
    )
  })

  it('does not resolve a UUID by number', async () => {
    renderAt('/matters/550e8400-e29b-41d4-a716-446655440000')
    await waitFor(() => expect(getMatterByNumber).not.toHaveBeenCalled())
  })

  it('offers a way back when the number matches no matter in this workspace', async () => {
    getMatterByNumber.mockRejectedValue(new Error('404'))
    renderAt('/matters/SMIT9999')

    expect(await screen.findByText('Matter not found')).toBeInTheDocument()
    expect(screen.getByText('SMIT9999')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Back to Matter Portfolio/i })).toBeInTheDocument()
  })

  it('normalizes a hyphenated number before showing it back to the reader', async () => {
    getMatterByNumber.mockRejectedValue(new Error('404'))
    renderAt('/matters/smit-9999')
    expect(await screen.findByText('SMIT9999')).toBeInTheDocument()
  })
})

describe('matter number badge', () => {
  it('copies the number to the clipboard and confirms it', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    // fireEvent rather than userEvent: userEvent.setup() installs its own
    // clipboard stub on navigator, which would shadow this one.
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })

    render(<MatterNumberBadge matterNumber="SMIT0001" />)
    const button = screen.getByRole('button', { name: /Copy matter number SMIT0001/i })
    expect(button).toHaveTextContent('SMIT0001')

    fireEvent.click(button)

    await waitFor(() => expect(writeText).toHaveBeenCalledWith('SMIT0001'))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Matter number SMIT0001 copied/i })).toBeInTheDocument(),
    )
    vi.unstubAllGlobals()
  })

  it('stays quiet when the clipboard is unavailable, leaving the number readable', async () => {
    // Insecure origins and permission policies can deny clipboard access. The
    // number is on screen regardless, so a denial must not surface an error.
    const writeText = vi.fn().mockRejectedValue(new Error('denied'))
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })

    render(<MatterNumberBadge matterNumber="SMIT0002" />)
    fireEvent.click(screen.getByRole('button', { name: /Copy matter number SMIT0002/i }))

    await waitFor(() => expect(writeText).toHaveBeenCalled())
    expect(screen.getByRole('button', { name: /Copy matter number SMIT0002/i })).toHaveTextContent('SMIT0002')
    vi.unstubAllGlobals()
  })

  it('renders nothing for a matter that has no number', () => {
    const { container } = render(<MatterNumberBadge matterNumber={null} />)
    expect(container).toBeEmptyDOMElement()
  })
})
