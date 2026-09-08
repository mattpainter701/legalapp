import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import WorkflowRunsPanel from './WorkflowRunsPanel'

const api = vi.hoisted(() => ({ listWorkflowRuns: vi.fn(), resumeWorkflowRun: vi.fn(), cancelWorkflowRun: vi.fn(), reconcileWorkflowRunCloud: vi.fn() }))
vi.mock('../../api', () => api)
const base = {
  run_id: 'run', matter_id: 'matter', objective: 'prepare_document', status: 'awaiting_review',
  version: 4, next_step: 0, created_at: '2026-09-08T12:00:00Z', origin_channel: 'workspace_mcp',
  can_continue: true, plan_sha256: 'a'.repeat(64), events: [],
  steps: [{ step_key: 'draft', capability: 'propose_matter_document', status: 'awaiting_review', task_id: 'task', required_inputs: [] }],
}
function show(run = base) {
  api.listWorkflowRuns.mockResolvedValue({ items: [run], next_offset: null })
  return render(<MemoryRouter><WorkflowRunsPanel matterId="matter" /></MemoryRouter>)
}
beforeEach(() => { for (const mock of Object.values(api)) { mock.mockReset(); mock.mockResolvedValue({}) } })
afterEach(cleanup)

it('shows review links and continues the exact run version', async () => {
  show()
  expect(await screen.findByRole('link', { name: 'Open review task' })).toHaveAttribute('href', '/tasks/task')
  fireEvent.click(screen.getByRole('button', { name: 'Continue run' }))
  await waitFor(() => expect(api.resumeWorkflowRun).toHaveBeenCalledWith('run', { expected_version: 4, missing_arguments: {} }))
  expect(api.listWorkflowRuns).toHaveBeenCalledWith({ matter_id: 'matter', offset: 0 })
})

it('submits only requested missing inputs', async () => {
  show({ ...base, status: 'awaiting_input', steps: [{ ...base.steps[0], required_inputs: ['body'] }] })
  fireEvent.change(await screen.findByLabelText('body'), { target: { value: 'Reviewed wording' } })
  fireEvent.click(screen.getByRole('button', { name: 'Continue run' }))
  await waitFor(() => expect(api.resumeWorkflowRun).toHaveBeenCalledWith('run', { expected_version: 4, missing_arguments: { body: 'Reviewed wording' } }))
})

it('displays conflicts without losing supplied input', async () => {
  api.resumeWorkflowRun.mockRejectedValue({ response: { data: { detail: { message: 'Reload this run before continuing' } } } })
  show()
  fireEvent.click(await screen.findByRole('button', { name: 'Continue run' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Reload this run')
})

it('makes firm-visible peer runs read-only', async () => {
  show({ ...base, can_continue: false })
  await screen.findByRole('link', { name: 'Open review task' })
  expect(screen.queryByRole('button', { name: 'Continue run' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Cancel remaining steps' })).not.toBeInTheDocument()
})

it('cancels only the remaining run steps', async () => {
  show()
  fireEvent.click(await screen.findByRole('button', { name: 'Cancel remaining steps' }))
  await waitFor(() => expect(api.cancelWorkflowRun).toHaveBeenCalledWith('run', 4))
})

it('reconciles an existing cloud file without offering a repeat upload', async () => {
  show({ ...base, status: 'reconciliation_required', steps: [{ ...base.steps[0], storage_operation_id: 'op' }] })
  fireEvent.change(await screen.findByLabelText('Provider file ID'), { target: { value: 'file-id' } })
  fireEvent.change(screen.getByLabelText('Reconciliation note'), { target: { value: 'Found original' } })
  fireEvent.click(screen.getByRole('button', { name: 'Verify existing file' }))
  await waitFor(() => expect(api.reconcileWorkflowRunCloud).toHaveBeenCalledWith('run', { expected_version: 4, provider_object_id: 'file-id', reason: 'Found original' }))
  expect(screen.queryByRole('button', { name: 'Continue run' })).not.toBeInTheDocument()
})

it('shows a load error and supports refresh', async () => {
  api.listWorkflowRuns.mockRejectedValue(new Error('offline'))
  render(<MemoryRouter><WorkflowRunsPanel /></MemoryRouter>)
  expect(await screen.findByRole('alert')).toHaveTextContent('Unable to load workflow runs')
  api.listWorkflowRuns.mockResolvedValue({ items: [], next_offset: null })
  fireEvent.click(screen.getByRole('button', { name: 'Refresh runs' }))
  expect(await screen.findByText('No workflow runs yet.')).toBeInTheDocument()
})

it.each([[], null, { items: null }])('contains an invalid list response within the panel: %j', async response => {
  api.listWorkflowRuns.mockResolvedValue(response)
  render(<MemoryRouter><h1>Matter workflow</h1><WorkflowRunsPanel /></MemoryRouter>)
  expect(await screen.findByRole('alert')).toHaveTextContent('Unable to load workflow runs')
  expect(screen.getByRole('heading', { name: 'Matter workflow' })).toBeVisible()
})
