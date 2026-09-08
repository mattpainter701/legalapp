import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import WorkflowSynthesisPanel from './WorkflowSynthesisPanel'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('../../api', () => ({ default: api }))
const user = { capabilities: ['manage_workflows', 'manage_matters', 'admin_settings'] }
const proposal = {
  id: 'proposal', name: 'Probate workflow', template_version_id: 'v1', status: 'pending',
  version_status: 'draft', rule_status: 'draft', proposal_sha256: 'a'.repeat(64), definition_sha256: 'b'.repeat(64),
  configuration: { definition: { checklist: [{ item_key: 'inventory', title: 'Request inventory', due_offset_days: 10, assignee_role: 'matter_owner' }] } },
  evidence: { cohort: 'lawhand', matter_count: 3, warning: 'Review wording and timing.', items: [{ item_key: 'inventory', matter_count: 3, sample_share: 1, due_offset_range: [9, 11], assignee_counts: { matter_owner: 3 }, evidence_refs: [{ kind: 'task', id: 'source-1', sha256: 'c'.repeat(64) }] }] },
}
beforeEach(() => { api.get.mockReset(); api.post.mockReset(); api.get.mockResolvedValue({ data: { items: [proposal], jobs: [], next_offset: null } }); api.post.mockResolvedValue({ data: {} }) })
afterEach(cleanup)

it('renders evidence, exact version link, and no independent approval button', async () => {
  render(<WorkflowSynthesisPanel user={user} />)
  expect(await screen.findByText('Request inventory')).toBeInTheDocument()
  expect(screen.getByText('3 (100%)')).toBeInTheDocument()
  expect(screen.getByText(/task source-1/)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /Review this draft/ })).toHaveAttribute('href', '#workflow-version-v1')
  expect(screen.queryByRole('button', { name: /^Approve/ })).not.toBeInTheDocument()
})

it('queues analysis and refreshes templates', async () => {
  const changed = vi.fn()
  render(<WorkflowSynthesisPanel user={user} onChanged={changed} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Analyze firm history' }))
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/workflow-config/synthesis/analyze', { request_id: expect.any(String) }))
  await waitFor(() => expect(changed).toHaveBeenCalled())
})

it('binds a decline to the exact proposal and requires a reason', async () => {
  render(<WorkflowSynthesisPanel user={user} />)
  const button = await screen.findByRole('button', { name: 'Decline pattern' })
  expect(button).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Reason to decline this pattern'), { target: { value: 'Keep our current process' } })
  fireEvent.click(button)
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/workflow-config/synthesis/proposal/decline', { expected_proposal_sha256: 'a'.repeat(64), reason: 'Keep our current process' }))
})

it('hides management actions from a legal reviewer and all content from unauthorized users', async () => {
  const { rerender } = render(<WorkflowSynthesisPanel user={{ capabilities: ['approve_legal_work'] }} />)
  await screen.findByText('Request inventory')
  expect(screen.queryByRole('button', { name: 'Analyze firm history' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Decline pattern' })).not.toBeInTheDocument()
  expect(screen.queryByText('Import Clio or Tabs3 workflow history')).not.toBeInTheDocument()
  rerender(<WorkflowSynthesisPanel user={{ capabilities: [] }} />)
  expect(screen.queryByRole('region')).not.toBeInTheDocument()
})

it('maps separate task and matter exports and binds the preview fingerprint', async () => {
  api.post.mockResolvedValueOnce({ data: { task_headers: ['Matter', 'Task', 'Due'], task_rows: 3, matter_headers: ['Key', 'Opened'], matter_rows: 3, fingerprint: 'f'.repeat(64) } })
    .mockResolvedValueOnce({ data: { imported_rows: 3, reused: false } })
  render(<WorkflowSynthesisPanel user={user} />)
  const tasks = new File(['Matter,Task,Due'], 'tasks.csv', { type: 'text/csv' })
  const matters = new File(['Key,Opened'], 'matters.csv', { type: 'text/csv' })
  fireEvent.change(screen.getByLabelText('Task or calendar CSV'), { target: { files: [tasks] } })
  fireEvent.change(screen.getByLabelText('Matter CSV (optional)'), { target: { files: [matters] } })
  fireEvent.click(screen.getByRole('button', { name: 'Read column names' }))
  await screen.findByText(/3 task rows and 3 matter rows/)
  for (const [label, value] of [['Task CSV: Matter key', 'Matter'], ['Task CSV: Task title', 'Task'], ['Task CSV: Task due date', 'Due'], ['Matter CSV: Matter key', 'Key'], ['Matter CSV: Matter opened date', 'Opened']]) {
    fireEvent.change(screen.getByLabelText(label), { target: { value } })
  }
  fireEvent.click(screen.getByRole('button', { name: 'Stage history and prepare drafts' }))
  await screen.findByText(/3 history rows staged/)
  const [url, form] = api.post.mock.calls[1]
  expect(url).toBe('/workflow-config/synthesis/history/import')
  expect(form.get('tasks')).toBe(tasks)
  expect(form.get('matters')).toBe(matters)
  expect(form.get('expected_fingerprint')).toBe('f'.repeat(64))
  expect(JSON.parse(form.get('mapping')).matters).toEqual({ matter_key: 'Key', opened_at: 'Opened' })
})

it('resets a preview when either uploaded file changes', async () => {
  api.post.mockResolvedValue({ data: { task_headers: ['a'], task_rows: 3, matter_headers: [], matter_rows: 0, fingerprint: 'f'.repeat(64) } })
  render(<WorkflowSynthesisPanel user={user} />)
  fireEvent.change(screen.getByLabelText('Task or calendar CSV'), { target: { files: [new File(['a'], 'one.csv')] } })
  fireEvent.click(screen.getByRole('button', { name: 'Read column names' }))
  await screen.findByText(/3 task rows/)
  fireEvent.change(screen.getByLabelText('Task or calendar CSV'), { target: { files: [new File(['b'], 'two.csv')] } })
  expect(screen.queryByRole('button', { name: 'Stage history and prepare drafts' })).not.toBeInTheDocument()
})

it('shows actionable errors and keeps onboarding focused on later review', async () => {
  api.post.mockRejectedValue({ response: { data: { detail: 'Files changed; preview and map them again' } } })
  render(<WorkflowSynthesisPanel user={user} onboarding />)
  await screen.findByText(/1 suggestions are ready/)
  expect(screen.queryByText('Request inventory')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Analyze firm history' }))
  await screen.findByText('Files changed; preview and map them again')
})
