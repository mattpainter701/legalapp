import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route, Link } from 'react-router-dom'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import { cleanup } from '@testing-library/react'
import * as api from '../api'
import MatterDetailPage from './MatterDetailPage'

vi.mock('../api', async () => {
  const actual = await vi.importActual('../api')
  return Object.fromEntries(Object.entries(actual).map(([key, value]) => [key, typeof value === 'function' ? vi.fn().mockResolvedValue([]) : value]))
})
vi.mock('../App', () => ({ useAuth: () => ({ user: { id: 'user', role: 'admin' } }) }))

beforeEach(() => {
  vi.clearAllMocks()
  api.getMatterV2.mockImplementation(async id => ({ id, matter_name: `Matter ${id}`, assignments: [], key_dates: {}, status: 'open' }))
  api.getMatterDashboard.mockResolvedValue({ open_tasks: 0, active_workers: [] })
  api.getMatterBudgetV2.mockResolvedValue({})
})
afterEach(cleanup)

it.each(['resolve', 'reject'])('ignores a late note %s after switching away and back to the same matter', async outcome => {
  let settle
  api.addMatterNote.mockImplementationOnce(() => new Promise((resolve, reject) => { settle = outcome === 'resolve' ? resolve : reject }))
  render(<MemoryRouter initialEntries={['/matters/A']}>
    <Link to="/matters/A">Go A</Link><Link to="/matters/B">Go B</Link>
    <Routes><Route path="/matters/:id" element={<MatterDetailPage />} /></Routes>
  </MemoryRouter>)
  await screen.findByRole('heading', { name: 'Matter A' })
  fireEvent.click(screen.getByRole('button', { name: 'Quick note' }))
  fireEvent.change(screen.getByLabelText('Title', { exact: true }), { target: { value: 'Old A draft' } })
  fireEvent.change(screen.getByLabelText('Content', { exact: true }), { target: { value: 'Original note' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save Note', exact: true }))
  await waitFor(() => expect(api.addMatterNote).toHaveBeenCalledOnce())
  fireEvent.click(screen.getByRole('link', { name: 'Go B' }))
  await screen.findByRole('heading', { name: 'Matter B' })
  fireEvent.click(screen.getByRole('link', { name: 'Go A' }))
  await screen.findByRole('heading', { name: 'Matter A' })
  fireEvent.click(screen.getByRole('button', { name: 'Quick note' }))
  fireEvent.change(screen.getByLabelText('Title', { exact: true }), { target: { value: 'New A draft' } })
  fireEvent.change(screen.getByLabelText('Content', { exact: true }), { target: { value: 'Do not replace this' } })
  const timelineCalls = api.getMatterTimeline.mock.calls.length
  await act(async () => settle(outcome === 'resolve' ? { id: 'saved-original' } : new Error('late failure')))
  expect(screen.getByLabelText('Title', { exact: true })).toHaveValue('New A draft')
  expect(screen.getByLabelText('Content', { exact: true })).toHaveValue('Do not replace this')
  expect(screen.queryByText('Note saved.')).not.toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(api.getMatterTimeline).toHaveBeenCalledTimes(timelineCalls)
})
