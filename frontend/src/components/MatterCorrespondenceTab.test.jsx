import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ConfirmProvider } from './dialog/ConfirmProvider'
import MatterCorrespondenceTab, { InboundEmailPanel } from './MatterCorrespondenceTab'
import {
  acceptMatterInboundEmail,
  getMatterInboundAlias,
  getMatterInboundEmail,
  getMatterCorrespondence,
} from '../api'

vi.mock('../api', () => ({
  getMatterCorrespondence: vi.fn(),
  scanMatterCorrespondence: vi.fn(),
  getCorrespondenceRules: vi.fn(),
  updateCorrespondenceRules: vi.fn(),
  matterCorrespondenceDownloadUrl: vi.fn(),
  getMatterInboundAlias: vi.fn(),
  createMatterInboundAlias: vi.fn(),
  rotateMatterInboundAlias: vi.fn(),
  disableMatterInboundAlias: vi.fn(),
  getMatterInboundEmail: vi.fn(),
  acceptMatterInboundEmail: vi.fn(),
  createMatterInboundExpenseDraft: vi.fn(),
  rejectMatterInboundEmail: vi.fn(),
}))

const taggedEmail = {
  id: 'email-1',
  envelope_sender: 'client@example.com',
  subject: '[TASK] Nigel I need to meet with you in two weeks',
  occurred_at: '2026-08-26T15:00:00Z',
  body_preview: 'Please put this on the calendar.',
  participants: { from: 'client@example.com', to: ['firm@example.com'] },
  task_suggestion: {
    tag: 'task',
    title: 'Nigel I need to meet with you',
    task_type: 'follow_up',
    priority: 'medium',
    due_date: '2026-09-09',
    calendar_sync: true,
  },
}

describe('Outbound delivery status', () => {
  afterEach(() => { cleanup(); vi.clearAllMocks() })
  it('distinguishes failed and uncertain attempts from ordinary correspondence', async () => {
    getMatterInboundAlias.mockResolvedValue({ enabled: false, alias: null })
    getMatterInboundEmail.mockResolvedValue({ items: [], total: 0 })
    getMatterCorrespondence.mockResolvedValue({ total: 2, items: [
      { id: 'failed', direction: 'outbound', status: 'failed', subject: 'Failed test',
        occurred_at: '2026-09-07T17:00:00Z', participants: { to: ['self@example.com'] } },
      { id: 'unknown', direction: 'outbound', status: 'delivery_unknown', subject: 'Uncertain test',
        occurred_at: '2026-09-07T17:01:00Z', participants: { to: ['self@example.com'] } },
    ] })
    render(<ConfirmProvider><MatterCorrespondenceTab matterId="matter-1" /></ConfirmProvider>)
    expect(await screen.findByText('Delivery failed — review before retrying')).toBeInTheDocument()
    expect(await screen.findByText('Delivery unconfirmed — check Sent Items before retrying')).toBeInTheDocument()
    expect(screen.getAllByText(/self@example.com/)).toHaveLength(2)
  })
})

describe('InboundEmailPanel subject-tag review', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('previews and confirms the task/calendar effect before filing', async () => {
    getMatterInboundAlias.mockResolvedValue({
      enabled: true,
      alias: { address: 'm-example@intake.getlawhand.com' },
    })
    getMatterInboundEmail.mockResolvedValue({ items: [taggedEmail], total: 1 })
    acceptMatterInboundEmail.mockResolvedValue({
      id: taggedEmail.id,
      status: 'accepted',
      communication_log_id: 'communication-1',
      task_id: 'task-1',
      task_due_date: '2026-09-09',
    })
    const onFiled = vi.fn()

    render(
      <ConfirmProvider>
        <InboundEmailPanel matterId="matter-1" onFiled={onFiled} />
      </ConfirmProvider>,
    )

    expect(await screen.findByText('Task tag detected')).toBeInTheDocument()
    expect(screen.getByText('Nigel I need to meet with you')).toBeInTheDocument()
    expect(screen.getByText(/calendar sync will be requested for the reviewer’s connected Outlook or Google calendar/i)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /file \+ create task/i }))

    expect(acceptMatterInboundEmail).toHaveBeenCalledWith('matter-1', 'email-1')
    expect(await screen.findByText(/email filed and task created for sep 9, 2026/i)).toBeInTheDocument()
    expect(onFiled).toHaveBeenCalledOnce()
  })
})
