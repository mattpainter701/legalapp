import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import BriefCheckPage from './BriefCheckPage'

vi.mock('../api', () => ({
  listBriefChecks: vi.fn().mockResolvedValue({ items: [] }),
  createBriefCheck: vi.fn(),
  decideBriefCheckItem: vi.fn(),
  exportBriefCheck: vi.fn(),
}))

describe('BriefCheckPage', () => {
  it('communicates bounded, attorney-review-first behavior', async () => {
    render(<MemoryRouter initialEntries={['/matters/m-1/brief-check']}><Routes><Route path="/matters/:matterId/brief-check" element={<BriefCheckPage />} /></Routes></MemoryRouter>)
    expect(await screen.findByRole('heading', { name: 'Brief Check' })).toBeInTheDocument()
    expect(screen.getByText(/No absence-of-evidence result is a good-law determination/i)).toBeInTheDocument()
    expect(screen.getByText(/15 MB and 300 pages/i)).toBeInTheDocument()
  })
})
