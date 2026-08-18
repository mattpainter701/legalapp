import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TaskBoard from './TaskBoard'
import { getTask, getTaskEvents, updateTaskPendingAction } from '../../api'

vi.mock('../../api', () => ({
  API_BASE_URL: '/api',
  getTask: vi.fn(),
  getTaskEvents: vi.fn(),
  searchUsers: vi.fn().mockResolvedValue([]),
  updateTaskPendingAction: vi.fn(),
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

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

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

  it('shows and version-guards edits to the authoritative outbound draft', async () => {
    const user = userEvent.setup()
    let current = {
      ...task,
      id: 'task-email-draft',
      title: 'Send client update',
      status: 'review',
      version: 3,
      source: 'assistant',
      description: 'STALE DESCRIPTION COPY',
      pending_action: {
        type: 'email_client',
        to: ['client@example.com'],
        subject: 'Original subject',
        body: 'Original authoritative body.',
        sources: [{
          source_id: 'document:agreement',
          label: 'Agreement.pdf',
          url: '/api/documents/agreement/download',
        }],
      },
    }
    getTask.mockImplementation(async () => current)
    updateTaskPendingAction.mockImplementation(async (_id, payload) => {
      current = {
        ...current,
        version: 4,
        pending_action: {
          ...current.pending_action,
          subject: payload.subject,
          body: payload.body,
        },
      }
      return current
    })
    const draftedData = {
      ...data,
      columns: data.columns.map((column) => (
        column.status === 'review'
          ? { ...column, total: 1, items: [{ ...current, description: undefined }] }
          : { ...column, total: 0, items: [] }
      )),
    }

    render(<TaskBoard {...props} data={draftedData} taskId="task-email-draft" />)

    expect(await screen.findByText('Authoritative outbound email draft')).toBeInTheDocument()
    expect(screen.getByText('Original authoritative body.')).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'Agreement.pdf' })[0]).toHaveAttribute(
      'href',
      '/api/documents/agreement/download',
    )
    expect(screen.queryByText('STALE DESCRIPTION COPY')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Edit draft' }))
    await user.clear(screen.getByLabelText('Subject'))
    await user.type(screen.getByLabelText('Subject'), 'Revised subject')
    await user.clear(screen.getByLabelText('Email body'))
    await user.type(screen.getByLabelText('Email body'), 'Revised authoritative body.')
    expect(screen.getByRole('button', { name: /^Move to/ })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Save outbound draft' }))

    await waitFor(() => expect(updateTaskPendingAction).toHaveBeenCalledWith(
      'task-email-draft',
      {
        subject: 'Revised subject',
        body: 'Revised authoritative body.',
        expected_version: 3,
      },
    ))
    expect(await screen.findByText('Revised authoritative body.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Move to/ })).toBeEnabled()
  })

  it('treats an assistant document as a focused review workspace and approval handoff', async () => {
    const user = userEvent.setup()
    let current = {
      ...task,
      id: 'task-document-draft',
      title: 'Review document: Client status letter',
      status: 'review',
      version: 2,
      source: 'assistant',
      pending_action: {
        type: 'matter_document_draft',
        title: 'Client status letter',
        body: 'Dear Client,\n\nThis letter provides the current matter status.',
        sources: [{ source_id: 'matter:1', label: 'Matter notes', url: '' }],
      },
    }
    getTask.mockImplementation(async () => current)
    updateTaskPendingAction.mockImplementation(async (_id, payload) => {
      current = {
        ...current,
        version: 3,
        pending_action: { ...current.pending_action, title: payload.title, body: payload.body },
      }
      return current
    })
    props.onTransition.mockResolvedValue({ ...current, status: 'in_progress', version: 4 })
    const draftedData = {
      ...data,
      columns: data.columns.map((column) => (
        column.status === 'review'
          ? { ...column, total: 1, items: [current] }
          : { ...column, total: 0, items: [] }
      )),
    }

    render(<TaskBoard {...props} data={draftedData} taskId="task-document-draft" />)

    expect(await screen.findByText('Word document draft')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Open draft workspace' }))
    expect(screen.getByRole('dialog', { name: 'Client status letter' })).toBeInTheDocument()
    expect(screen.getByText('No template attached')).toBeInTheDocument()

    await user.clear(screen.getByRole('textbox', { name: 'Document text' }))
    await user.type(screen.getByRole('textbox', { name: 'Document text' }), 'Dear Client,\n\nThe hearing is set for September 14.')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(updateTaskPendingAction).toHaveBeenCalledWith(
      'task-document-draft',
      expect.objectContaining({
        title: 'Client status letter',
        body: 'Dear Client,\n\nThe hearing is set for September 14.',
        expected_version: 2,
      }),
    ))
    await user.click(screen.getByRole('button', { name: 'Review and approve' }))

    expect(screen.getByRole('dialog', { name: 'Approve and file document' })).toBeInTheDocument()
    expect(screen.getByText(/convert.*Client status letter.*editable Word file/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Approve document' }))
    await waitFor(() => expect(props.onTransition).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'task-document-draft', version: 3 }),
      'in_progress',
      expect.any(Object),
    ))
  })

  it('loads the immutable delivery action snapshot from task detail after the live draft clears', async () => {
    const boardCard = {
      ...task,
      id: 'task-sent-email',
      title: 'Sent client update',
      status: 'in_progress',
      version: 5,
      source: 'assistant',
      pending_action: null,
      delivery: { status: 'sent', provider: 'microsoft' },
    }
    getTask.mockResolvedValue({
      ...boardCard,
      delivery: {
        status: 'sent',
        action_type: 'email_client',
        provider: 'microsoft',
        provider_message_id: 'message-123',
        action_sha256: 'abc123',
        delivery_detail: 'Accepted by Microsoft Graph',
        action_snapshot: {
          type: 'email_client',
          to: ['client@example.com'],
          subject: 'Final sent subject',
          body: 'Final immutable sent body.',
          sources: [{
            source_id: 'courtlistener:4242',
            label: 'Redwood v. North Dakota',
            url: 'https://www.courtlistener.com/opinion/4242/',
          }],
        },
      },
    })
    const sentData = {
      ...data,
      columns: data.columns.map((column) => (
        column.status === 'in_progress'
          ? { ...column, total: 1, items: [boardCard] }
          : { ...column, total: 0, items: [] }
      )),
    }

    render(<TaskBoard {...props} data={sentData} taskId="task-sent-email" />)

    expect(await screen.findByText('Immutable delivery audit snapshot')).toBeInTheDocument()
    expect(screen.getByText('Final sent subject')).toBeInTheDocument()
    expect(screen.getByText('Final immutable sent body.')).toBeInTheDocument()
    expect(screen.getByText('message-123')).toBeInTheDocument()
    expect(screen.getByText('Accepted by Microsoft Graph')).toBeInTheDocument()
    expect(screen.getByText('abc123')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Redwood v. North Dakota' })).toHaveAttribute(
      'href',
      'https://www.courtlistener.com/opinion/4242/',
    )
    expect(screen.queryByRole('button', { name: 'Edit draft' })).not.toBeInTheDocument()
    expect(getTask).toHaveBeenCalledWith('task-sent-email')
  })

  it('renders every immutable delivery attempt with its recorded payload and provider evidence', async () => {
    const latestAttempt = {
      id: 'attempt-2',
      status: 'sent',
      delivery_certainty: 'confirmed_sent',
      provider: 'google',
      provider_message_id: 'google-message-2',
      action_sha256: 'hash-latest',
      action_snapshot: {
        type: 'email_client',
        to: ['client@example.com'],
        subject: 'Latest approved subject',
        body: 'Latest immutable body.',
        sources: [],
      },
    }
    const priorAttempt = {
      id: 'attempt-1',
      status: 'failed',
      delivery_certainty: 'outcome_unknown',
      provider: 'microsoft',
      provider_message_id: 'microsoft-message-1',
      action_sha256: 'hash-prior',
      delivery_detail: 'Connection closed after submission',
      action_snapshot: {
        type: 'email_client',
        to: ['client@example.com'],
        subject: 'Prior approved subject',
        body: 'Prior immutable body.',
        sources: [{
          source_id: 'courtlistener:99',
          label: 'Prior cited authority',
          url: 'https://www.courtlistener.com/opinion/99/',
        }],
      },
    }
    const boardCard = {
      ...task,
      id: 'task-delivery-history',
      title: 'Client delivery history',
      status: 'in_progress',
      source: 'assistant',
      pending_action: null,
      delivery: latestAttempt,
    }
    getTask.mockResolvedValue({
      ...boardCard,
      delivery_history: [latestAttempt, priorAttempt],
    })
    const historyData = {
      ...data,
      columns: data.columns.map((column) => (
        column.status === 'in_progress'
          ? { ...column, total: 1, items: [boardCard] }
          : { ...column, total: 0, items: [] }
      )),
    }

    render(<TaskBoard {...props} data={historyData} taskId="task-delivery-history" />)

    const heading = await screen.findByRole('heading', { name: 'Delivery attempts' })
    const history = within(heading.closest('section'))
    expect(history.getByText(/Attempt 2: sent.*confirmed sent/i)).toBeInTheDocument()
    expect(history.getByText(/Attempt 1: failed.*outcome unknown/i)).toBeInTheDocument()
    expect(history.getByText('Latest immutable body.')).toBeInTheDocument()
    expect(history.getByText('Prior immutable body.')).toBeInTheDocument()
    expect(history.getByText('google-message-2')).toBeInTheDocument()
    expect(history.getByText('microsoft-message-1')).toBeInTheDocument()
    expect(history.getByText('hash-latest')).toBeInTheDocument()
    expect(history.getByText('hash-prior')).toBeInTheDocument()
    expect(history.getByRole('link', { name: 'Prior cited authority' })).toHaveAttribute(
      'href',
      'https://www.courtlistener.com/opinion/99/',
    )
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

  it('states that non-approval moves leave a Review email unsent', async () => {
    const user = userEvent.setup()
    const drafted = {
      ...task,
      id: 'task-unsent-draft',
      title: 'Request insurance certificate',
      status: 'review',
      source: 'assistant',
      pending_action: {
        type: 'email_client',
        to: ['gc@redwood.example'],
        subject: 'Certificate of insurance',
        body: 'Please send the current certificate.',
        source_ids: [],
      },
    }
    const draftedData = {
      ...data,
      columns: data.columns.map((column) => (
        column.status === 'review'
          ? { ...column, total: 1, items: [drafted] }
          : { ...column, total: 0, items: [] }
      )),
    }
    render(<TaskBoard {...props} data={draftedData} />)

    await user.click(screen.getAllByRole('button', {
      name: 'Choose a destination for Request insurance certificate',
    })[0])
    await user.click(screen.getByRole('button', { name: /^Waiting/ }))

    const notice = await screen.findByRole('note')
    expect(notice).toHaveTextContent('This move does not send the email')
    expect(notice).toHaveTextContent('Only moving this task from Review to In Progress')
    expect(notice).not.toHaveTextContent('This approval sends an email')
  })

  it('requires duplicate-risk acknowledgment for an outcome-unknown retry', async () => {
    const user = userEvent.setup()
    const onTransition = vi.fn().mockResolvedValue({ ...task, status: 'in_progress', version: 3 })
    const drafted = {
      ...task,
      id: 'task-unknown-retry',
      title: 'Retry client update',
      status: 'review',
      source: 'assistant',
      pending_action: {
        type: 'email_client',
        to: ['client@example.com'],
        subject: 'Client update',
        body: 'Reviewed body.',
      },
      delivery: {
        status: 'failed',
        delivery_certainty: 'outcome_unknown',
        error_message: 'Provider response was interrupted',
      },
    }
    const draftedData = {
      ...data,
      columns: data.columns.map((column) => (
        column.status === 'review'
          ? { ...column, total: 1, items: [drafted] }
          : { ...column, total: 0, items: [] }
      )),
    }
    render(<TaskBoard {...props} data={draftedData} onTransition={onTransition} />)

    await user.click(screen.getAllByRole('button', { name: 'Choose a destination for Retry client update' })[0])
    await user.click(screen.getByRole('button', { name: /^In Progress/ }))
    const dialog = screen.getByRole('dialog', { name: 'Move to In Progress' })
    const confirm = screen.getByRole('button', { name: 'Move task' })
    expect(confirm).toBeDisabled()
    expect(within(dialog).getByRole('alert')).toHaveTextContent(/could send a duplicate/i)
    await user.click(screen.getByRole('checkbox', { name: /checked.*Sent Items/i }))
    expect(confirm).toBeEnabled()
    await user.click(confirm)

    await waitFor(() => expect(onTransition).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'task-unknown-retry' }),
      'in_progress',
      expect.objectContaining({ acknowledge_prior_delivery_risk: true }),
    ))
  })

  it('allows a confirmed-no-attempt retry without duplicate-risk acknowledgment', async () => {
    const user = userEvent.setup()
    const onTransition = vi.fn().mockResolvedValue({ ...task, status: 'in_progress', version: 3 })
    const drafted = {
      ...task,
      id: 'task-not-attempted-retry',
      title: 'Retry unsent update',
      status: 'review',
      source: 'assistant',
      pending_action: {
        type: 'email_client',
        to: ['client@example.com'],
        subject: 'Client update',
        body: 'Reviewed body.',
      },
      delivery: {
        status: 'failed',
        delivery_certainty: 'not_attempted',
        error_message: 'Reconnect Microsoft 365',
      },
    }
    const draftedData = {
      ...data,
      columns: data.columns.map((column) => (
        column.status === 'review'
          ? { ...column, total: 1, items: [drafted] }
          : { ...column, total: 0, items: [] }
      )),
    }
    render(<TaskBoard {...props} data={draftedData} onTransition={onTransition} />)

    await user.click(screen.getAllByRole('button', { name: 'Choose a destination for Retry unsent update' })[0])
    await user.click(screen.getByRole('button', { name: /^In Progress/ }))
    const dialog = screen.getByRole('dialog', { name: 'Move to In Progress' })
    expect(within(dialog).getByRole('alert')).toHaveTextContent(/Email was not sent/i)
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Move task' }))

    await waitFor(() => expect(onTransition).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'task-not-attempted-retry' }),
      'in_progress',
      expect.not.objectContaining({ acknowledge_prior_delivery_risk: true }),
    ))
  })

  it.each([
    ['queued', 'still queued'],
    ['sending', 'still sending'],
    ['sent', 'already confirmed sent'],
  ])('blocks approval when the prior delivery is %s', async (deliveryStatus, message) => {
    const user = userEvent.setup()
    const drafted = {
      ...task,
      id: `task-${deliveryStatus}-delivery`,
      title: `Blocked ${deliveryStatus} update`,
      status: 'review',
      source: 'assistant',
      pending_action: {
        type: 'email_client',
        to: ['client@example.com'],
        subject: 'Client update',
        body: 'Reviewed body.',
      },
      delivery: {
        status: deliveryStatus,
        delivery_certainty: deliveryStatus === 'sent' ? 'confirmed_sent' : 'not_attempted',
      },
    }
    const draftedData = {
      ...data,
      columns: data.columns.map((column) => (
        column.status === 'review'
          ? { ...column, total: 1, items: [drafted] }
          : { ...column, total: 0, items: [] }
      )),
    }
    render(<TaskBoard {...props} data={draftedData} />)

    await user.click(screen.getAllByRole('button', { name: `Choose a destination for Blocked ${deliveryStatus} update` })[0])
    await user.click(screen.getByRole('button', { name: /^In Progress/ }))
    expect(screen.getByRole('button', { name: 'Move task' })).toBeDisabled()
    expect(screen.getAllByRole('status').at(-1)).toHaveTextContent(message)
    expect(props.onTransition).not.toHaveBeenCalled()
  })

  it('links verified action sources and reports every delivery state honestly', () => {
    const sourced = {
      ...task,
      id: 'task-delivery',
      title: 'Send cited client update',
      status: 'in_progress',
      source: 'assistant',
      pending_action: {
        type: 'email_client',
        to: ['gc@redwood.example'],
        subject: 'Authority update',
        body: 'Please review the authority.',
        sources: [{
          source_id: 'courtlistener:4242',
          label: 'Redwood v. North Dakota',
          citation: '2026 ND 42',
          locator: 'Paragraph 12',
          url: 'https://www.courtlistener.com/opinion/4242/',
        }],
      },
      delivery: { status: 'failed', error_message: 'Outcome unknown after worker interruption' },
    }
    const sourcedData = {
      ...data,
      columns: data.columns.map((column) => (
        column.status === 'in_progress'
          ? { ...column, total: 1, items: [sourced] }
          : { ...column, total: 0, items: [] }
      )),
    }
    const { rerender } = render(<TaskBoard {...props} data={sourcedData} />)

    expect(screen.getAllByRole('link', { name: /Redwood v\. North Dakota/ })[0]).toHaveAttribute(
      'href',
      'https://www.courtlistener.com/opinion/4242/',
    )
    expect(screen.getAllByRole('alert')[0]).toHaveTextContent(/Delivery not confirmed/i)
    expect(screen.getAllByRole('alert')[0]).toHaveTextContent(/Sent Items.*duplicate/i)

    const queuedData = {
      ...sourcedData,
      columns: sourcedData.columns.map((column) => ({
        ...column,
        items: column.items.map((item) => ({
          ...item,
          delivery: { status: 'queued' },
        })),
      })),
    }
    rerender(<TaskBoard {...props} data={queuedData} />)
    expect(screen.getAllByRole('status')[0]).toHaveTextContent(/queued.*Not yet confirmed sent/i)

    const sendingData = {
      ...queuedData,
      columns: queuedData.columns.map((column) => ({
        ...column,
        items: column.items.map((item) => ({
          ...item,
          delivery: { status: 'sending' },
        })),
      })),
    }
    rerender(<TaskBoard {...props} data={sendingData} />)
    expect(screen.getAllByRole('status')[0]).toHaveTextContent(/in progress.*Not yet confirmed sent/i)
  })

  it('stops bounded delivery refresh as soon as the server reports a terminal outcome', async () => {
    vi.useFakeTimers()
    const queuedTask = {
      ...task,
      id: 'task-queued-delivery',
      status: 'in_progress',
      source: 'assistant',
      version: 4,
      delivery: { status: 'queued' },
    }
    const queuedData = {
      ...data,
      columns: data.columns.map((column) => (
        column.status === 'in_progress'
          ? { ...column, total: 1, items: [queuedTask] }
          : { ...column, total: 0, items: [] }
      )),
    }
    const terminalData = {
      ...queuedData,
      columns: queuedData.columns.map((column) => ({
        ...column,
        items: column.items.map((item) => ({
          ...item,
          delivery: { status: 'sent', provider: 'google' },
        })),
      })),
    }
    const onRetry = vi.fn().mockResolvedValue(terminalData)
    render(<TaskBoard {...props} data={queuedData} onRetry={onRetry} />)

    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    expect(onRetry).toHaveBeenCalledTimes(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(30000) })
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('caps delivery refresh when queued state never becomes terminal', async () => {
    vi.useFakeTimers()
    const queuedTask = {
      ...task,
      id: 'task-stuck-delivery',
      status: 'in_progress',
      source: 'assistant',
      version: 9,
      delivery: { status: 'queued' },
    }
    const queuedData = {
      ...data,
      columns: data.columns.map((column) => (
        column.status === 'in_progress'
          ? { ...column, total: 1, items: [queuedTask] }
          : { ...column, total: 0, items: [] }
      )),
    }
    const onRetry = vi.fn().mockResolvedValue(queuedData)
    render(<TaskBoard {...props} data={queuedData} onRetry={onRetry} />)

    for (let attempt = 0; attempt < 12; attempt += 1) {
      await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    }
    expect(onRetry).toHaveBeenCalledTimes(8)
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
