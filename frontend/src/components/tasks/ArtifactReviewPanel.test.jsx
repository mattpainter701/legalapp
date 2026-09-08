import { cleanup, render, screen, fireEvent, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ArtifactReviewPanel from './ArtifactReviewPanel'
import api from '../../api'

vi.mock('../../api', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
const task = { id: 'task-1', version: 3, pending_action: { artifact_id: 'artifact-1', artifact_revision_no: 2 } }
beforeEach(() => { vi.resetAllMocks() })

it('submits the exact live version to the assigned staff review route', async () => {
  api.get.mockResolvedValue({ data: { task_version: 3, current_revision_no: 2, available_actions: ['staff'] } })
  const updated = { ...task, version: 4 }
  api.post.mockResolvedValue({ data: updated })
  const onUpdated = vi.fn()
  render(<ArtifactReviewPanel task={task} onUpdated={onUpdated} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Complete staff review' }))
  await waitFor(() => expect(onUpdated).toHaveBeenCalledWith(updated))
  expect(api.post).toHaveBeenCalledWith('/tasks/task-1/review/staff', { expected_version: 3, reason: null, decision: 'approve' })
  expect(screen.queryByRole('button', { name: 'Approve document' })).not.toBeInTheDocument()
})

it('requires an explicit override reason and records it through the human endpoint', async () => {
  api.get.mockResolvedValue({ data: { task_version: 3, available_actions: ['override'] } })
  api.post.mockResolvedValue({ data: task })
  render(<ArtifactReviewPanel task={task} onUpdated={vi.fn()} />)
  const button = await screen.findByRole('button', { name: 'Override staff review' })
  fireEvent.click(button)
  expect(api.post).not.toHaveBeenCalled()
  expect(screen.getByRole('alert')).toHaveTextContent('Enter a reason')
  fireEvent.change(screen.getByLabelText('Review reason'), { target: { value: 'Deadline today; personally reviewed' } })
  fireEvent.click(button)
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/tasks/task-1/review/attorney-override', { expected_version: 3, reason: 'Deadline today; personally reviewed' }))
})

it('disables decisions when the history belongs to a newer task version', async () => {
  api.get.mockResolvedValue({ data: { task_version: 4, available_actions: ['attorney'] } })
  render(<ArtifactReviewPanel task={task} onUpdated={vi.fn()} />)
  expect(await screen.findByRole('button', { name: 'Approve document' })).toBeDisabled()
  expect(screen.getByRole('alert')).toHaveTextContent('This task changed')
})

it('retains unknown delivery evidence and surfaces a rejected decision', async () => {
  api.get.mockResolvedValue({ data: { task_version: 3, available_actions: ['attorney'], deliveries: [{ id: 'delivery', channel: 'email', status: 'outcome_unknown', created_at: '2026-09-08T12:00:00Z' }] } })
  api.post.mockRejectedValue({ response: { data: { detail: 'The cloud document changed' } } })
  render(<ArtifactReviewPanel task={task} onUpdated={vi.fn()} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Approve document' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('The cloud document changed')
  expect(screen.getByText(/email: outcome unknown/)).toBeInTheDocument()
})

afterEach(cleanup)
