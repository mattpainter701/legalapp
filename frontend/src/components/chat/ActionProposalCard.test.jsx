import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ActionProposalCard from './ActionProposalCard'

const emailProposal = {
  task_id: 'task-9',
  title: 'Request insurance certificate from Redwood',
  status: 'review',
  matter_id: 'matter-3',
  due_date: '2026-09-15',
  action_type: 'email_client',
  approval_effect:
    'Approving sends this email to gc@redwood.example. Edit the draft first if anything is wrong.',
  pending_action: {
    type: 'email_client',
    to: ['gc@redwood.example'],
    subject: 'Certificate of insurance',
    body: 'Please send the current certificate of insurance.',
    matter_id: 'matter-3',
    source_ids: [],
  },
}

const taskProposal = {
  task_id: 'task-10',
  title: 'Diary the Evergreen non-renewal deadline',
  status: 'review',
  action_type: null,
  approval_effect: 'Approving moves this task into active work. Nothing is sent.',
  pending_action: null,
}

afterEach(cleanup)

describe('assistant action proposals in chat', () => {
  it('links verified authorities and never creates an empty source link', async () => {
    render(
      <ActionProposalCard
        proposal={{
          ...taskProposal,
          sources: [
            {
              source_id: 'courtlistener:4242',
              label: 'Redwood v. North Dakota',
              citation: '2026 ND 42',
              locator: 'Paragraph 12',
              url: 'https://www.courtlistener.com/opinion/4242/',
            },
            {
              source_id: 'authority:no-link',
              label: 'Internal authority note',
              url: 'javascript:alert(1)',
            },
          ],
        }}
        onApprove={vi.fn()}
        onLoadTask={vi.fn().mockResolvedValue({
          id: taskProposal.task_id,
          title: taskProposal.title,
          status: 'review',
          version: 3,
          pending_action: null,
          delivery: null,
        })}
      />,
    )

    expect(await screen.findByRole('link', { name: /Redwood v\. North Dakota/ })).toHaveAttribute(
      'href',
      'https://www.courtlistener.com/opinion/4242/',
    )
    expect(screen.getByText('Internal authority note').closest('a')).toBeNull()
  })

  it('rehydrates a stale snapshot and never offers approval for a completed task', async () => {
    const onApprove = vi.fn()
    const onLoadTask = vi.fn().mockResolvedValue({
      id: emailProposal.task_id,
      title: emailProposal.title,
      status: 'completed',
      version: 12,
      due_date: emailProposal.due_date,
      pending_action: emailProposal.pending_action,
      delivery: null,
    })

    render(
      <ActionProposalCard
        proposal={emailProposal}
        onApprove={onApprove}
        onLoadTask={onLoadTask}
      />,
    )

    expect(screen.queryByRole('button', { name: /approve and send/i })).not.toBeInTheDocument()
    expect(await screen.findByText(/Current status: Completed/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /approve and send/i })).not.toBeInTheDocument()
    expect(onApprove).not.toHaveBeenCalled()
  })

  it('fails closed when a stale email snapshot has no immutable delivery evidence', async () => {
    render(
      <ActionProposalCard
        proposal={{ ...emailProposal, version: 2, sources: [{ source_id: 'old', label: 'Old source' }] }}
        onApprove={vi.fn()}
        onLoadTask={vi.fn().mockResolvedValue({
          id: emailProposal.task_id,
          title: emailProposal.title,
          status: 'review',
          version: 9,
          pending_action: null,
          delivery: { status: 'sent', action_type: 'email_client' },
        })}
      />,
    )

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/historical email draft.*no longer attached/i)
    expect(alert).toHaveTextContent(/Nothing can be approved or sent/i)
    expect(screen.queryByRole('button', { name: /approve/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /edit draft/i })).not.toBeInTheDocument()
    expect(screen.queryByText('Old source')).not.toBeInTheDocument()
  })

  it('restores confirmed sent truth from the immutable payload after the live draft is cleared', async () => {
    const onApprove = vi.fn()
    const onAwaitDelivery = vi.fn()
    const recordedAction = {
      ...emailProposal.pending_action,
      body: 'Exact body accepted for delivery.',
      sources: [{
        source_id: 'document:insurance',
        label: 'Insurance request.pdf',
        url: '/api/documents/insurance/download',
      }],
    }

    render(
      <ActionProposalCard
        proposal={emailProposal}
        onApprove={onApprove}
        onAwaitDelivery={onAwaitDelivery}
        onLoadTask={vi.fn().mockResolvedValue({
          id: emailProposal.task_id,
          title: emailProposal.title,
          status: 'in_progress',
          version: 13,
          pending_action: null,
          delivery: {
            status: 'sent',
            action_type: 'email_client',
            action_snapshot: recordedAction,
            provider: 'microsoft',
            provider_message_id: 'message-123',
          },
        })}
      />,
    )

    expect(await screen.findByText('Recorded delivery payload')).toBeInTheDocument()
    expect(await screen.findByText('Exact body accepted for delivery.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Insurance request.pdf' })).toHaveAttribute(
      'href',
      '/api/documents/insurance/download',
    )
    expect(screen.getByText(/Sent to the client/i)).toBeInTheDocument()
    expect(screen.queryByText(/historical email draft/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /approve/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /edit draft/i })).not.toBeInTheDocument()
    expect(onApprove).not.toHaveBeenCalled()
    expect(onAwaitDelivery).not.toHaveBeenCalled()
  })

  it.each([
    {
      status: 'failed',
      task_status: 'in_progress',
      error_message: 'Microsoft Graph timed out after accepting the request',
      expected: /Delivery was not confirmed.*Microsoft Graph timed out/i,
      role: 'alert',
    },
    {
      status: 'queued',
      task_status: 'review',
      error_message: null,
      expected: /Approved.*Not yet confirmed sent/i,
      role: 'status',
    },
  ])('treats immutable $status delivery evidence as consumed without claiming success', async ({
    status,
    task_status,
    error_message,
    expected,
    role,
  }) => {
    render(
      <ActionProposalCard
        proposal={emailProposal}
        onApprove={vi.fn()}
        onLoadTask={vi.fn().mockResolvedValue({
          id: emailProposal.task_id,
          title: emailProposal.title,
          status: task_status,
          version: 14,
          pending_action: null,
          delivery: {
            status,
            action_type: 'email_client',
            error_message,
            action_snapshot: {
              ...emailProposal.pending_action,
              body: `Immutable ${status} payload.`,
            },
          },
        })}
      />,
    )

    expect(await screen.findByText(`Immutable ${status} payload.`)).toBeInTheDocument()
    expect(screen.getByText('Recorded action outcome')).toBeInTheDocument()
    expect(screen.getByRole(role)).toHaveTextContent(expected)
    expect(screen.queryByText(/Sent to the client/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/historical email draft/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /approve/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /edit draft/i })).not.toBeInTheDocument()
  })

  it('restores confirmed delivery truth from the live task after a reload', async () => {
    const onAwaitDelivery = vi.fn()
    render(
      <ActionProposalCard
        proposal={emailProposal}
        onApprove={vi.fn()}
        onLoadTask={vi.fn().mockResolvedValue({
          id: emailProposal.task_id,
          title: emailProposal.title,
          status: 'in_progress',
          version: 13,
          pending_action: emailProposal.pending_action,
          delivery: { status: 'sent', action_type: 'email_client' },
        })}
        onAwaitDelivery={onAwaitDelivery}
      />,
    )

    expect(await screen.findByText(/Sent to the client/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /approve and send/i })).not.toBeInTheDocument()
    expect(onAwaitDelivery).not.toHaveBeenCalled()
  })

  it('requires explicit Sent Items acknowledgment before an outcome-unknown retry', async () => {
    const onApprove = vi.fn().mockResolvedValue({})
    const failedAttempt = {
      id: 'attempt-failed',
      status: 'failed',
      action_type: 'email_client',
      delivery_certainty: 'outcome_unknown',
      error_message: 'Provider response was interrupted',
      action_snapshot: emailProposal.pending_action,
    }
    render(
      <ActionProposalCard
        proposal={emailProposal}
        onApprove={onApprove}
        onLoadTask={vi.fn().mockResolvedValue({
          id: emailProposal.task_id,
          title: emailProposal.title,
          status: 'review',
          version: 17,
          pending_action: emailProposal.pending_action,
          delivery: failedAttempt,
          delivery_history: [failedAttempt],
        })}
      />,
    )

    const approve = await screen.findByRole('button', { name: /approve and send/i })
    expect(approve).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent(/Delivery was not confirmed/i)
    const acknowledgment = screen.getByRole('checkbox', { name: /checked.*Sent Items/i })
    await userEvent.click(acknowledgment)
    expect(approve).toBeEnabled()
    await userEvent.click(approve)

    await waitFor(() => expect(onApprove).toHaveBeenCalledWith(
      expect.objectContaining({ version: 17 }),
      { acknowledge_prior_delivery_risk: true },
    ))
  })

  it('blocks a duplicate approval if a confirmed send is returned with a review task', async () => {
    render(
      <ActionProposalCard
        proposal={emailProposal}
        onApprove={vi.fn()}
        onLoadTask={vi.fn().mockResolvedValue({
          id: emailProposal.task_id,
          title: emailProposal.title,
          status: 'review',
          version: 18,
          pending_action: emailProposal.pending_action,
          delivery: {
            id: 'attempt-confirmed',
            status: 'sent',
            action_type: 'email_client',
            delivery_certainty: 'confirmed_sent',
          },
        })}
      />,
    )

    expect(await screen.findByText(/already confirmed sent/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /approve and send/i })).not.toBeInTheDocument()
  })

  it('allows an exact confirmed-no-send retry without a duplicate-risk acknowledgment', async () => {
    const onApprove = vi.fn().mockResolvedValue({})
    const failedAttempt = {
      id: 'attempt-not-sent',
      status: 'failed',
      action_type: 'email_client',
      delivery_certainty: 'not_attempted',
      error_message: 'Reconnect Microsoft 365',
      action_snapshot: emailProposal.pending_action,
    }
    render(
      <ActionProposalCard
        proposal={emailProposal}
        onApprove={onApprove}
        onLoadTask={vi.fn().mockResolvedValue({
          id: emailProposal.task_id,
          title: emailProposal.title,
          status: 'review',
          version: 18,
          pending_action: emailProposal.pending_action,
          delivery: failedAttempt,
          delivery_history: [failedAttempt],
        })}
      />,
    )

    expect(await screen.findByRole('alert')).toHaveTextContent(/Email was not sent/i)
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /approve and send/i }))
    await waitFor(() => expect(onApprove).toHaveBeenCalledWith(
      expect.objectContaining({ version: 18 }),
      undefined,
    ))
  })

  it('blocks another approval while a delivery is queued and retains prior immutable evidence', async () => {
    const prior = {
      id: 'attempt-prior',
      status: 'failed',
      action_type: 'email_client',
      delivery_certainty: 'outcome_unknown',
      error_message: 'Outcome unknown',
      action_snapshot: {
        ...emailProposal.pending_action,
        body: 'Immutable prior attempt body.',
      },
    }
    const queued = {
      id: 'attempt-current',
      status: 'queued',
      action_type: 'email_client',
      delivery_certainty: 'not_attempted',
      action_snapshot: emailProposal.pending_action,
    }
    render(
      <ActionProposalCard
        proposal={emailProposal}
        onApprove={vi.fn()}
        onLoadTask={vi.fn().mockResolvedValue({
          id: emailProposal.task_id,
          title: emailProposal.title,
          status: 'review',
          version: 19,
          pending_action: emailProposal.pending_action,
          delivery: queued,
          delivery_history: [queued, prior],
        })}
      />,
    )

    expect(await screen.findByText(/Another delivery attempt is still queued/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /approve and send/i })).not.toBeInTheDocument()
    await userEvent.click(screen.getByText(/Attempt 1: failed/i))
    expect(screen.getByText('Immutable prior attempt body.')).toBeInTheDocument()
  })

  it('approves the live review version rather than the stale message snapshot', async () => {
    const onApprove = vi.fn().mockResolvedValue({
      id: emailProposal.task_id,
      status: 'in_progress',
      version: 8,
      pending_action: { ...emailProposal.pending_action, body: 'Live revised draft.' },
      delivery: { status: 'queued', action_type: 'email_client' },
    })
    render(
      <ActionProposalCard
        proposal={{ ...emailProposal, version: 2 }}
        onApprove={onApprove}
        onLoadTask={vi.fn().mockResolvedValue({
          id: emailProposal.task_id,
          title: emailProposal.title,
          status: 'review',
          version: 7,
          pending_action: { ...emailProposal.pending_action, body: 'Live revised draft.' },
          delivery: null,
        })}
      />,
    )

    await userEvent.click(await screen.findByRole('button', { name: /approve and send/i }))
    await waitFor(() => expect(onApprove).toHaveBeenCalledWith(
      expect.objectContaining({ version: 7, status: 'review' }),
      undefined,
    ))
  })

  it('fails closed when the live task cannot be verified', async () => {
    const onApprove = vi.fn()
    render(
      <ActionProposalCard
        proposal={emailProposal}
        onApprove={onApprove}
        onLoadTask={vi.fn().mockRejectedValue(new Error('Task service unavailable'))}
      />,
    )

    expect(await screen.findByRole('alert')).toHaveTextContent('Task service unavailable')
    expect(screen.getByRole('button', { name: /retry task status/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /approve and send/i })).not.toBeInTheDocument()
    expect(onApprove).not.toHaveBeenCalled()
  })

  it('rehydrates a conflict snapshot and removes approval when another actor already transitioned it', async () => {
    const message = 'This task changed after it was loaded. Review the latest task and try again.'
    const currentTask = {
      id: emailProposal.task_id,
      title: emailProposal.title,
      status: 'in_progress',
      version: 8,
      pending_action: emailProposal.pending_action,
      delivery: { status: 'sent', action_type: 'email_client' },
    }
    const conflict = Object.assign(new Error(message), {
      // Match the raw FastAPI response shape. The API helper normally
      // normalizes this, but the card must also fail closed when a caller
      // passes through the structured detail unchanged.
      response: {
        status: 409,
        data: { detail: { message, current_task: currentTask } },
      },
    })
    const onApprove = vi.fn().mockRejectedValue(conflict)
    render(
      <ActionProposalCard
        proposal={emailProposal}
        onApprove={onApprove}
        onLoadTask={vi.fn().mockResolvedValue({
          ...currentTask,
          status: 'review',
          version: 7,
          delivery: null,
        })}
      />,
    )

    await userEvent.click(await screen.findByRole('button', { name: /approve and send/i }))

    expect(await screen.findByText(/Sent to the client/i)).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent(message)
    expect(screen.queryByRole('button', { name: /approve and send/i })).not.toBeInTheDocument()
  })

  it('states who will be emailed before the attorney can approve', () => {
    render(<ActionProposalCard proposal={emailProposal} onApprove={vi.fn()} />)

    expect(screen.getByText(/Approving sends this email to gc@redwood.example/)).toBeInTheDocument()
    expect(screen.getByText('gc@redwood.example')).toBeInTheDocument()
    expect(screen.getByText('Certificate of insurance')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /approve and send/i })).toBeInTheDocument()
  })

  it('does not offer to send for a plain task proposal', () => {
    render(<ActionProposalCard proposal={taskProposal} onApprove={vi.fn()} />)

    expect(screen.getByText(/Nothing is sent/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^approve$/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /edit draft/i })).not.toBeInTheDocument()
  })

  it('does not claim the client was contacted before delivery completes', async () => {
    const onApprove = vi.fn().mockResolvedValue({})
    render(<ActionProposalCard proposal={emailProposal} onApprove={onApprove} />)

    await userEvent.click(screen.getByRole('button', { name: /approve and send/i }))

    // Delivery runs out-of-band, so "sent" would be a claim we cannot back.
    const status = await screen.findByRole('status')
    expect(status).toHaveTextContent(/Not yet confirmed sent/i)
    expect(status).not.toHaveTextContent(/Sent to the client/i)
  })

  it('approves without edits when the draft is untouched', async () => {
    const onApprove = vi.fn().mockResolvedValue({})
    render(<ActionProposalCard proposal={emailProposal} onApprove={onApprove} />)

    await userEvent.click(screen.getByRole('button', { name: /approve and send/i }))

    // undefined edits means the caller skips the draft PATCH entirely.
    await waitFor(() => expect(onApprove).toHaveBeenCalledWith(emailProposal, undefined))
    expect(await screen.findByText(/Not yet confirmed sent/i)).toBeInTheDocument()
  })

  it('sends the edited body when the attorney rewrites the draft', async () => {
    const onApprove = vi.fn().mockResolvedValue({})
    render(<ActionProposalCard proposal={emailProposal} onApprove={onApprove} />)

    await userEvent.click(screen.getByRole('button', { name: /edit draft/i }))
    const textarea = screen.getByRole('textbox')
    await userEvent.clear(textarea)
    await userEvent.type(textarea, 'Please send the certificate by Friday.')
    await userEvent.click(screen.getByRole('button', { name: /approve and send/i }))

    await waitFor(() =>
      expect(onApprove).toHaveBeenCalledWith(emailProposal, {
        body: 'Please send the certificate by Friday.',
      }),
    )
  })

  it('never exposes recipients as an editable field', async () => {
    render(<ActionProposalCard proposal={emailProposal} onApprove={vi.fn()} />)

    await userEvent.click(screen.getByRole('button', { name: /edit draft/i }))

    // Exactly one editable control, the body. Recipients are resolved
    // server-side from the matter's parties and must stay unwritable here.
    expect(screen.getAllByRole('textbox')).toHaveLength(1)
  })

  it('surfaces a failed approval and lets the attorney retry', async () => {
    const onApprove = vi
      .fn()
      .mockRejectedValueOnce({ response: { data: { detail: 'Task changed' } } })
      .mockResolvedValueOnce({})
    render(<ActionProposalCard proposal={emailProposal} onApprove={onApprove} />)

    await userEvent.click(screen.getByRole('button', { name: /approve and send/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Task changed')
    // Still actionable rather than stuck in a spinner.
    await userEvent.click(screen.getByRole('button', { name: /approve and send/i }))
    expect(await screen.findByText(/Not yet confirmed sent/i)).toBeInTheDocument()
  })

  it('can be dismissed without approving', async () => {
    const onApprove = vi.fn()
    render(<ActionProposalCard proposal={emailProposal} onApprove={onApprove} />)

    await userEvent.click(screen.getByRole('button', { name: /hide for now/i }))

    expect(screen.queryByTestId('action-proposal')).not.toBeInTheDocument()
    expect(onApprove).not.toHaveBeenCalled()
  })
})

describe('confirmed delivery reporting', () => {
  it('claims the client was contacted only once delivery is confirmed sent', async () => {
    const onAwaitDelivery = vi.fn().mockResolvedValue({ status: 'sent' })
    render(
      <ActionProposalCard
        proposal={emailProposal}
        onApprove={vi.fn().mockResolvedValue({})}
        onAwaitDelivery={onAwaitDelivery}
      />,
    )

    await userEvent.click(screen.getByRole('button', { name: /approve and send/i }))

    expect(await screen.findByText(/Sent to the client/i)).toBeInTheDocument()
    expect(onAwaitDelivery).toHaveBeenCalledWith(
      expect.objectContaining(emailProposal),
      { signal: expect.objectContaining({ aborted: false }) },
    )
  })

  it('reports an unconfirmed send with duplicate-safe retry guidance', async () => {
    render(
      <ActionProposalCard
        proposal={emailProposal}
        onApprove={vi.fn().mockResolvedValue({})}
        onAwaitDelivery={vi.fn().mockResolvedValue({
          status: 'failed',
          error_message: 'Email delivery did not complete (unconfigured)',
        })}
      />,
    )

    await userEvent.click(screen.getByRole('button', { name: /approve and send/i }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/Delivery was not confirmed/i)
    expect(alert).toHaveTextContent(/unconfigured/i)
    expect(alert).toHaveTextContent(/check.*Sent Items.*before retrying/i)
    expect(alert).toHaveTextContent(/duplicate/i)
    // A failure must never read as success.
    expect(alert).not.toHaveTextContent(/Sent to the client/i)
  })

  it('does not claim success when the outcome is still unknown', async () => {
    // Polling gave up without a terminal state — the honest answer is "unknown",
    // not reassurance.
    render(
      <ActionProposalCard
        proposal={emailProposal}
        onApprove={vi.fn().mockResolvedValue({})}
        onAwaitDelivery={vi.fn().mockResolvedValue({ status: 'sending' })}
      />,
    )

    await userEvent.click(screen.getByRole('button', { name: /approve and send/i }))

    const status = await screen.findByRole('status')
    expect(status).toHaveTextContent(/Not yet confirmed sent/i)
    expect(status).not.toHaveTextContent(/Sent to the client/i)
  })

  it('stops delivery polling when the proposal card unmounts', async () => {
    let observedSignal
    const onAwaitDelivery = vi.fn((_proposal, { signal }) => {
      observedSignal = signal
      return new Promise(() => {})
    })
    const view = render(
      <ActionProposalCard
        proposal={emailProposal}
        onApprove={vi.fn().mockResolvedValue({})}
        onAwaitDelivery={onAwaitDelivery}
      />,
    )

    await userEvent.click(screen.getByRole('button', { name: /approve and send/i }))
    await waitFor(() => expect(onAwaitDelivery).toHaveBeenCalledOnce())
    view.unmount()

    expect(observedSignal.aborted).toBe(true)
  })

  it('still confirms a plain task without waiting on delivery', async () => {
    const onAwaitDelivery = vi.fn()
    render(
      <ActionProposalCard
        proposal={taskProposal}
        onApprove={vi.fn().mockResolvedValue({})}
        onAwaitDelivery={onAwaitDelivery}
      />,
    )

    await userEvent.click(screen.getByRole('button', { name: /^approve$/i }))

    expect(await screen.findByText(/moved into active work/i)).toBeInTheDocument()
    expect(onAwaitDelivery).not.toHaveBeenCalled()
  })
})
