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
