import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ComposeEmailModal from './ComposeEmailModal'
import { emailMatterClient } from '../api'

vi.mock('../api', () => ({
  emailMatterClient: vi.fn(),
}))

const props = {
  matterId: 'matter-1',
  matterName: 'Smith Estate',
  caseNumber: '2026-001',
  clientEmail: 'client@example.com',
  onSent: vi.fn(),
  onClose: vi.fn(),
}

async function submitMessage() {
  const user = userEvent.setup()
  await user.type(screen.getByRole('textbox', { name: 'Message' }), 'Please review the attached update.')
  await user.click(screen.getByRole('button', { name: 'Send' }))
}

describe('ComposeEmailModal delivery honesty', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('does not call onSent when a legacy response says delivery failed', async () => {
    emailMatterClient.mockResolvedValueOnce({ sent: false })
    render(<ComposeEmailModal {...props} />)

    await submitMessage()

    expect(await screen.findByText(/email was not sent/i)).toBeInTheDocument()
    expect(props.onSent).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog', { name: 'Email Client' })).toBeInTheDocument()
  })

  it('shows a typed API delivery error and keeps the composer open', async () => {
    emailMatterClient.mockRejectedValueOnce({
      response: {
        status: 503,
        data: { detail: 'Client email was not completed because outbound email is unavailable.' },
      },
    })
    render(<ComposeEmailModal {...props} />)

    await submitMessage()

    expect(await screen.findByText(/outbound email is unavailable/i)).toBeInTheDocument()
    expect(props.onSent).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog', { name: 'Email Client' })).toBeInTheDocument()
  })

  it('calls onSent only after confirmed delivery', async () => {
    emailMatterClient.mockResolvedValueOnce({ sent: true, id: 'communication-1' })
    render(<ComposeEmailModal {...props} />)

    await submitMessage()

    expect(props.onSent).toHaveBeenCalledWith({ sent: true, id: 'communication-1' })
  })
})
