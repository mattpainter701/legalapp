import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route, Link, useLocation, useNavigate } from 'react-router-dom'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import * as api from '../api'
import MatterDetailPage from './MatterDetailPage'

vi.mock('../api', async () => {
  const actual = await vi.importActual('../api')
  return Object.fromEntries(Object.entries(actual).map(([key, value]) => [key, typeof value === 'function' ? vi.fn().mockResolvedValue([]) : value]))
})
vi.mock('../App', () => ({ useAuth: () => ({ user: { id: 'user', role: 'admin' } }) }))
vi.mock('../components/MatterDocumentsTab', () => ({ default: () => <p>Document workspace</p> }))
vi.mock('../components/MatterPartiesTab', () => ({ default: () => <p>Parties panel</p> }))

function Navigation() {
  const location = useLocation()
  const navigate = useNavigate()
  return <><output aria-label="Location">{location.pathname}{location.search}</output>
    <button onClick={() => navigate(-1)}>Browser back</button>
    <Link to="/matters/B">Go B</Link></>
}
function renderMatter(path = '/matters/A') {
  return render(<MemoryRouter initialEntries={[path]}><Navigation />
    <Routes><Route path="/matters/:id" element={<MatterDetailPage />} /></Routes>
  </MemoryRouter>)
}
beforeEach(() => {
  vi.clearAllMocks()
  api.getMatterV2.mockImplementation(async id => ({ id, matter_name: `Matter ${id}`, assignments: [], key_dates: {}, status: 'open' }))
  api.getMatterDashboard.mockResolvedValue({ open_tasks: 1, active_workers: [] })
  api.getMatterBudgetV2.mockResolvedValue({})
  api.getTasks.mockResolvedValue({ items: [{ id: 'task-1', title: 'Review inventory', task_type: 'review', status: 'pending' }] })
})
afterEach(cleanup)

it('opens a bookmarked section and restores it with browser back while preserving other query parameters', async () => {
  renderMatter('/matters/A?tab=documents&source=estate')
  await screen.findByText('Document workspace')
  fireEvent.click(screen.getByRole('button', { name: 'Activity', exact: true }))
  expect(screen.getByLabelText('Location')).toHaveTextContent('?tab=activity&source=estate')
  fireEvent.click(screen.getByRole('button', { name: 'Browser back' }))
  await screen.findByText('Document workspace')
  expect(screen.getByLabelText('Location')).toHaveTextContent('?tab=documents&source=estate')
})

it('keeps team and matter parties together under People', async () => {
  renderMatter('/matters/A?tab=team')
  expect(await screen.findByText('Parties panel')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Team Assignments' })).toBeInTheDocument()
})

it('keeps the Documents workspace free of the Parties panel', async () => {
  renderMatter('/matters/A?tab=documents')
  await screen.findByText('Document workspace')
  expect(screen.queryByText('Parties panel')).not.toBeInTheDocument()
})

it('falls back to dashboard for an unknown section and exposes scoped task correction links', async () => {  renderMatter('/matters/A?tab=unknown')
  expect(await screen.findByRole('link', { name: 'Review inventory', exact: true })).toHaveAttribute('href', '/tasks/task-1?matter_id=A')
  expect(screen.getByRole('link', { name: 'Manage matter tasks' })).toHaveAttribute('href', '/tasks?matter_id=A')
})

it('retains a task on rejected completion, prevents duplicate submits, and explains the failure', async () => {
  let reject
  api.updateTask.mockImplementationOnce(() => new Promise((_, fail) => { reject = fail }))
  renderMatter()
  const complete = await screen.findByRole('button', { name: 'Complete task: Review inventory' })
  fireEvent.click(complete)
  fireEvent.click(complete)
  expect(complete).toBeDisabled()
  expect(api.updateTask).toHaveBeenCalledOnce()
  await act(async () => reject({ response: { data: { detail: 'This task requires review before completion.' } } }))
  expect(screen.getByRole('alert')).toHaveTextContent('requires review')
  expect(screen.getByRole('link', { name: 'Review inventory', exact: true })).toBeInTheDocument()
  expect(complete).toBeEnabled()
  api.updateTask.mockResolvedValueOnce({ status: 'completed' })
  fireEvent.click(complete)
  await waitFor(() => expect(screen.queryByRole('link', { name: 'Review inventory', exact: true })).not.toBeInTheDocument())
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

it('shows retry instead of a false empty state after a failed task load', async () => {
  api.getTasks.mockRejectedValueOnce(new Error('offline'))
  renderMatter()
  const retry = await screen.findByRole('button', { name: 'Retry tasks' })
  expect(screen.queryByText(/No pending to-dos/)).not.toBeInTheDocument()
  fireEvent.click(retry)
  await screen.findByRole('link', { name: 'Review inventory', exact: true })
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

it('isolates matter state when a previous matter response arrives late', async () => {
  let resolveA
  api.getMatterV2.mockImplementation(id => id === 'A'
    ? new Promise(resolve => { resolveA = resolve })
    : Promise.resolve({ id, matter_name: `Matter ${id}`, assignments: [], key_dates: {}, status: 'open' }))
  renderMatter()
  fireEvent.click(screen.getByRole('link', { name: 'Go B' }))
  await screen.findByRole('heading', { name: 'Matter B' })
  await act(async () => resolveA({ id: 'A', matter_name: 'Matter A' }))
  expect(screen.getByRole('heading', { name: 'Matter B' })).toBeInTheDocument()
  expect(screen.queryByRole('heading', { name: 'Matter A' })).not.toBeInTheDocument()
})
