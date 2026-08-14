import React from 'react'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TaskBoard from './TaskBoard'
import { getTask, getTaskEvents } from '../../api'

vi.mock('../../api', () => ({
  getTask: vi.fn(),
  getTaskEvents: vi.fn(),
  searchUsers: vi.fn().mockResolvedValue([]),
}))

const task = {
  id: 'task-1',
  title: 'Review discovery responses',
  task_type: 'review',
  status: 'pending',
  priority: 'high',
  due_date: '2026-08-08',
  due_time: null,
  matter_id: 'matter-1',
  contact_id: null,
  assigned_to_user_id: 'user-1',
  reviewer_user_id: null,
  matter: { id: 'matter-1', label: 'Smith v. Jones', case_number: 'CV-26-104' },
  assignee: { id: 'user-1', label: 'Pat Paralegal' },
  reviewer: null,
  viewed_at: null,
  customer_contacted_at: null,
  waiting_reason: null,
  waiting_follow_up_date: null,
  source: 'manual',
  external_ref: null,
  version: 1,
  status_changed_at: '2026-08-04T15:00:00Z',
  updated_at: '2026-08-04T15:00:00Z',
}

const statuses = [
  ['pending', 'To Do'],
  ['in_progress', 'In Progress'],
  ['waiting', 'Waiting'],
  ['review', 'Review'],
  ['completed', 'Done'],
]

const data = {
  scope: 'mine',
  generated_at: '2026-08-04T15:00:00Z',
  risk_counts: { overdue: 1, due_today: 2, unassigned: 0, waiting_follow_up_due: 1 },
  columns: statuses.map(([status, label]) => ({
    status,
    label,
    total: status === 'pending' ? 1 : 0,
    items: status === 'pending' ? [task] : [],
    next_cursor: null,
  })),
}

const props = {
  data,
  loading: false,
  error: null,
  scope: 'mine',
  onRetry: vi.fn(),
  onTransition: vi.fn(),
  onLoadMore: vi.fn(),
  taskId: null,
  onOpenTask: vi.fn(),
  onCloseTask: vi.fn(),
  onTaskAction: vi.fn(),
  canOpenMatters: true,
}

