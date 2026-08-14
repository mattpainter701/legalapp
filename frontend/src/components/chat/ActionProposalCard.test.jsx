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

  it('approves without edits when the draft is untouched', async () => {
    const onApprove = vi.fn().mockResolvedValue({})
    render(<ActionProposalCard proposal={emailProposal} onApprove={onApprove} />)

    await userEvent.click(screen.getByRole('button', { name: /approve and send/i }))

    // undefined edits means the caller skips the draft PATCH entirely.
    await waitFor(() => expect(onApprove).toHaveBeenCalledWith(emailProposal, undefined))
    expect(await screen.findByText(/Approved and sent/i)).toBeInTheDocument()
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
    expect(await screen.findByText(/Approved and sent/i)).toBeInTheDocument()
  })

  it('can be dismissed without approving', async () => {
    const onApprove = vi.fn()
    render(<ActionProposalCard proposal={emailProposal} onApprove={onApprove} />)

    await userEvent.click(screen.getByRole('button', { name: /dismiss/i }))

    expect(screen.queryByTestId('action-proposal')).not.toBeInTheDocument()
    expect(onApprove).not.toHaveBeenCalled()
  })
})
