import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ClientConversation from './ClientConversation'
import {
  getMatterPortalMessages,
  markMatterPortalMessagesRead,
  sendMatterPortalMessage,
} from '../../api'

vi.mock('../../api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
  getMatterPortalMessages: vi.fn(),
  sendMatterPortalMessage: vi.fn(),
  markMatterPortalMessagesRead: vi.fn(),
}))

afterEach(cleanup)

const thread = (overrides = {}) => ({
  messages: [
    { id: 'a', direction: 'inbound', subject: 'Portal message', body: 'Can we move the hearing?', occurred_at: '2026-09-10T14:00:00Z', unread: true },
  ],
  unread_count: 1,
  total: 1,
  has_more: false,
  ...overrides,
})

beforeEach(() => {
  vi.resetAllMocks()
  getMatterPortalMessages.mockResolvedValue(thread())
  sendMatterPortalMessage.mockResolvedValue({
    id: 'b', direction: 'outbound', subject: 'Message from your legal team',
    body: 'Yes — I will call tomorrow.', occurred_at: '2026-09-10T15:00:00Z',
  })
  markMatterPortalMessagesRead.mockResolvedValue({ messages_seen_at: '2026-09-10T16:00:00Z' })
})

it('shows unread client messages and reports the count upward', async () => {
  const onUnreadChange = vi.fn()
  render(<ClientConversation matterId="matter" onUnreadChange={onUnreadChange} />)
  expect(await screen.findByText('1 new')).toBeInTheDocument()
  expect(screen.getByText('Can we move the hearing?')).toBeInTheDocument()
  await waitFor(() => expect(onUnreadChange).toHaveBeenCalledWith(1))
})

it('clears the badge when the firm marks the thread read', async () => {
  const user = userEvent.setup()
  const onUnreadChange = vi.fn()
  render(<ClientConversation matterId="matter" onUnreadChange={onUnreadChange} />)
  await user.click(await screen.findByRole('button', { name: 'Mark as read' }))
  expect(markMatterPortalMessagesRead).toHaveBeenCalledWith('matter')
  await waitFor(() => expect(screen.queryByText('1 new')).not.toBeInTheDocument())
  expect(onUnreadChange).toHaveBeenLastCalledWith(0)
})

it('sends the firm reply into the portal thread', async () => {
  const user = userEvent.setup()
  render(<ClientConversation matterId="matter" />)
  await screen.findByText('Can we move the hearing?')
  await user.type(screen.getByLabelText('Reply to the client'), 'Yes — I will call tomorrow.')
  await user.click(screen.getByRole('button', { name: 'Send message' }))
  expect(sendMatterPortalMessage).toHaveBeenCalledWith('matter', { body: 'Yes — I will call tomorrow.' })
  expect(await screen.findByText('Yes — I will call tomorrow.')).toBeInTheDocument()
  // The box empties so the same reply cannot be sent twice by accident.
  expect(screen.getByLabelText('Reply to the client')).toHaveValue('')
})

it('will not send an empty message', async () => {
  const user = userEvent.setup()
  render(<ClientConversation matterId="matter" />)
  await screen.findByText('Can we move the hearing?')
  expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled()
  await user.type(screen.getByLabelText('Reply to the client'), '   ')
  expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled()
  expect(sendMatterPortalMessage).not.toHaveBeenCalled()
})

it('surfaces a send failure without losing the draft', async () => {
  const user = userEvent.setup()
  sendMatterPortalMessage.mockRejectedValue({ response: { data: { detail: 'Invite the client to the portal first.' } } })
  render(<ClientConversation matterId="matter" />)
  await screen.findByText('Can we move the hearing?')
  await user.type(screen.getByLabelText('Reply to the client'), 'Please call me')
  await user.click(screen.getByRole('button', { name: 'Send message' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Invite the client to the portal first.')
  expect(screen.getByLabelText('Reply to the client')).toHaveValue('Please call me')
})

it('still offers the compose box when the thread cannot load', async () => {
  getMatterPortalMessages.mockRejectedValue(new Error('offline'))
  render(<ClientConversation matterId="matter" />)
  expect(await screen.findByLabelText('Reply to the client')).toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})