describe('TaskBoard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getTask.mockResolvedValue({ ...task, description: 'Privileged work notes.' })
    getTaskEvents.mockResolvedValue({ items: [], total: 0 })
  })

  afterEach(() => cleanup())

  it('shows risk and workflow metadata without privileged descriptions', async () => {
    const { container } = render(<TaskBoard {...props} />)

    expect(screen.getAllByText('1', { selector: 'div.text-lg' })).toHaveLength(2)
    expect(screen.getAllByText('Review discovery responses').length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Smith v. Jones/).length).toBeGreaterThan(0)
    expect(screen.queryByText('Privileged work notes.')).not.toBeInTheDocument()
    expect(await axe(container)).toHaveNoViolations()
  })

  it('uses the accessible destination flow and requires a waiting reason', async () => {
    const user = userEvent.setup()
    props.onTransition.mockResolvedValue({
      ...task,
      status: 'waiting',
      version: 2,
      waiting_reason: 'Waiting for signed authorization',
    })
    render(<TaskBoard {...props} />)

    await user.click(screen.getAllByRole('button', { name: 'Choose a destination for Review discovery responses' })[0])
    await user.click(screen.getByRole('button', { name: /^Waiting/ }))
    expect(screen.getByRole('dialog', { name: 'Move to Waiting' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Move task' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Explain what this task is waiting on')

    await user.type(screen.getByRole('textbox', { name: /Waiting on/ }), 'Waiting for signed authorization')
    await user.type(screen.getByLabelText(/Follow up on/), '2026-08-12')
    await user.click(screen.getByRole('button', { name: 'Move task' }))

    await waitFor(() => expect(props.onTransition).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'task-1', version: 1 }),
      'waiting',
      expect.objectContaining({
        reason: 'Waiting for signed authorization',
        waiting_follow_up_date: '2026-08-12',
      }),
    ))
  })

  it('loads sensitive notes and task history only after the detail route opens', async () => {
    getTaskEvents.mockResolvedValue({
      total: 1,
      items: [{
        id: 'event-1',
        task_id: 'task-1',
        event_type: 'status_changed',
        actor_label: 'Alex Attorney',
        from_status: 'pending',
        to_status: 'in_progress',
        note: 'Started drafting',
        metadata_json: {},
        created_at: '2026-08-04T15:30:00Z',
      }],
    })
    const { rerender } = render(<TaskBoard {...props} />)
    expect(getTask).not.toHaveBeenCalled()

    rerender(<TaskBoard {...props} taskId="task-1" />)

    expect(await screen.findByText('Privileged work notes.')).toBeInTheDocument()
    expect(screen.getByText('To Do → In Progress')).toBeInTheDocument()
    expect(getTask).toHaveBeenCalledWith('task-1')
  })

  it('rolls back and refreshes when another user changed the task first', async () => {
    const user = userEvent.setup()
    const onRetry = vi.fn().mockResolvedValue(undefined)
    const onTransition = vi.fn().mockRejectedValue({
      response: {
        status: 409,
        data: { detail: { message: 'This task changed after it was loaded.' } },
      },
    })
    render(<TaskBoard {...props} onRetry={onRetry} onTransition={onTransition} />)

    await user.click(screen.getAllByRole('button', { name: 'Choose a destination for Review discovery responses' })[0])
    await user.click(screen.getByRole('button', { name: /^In Progress/ }))

    await waitFor(() => expect(onRetry).toHaveBeenCalledTimes(1))
    expect(screen.getByRole('alert')).toHaveTextContent('This task changed after it was loaded.')
    expect(screen.getAllByText('Review discovery responses').length).toBeGreaterThan(0)
  })

  it('names the recipients before an approval that will send a client email', async () => {
    const user = userEvent.setup()
    const drafted = {
      ...task,
      id: 'task-draft',
      title: 'Request insurance certificate',
      status: 'review',
      source: 'assistant',
      reviewer: { id: 'user-1', label: 'Test Attorney' },
      pending_action: {
        type: 'email_client',
        to: ['gc@redwood.example'],
        subject: 'Certificate of insurance',
        body: 'Please send the current certificate.',
        matter_id: 'matter-1',
        source_ids: [],
      },
    }
    const draftedData = {
      ...data,
      columns: data.columns.map((column) =>
        column.status === 'review'
          ? { ...column, total: 1, items: [drafted] }
          : { ...column, total: 0, items: [] },
      ),
    }
    render(<TaskBoard {...props} data={draftedData} />)

    // Visible on the card itself, not only in the dialog.
    expect(screen.getAllByTestId('pending-action-badge')[0]).toHaveTextContent(
      'Approving emails gc@redwood.example',
    )
    expect(screen.getAllByText(/Drafted by the assistant/).length).toBeGreaterThan(0)

    await user.click(
      screen.getAllByRole('button', {
        name: 'Choose a destination for Request insurance certificate',
      })[0],
    )
    await user.click(screen.getByRole('button', { name: /^In Progress/ }))

    const notice = await screen.findByRole('note')
    expect(notice).toHaveTextContent('This approval sends an email')
    expect(notice).toHaveTextContent('gc@redwood.example')
  })

  it('does not warn about sending for an ordinary review task', async () => {
    const user = userEvent.setup()
    const plain = { ...task, id: 'task-plain', status: 'review', pending_action: null }
    const plainData = {
      ...data,
      columns: data.columns.map((column) =>
        column.status === 'review'
          ? { ...column, total: 1, items: [plain] }
          : { ...column, total: 0, items: [] },
      ),
    }
    render(<TaskBoard {...props} data={plainData} />)

    expect(screen.queryByTestId('pending-action-badge')).not.toBeInTheDocument()

    await user.click(
      screen.getAllByRole('button', {
        name: 'Choose a destination for Review discovery responses',
      })[0],
    )
    await user.click(screen.getByRole('button', { name: /^In Progress/ }))

    expect(screen.queryByRole('note')).not.toBeInTheDocument()
  })
})
