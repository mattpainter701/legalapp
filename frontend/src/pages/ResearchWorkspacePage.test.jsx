import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ResearchWorkspacePage from './ResearchWorkspacePage'

const api = vi.hoisted(() => ({
  listResearchWorkspaces: vi.fn(), createResearchWorkspace: vi.fn(), listResearchRecords: vi.fn(), createResearchRecord: vi.fn(),
  createResearchSnapshot: vi.fn(), listResearchSnapshots: vi.fn(), listResearchWorkspaceHistory: vi.fn(), exportResearchSnapshot: vi.fn(),
}))
vi.mock('../api', () => api)

beforeEach(() => {
  vi.clearAllMocks()
  api.listResearchWorkspaces.mockResolvedValue({ items: [] })
  api.listResearchRecords.mockResolvedValue({ items: [] })
  api.listResearchSnapshots.mockResolvedValue({ items: [] })
  api.listResearchWorkspaceHistory.mockResolvedValue({ items: [] })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('states the evidence classes and creates a matter-scoped workspace', async () => {
  render(<MemoryRouter initialEntries={['/matters/m-1/research']}><Routes><Route path="/matters/:matterId/research" element={<ResearchWorkspacePage />} /></Routes></MemoryRouter>)
  expect(await screen.findByRole('heading', { name: 'Research Workspace' })).toBeInTheDocument()
  expect(screen.getByLabelText('Workspace title')).toBeInTheDocument()
  expect(screen.getByText(/Create a workspace for this matter/i)).toBeInTheDocument()
})

it('reuses an attempt key after a failed create and makes a distinct snapshot key', async () => {
  vi.stubGlobal('crypto', { randomUUID: vi.fn().mockReturnValueOnce('workspace-attempt').mockReturnValueOnce('snapshot-attempt') })
  api.createResearchWorkspace.mockRejectedValueOnce(new Error('retry')).mockResolvedValueOnce({ id: 'workspace-1', title: 'Issue set', role: 'owner' })
  api.createResearchSnapshot.mockRejectedValueOnce(new Error('retry')).mockResolvedValueOnce({ id: 'snapshot-1', sequence: 1, sha256: 'a'.repeat(64), created_at: '2026-08-30T00:00:00Z' })
  render(<MemoryRouter initialEntries={['/matters/m-1/research']}><Routes><Route path="/matters/:matterId/research" element={<ResearchWorkspacePage />} /></Routes></MemoryRouter>)
  const input = await screen.findByLabelText('Workspace title')
  fireEvent.change(input, { target: { value: 'Issue set' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create workspace' }))
  await waitFor(() => expect(api.createResearchWorkspace).toHaveBeenCalledTimes(1))
  fireEvent.click(screen.getByRole('button', { name: 'Create workspace' }))
  await waitFor(() => expect(api.createResearchWorkspace).toHaveBeenCalledTimes(2))
  expect(api.createResearchWorkspace.mock.calls.map((call) => call[2])).toEqual(['workspace-attempt', 'workspace-attempt'])
  await screen.findByRole('button', { name: /Freeze snapshot/i })
  fireEvent.click(screen.getByRole('button', { name: /Freeze snapshot/i }))
  await waitFor(() => expect(api.createResearchSnapshot).toHaveBeenCalledTimes(1))
  fireEvent.click(screen.getByRole('button', { name: /Freeze snapshot/i }))
  await waitFor(() => expect(api.createResearchSnapshot).toHaveBeenCalledTimes(2))
  expect(api.createResearchSnapshot.mock.calls.map((call) => call[3])).toEqual(['snapshot-attempt', 'snapshot-attempt'])
  expect(api.createResearchWorkspace.mock.calls[0][2]).not.toBe(api.createResearchSnapshot.mock.calls[0][3])
})

it('keeps a slower workspace response from replacing the selected workspace', async () => {
  let resolveFirst
  const firstRecords = new Promise((resolve) => { resolveFirst = resolve })
  api.listResearchWorkspaces.mockResolvedValue({ items: [
    { id: 'workspace-1', title: 'First', role: 'owner' },
    { id: 'workspace-2', title: 'Second', role: 'owner' },
  ] })
  api.listResearchRecords
    .mockReturnValueOnce(firstRecords)
    .mockResolvedValue({ items: [{ id: 'second-record', title: 'Second record' }] })
  api.listResearchSnapshots.mockResolvedValue({ items: [] })
  api.listResearchWorkspaceHistory.mockResolvedValue({ items: [] })

  render(<MemoryRouter initialEntries={['/matters/m-1/research']}><Routes><Route path="/matters/:matterId/research" element={<ResearchWorkspacePage />} /></Routes></MemoryRouter>)
  await screen.findByRole('button', { name: /Second/i })
  fireEvent.click(screen.getByRole('button', { name: /Second/i }))
  expect(await screen.findByText('Second record')).toBeInTheDocument()
  resolveFirst({ items: [{ id: 'first-record', title: 'First record' }] })
  await Promise.resolve()
  expect(screen.queryByText('First record')).not.toBeInTheDocument()
})

it('clears the previous trail while a selected workspace loads and ignores its stale failure', async () => {
  let rejectSecond
  const secondRecords = new Promise((_, reject) => { rejectSecond = reject })
  api.listResearchWorkspaces.mockResolvedValue({ items: [
    { id: 'workspace-1', title: 'First', role: 'owner' },
    { id: 'workspace-2', title: 'Second', role: 'owner' },
  ] })
  api.listResearchRecords
    .mockResolvedValueOnce({ items: [{ id: 'first-record', title: 'First record' }] })
    .mockReturnValueOnce(secondRecords)
    .mockResolvedValue({ items: [] })
  api.listResearchSnapshots.mockResolvedValue({ items: [] })
  api.listResearchWorkspaceHistory.mockResolvedValue({ items: [] })

  render(<MemoryRouter initialEntries={['/matters/m-1/research']}><Routes><Route path="/matters/:matterId/research" element={<ResearchWorkspacePage />} /></Routes></MemoryRouter>)
  expect(await screen.findByText('First record')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /Second/i }))
  expect(screen.queryByText('First record')).not.toBeInTheDocument()
  rejectSecond(new Error('stale workspace failed'))
  await Promise.resolve()
  expect(screen.queryByText('stale workspace failed')).not.toBeInTheDocument()
})

it('ignores a delayed workspace list from the previous matter', async () => {
  let resolveFirstMatter
  const firstMatter = new Promise((resolve) => { resolveFirstMatter = resolve })
  api.listResearchWorkspaces
    .mockReturnValueOnce(firstMatter)
    .mockResolvedValueOnce({ items: [{ id: 'workspace-2', title: 'Second matter workspace', role: 'owner' }] })
  api.listResearchRecords.mockResolvedValue({ items: [] })
  api.listResearchSnapshots.mockResolvedValue({ items: [] })
  api.listResearchWorkspaceHistory.mockResolvedValue({ items: [] })

  const view = (matterId) => (
    <MemoryRouter key={matterId} initialEntries={[`/matters/${matterId}/research`]}>
      <Routes><Route path="/matters/:matterId/research" element={<ResearchWorkspacePage />} /></Routes>
    </MemoryRouter>
  )
  const { rerender } = render(view('m-1'))
  rerender(view('m-2'))
  expect(await screen.findByRole('button', { name: /Second matter workspace/i })).toBeInTheDocument()
  resolveFirstMatter({ items: [{ id: 'workspace-1', title: 'Old matter workspace', role: 'owner' }] })
  await Promise.resolve()
  expect(screen.queryByRole('button', { name: /Old matter workspace/i })).not.toBeInTheDocument()
})

it('does not append an in-flight record to a newly selected workspace', async () => {
  let resolveCreate
  api.listResearchWorkspaces.mockResolvedValue({ items: [
    { id: 'workspace-1', title: 'First', role: 'owner' },
    { id: 'workspace-2', title: 'Second', role: 'owner' },
  ] })
  api.listResearchRecords.mockResolvedValue({ items: [] })
  api.listResearchSnapshots.mockResolvedValue({ items: [] })
  api.listResearchWorkspaceHistory.mockResolvedValue({ items: [] })
  api.createResearchRecord.mockReturnValue(new Promise((resolve) => { resolveCreate = resolve }))
  render(<MemoryRouter initialEntries={['/matters/m-1/research']}><Routes><Route path="/matters/:matterId/research" element={<ResearchWorkspacePage />} /></Routes></MemoryRouter>)
  await screen.findByRole('button', { name: /Second/i })
  fireEvent.change(screen.getByLabelText('Record title'), { target: { value: 'Old record' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save record' }))
  fireEvent.click(screen.getByRole('button', { name: /Second/i }))
  resolveCreate({ id: 'old-record', title: 'Old record' })
  await Promise.resolve()
  expect(screen.queryByText('Old record')).not.toBeInTheDocument()
})
