import React from 'react'
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

    await userEvent.click(screen.getByRole('button', { name: /dismiss/i }))

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
    expect(onAwaitDelivery).toHaveBeenCalledWith(emailProposal)
  })

  it('reports a failed send as not sent, with the reason and a retry path', async () => {
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
    expect(alert).toHaveTextContent(/Not sent/i)
    expect(alert).toHaveTextContent(/unconfigured/i)
    expect(alert).toHaveTextContent(/approve it again to retry/i)
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
