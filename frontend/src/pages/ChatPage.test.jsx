import React from 'react'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  getConversation: vi.fn(),
  streamMessage: vi.fn(),
  createConversation: vi.fn(),
  updateConversation: vi.fn(),
  uploadChatAttachment: vi.fn(),
  getMattersV2: vi.fn(),
  getLegalSourceHealth: vi.fn(),
}))

const shellHarness = vi.hoisted(() => ({ value: null }))
const routerHarness = vi.hoisted(() => ({
  query: 'conv=conversation-a',
  navigate: vi.fn(),
}))

vi.mock('../api', () => apiMocks)
vi.mock('../components/AppShell', () => ({
  useAppShell: () => shellHarness.value,
}))
vi.mock('react-router-dom', () => ({
  useNavigate: () => routerHarness.navigate,
  useSearchParams: () => [new URLSearchParams(routerHarness.query)],
}))
vi.mock('../components/ChatHeader', () => ({
  default: ({ activeConvTitle }) => <div data-testid="chat-title">{activeConvTitle}</div>,
}))
vi.mock('../components/ChatInput', () => ({
  default: ({ inputValue, onInputChange, onSend, isSending, sendDisabled, sendDisabledLabel }) => (
    <div>
      <textarea
        aria-label="Message the assistant"
        value={inputValue}
        onChange={(event) => onInputChange(event.target.value)}
      />
      <button
        type="button"
        onClick={onSend}
        disabled={isSending || sendDisabled || !inputValue.trim()}
        aria-label={sendDisabled ? sendDisabledLabel : 'Send message'}
      >
        Send message
      </button>
    </div>
  ),
}))
vi.mock('../components/Messages', () => ({
  default: ({ messages, isLoading }) => (
    <div data-testid="messages" data-loading={isLoading ? 'true' : 'false'}>
      {(messages || []).map((message) => (
        <article key={message.id}>
          <span>{message.content}</span>
          {(message.sources || []).map((source) => (
            <span key={source.source_id}>{source.case_name}</span>
          ))}
        </article>
      ))}
    </div>
  ),
}))
vi.mock('../components/chat/ChatRail', () => ({
  default: ({ onSelectConversation, onDeleteConversation }) => (
    <div>
      {(shellHarness.value?.conversations || []).map((conversation) => (
        <React.Fragment key={conversation.id}>
          <button type="button" onClick={() => onSelectConversation(conversation.id)}>
            {conversation.title}
          </button>
          <button type="button" onClick={() => onDeleteConversation(conversation.id)}>
            Delete {conversation.title}
          </button>
        </React.Fragment>
      ))}
    </div>
  ),
}))

import ChatPage, { mergeRefreshedTranscript } from './ChatPage'

const conversation = (id, title, messages = []) => ({
  conversation: { id, title, updated_at: '2099-01-01T00:00:00Z' },
  messages,
})

const assistantMessage = (id, content, sources = []) => ({
  id,
  role: 'assistant',
  content,
  sources,
  created_at: '2099-01-01T00:00:02Z',
})

describe('streamed transcript reconciliation', () => {
  it('pairs the newest persisted turn by server order despite client/server clock skew', () => {
    const optimisticUser = {
      id: 'temp-new-turn',
      role: 'user',
      content: 'Repeat question',
      client_turn_id: 'new-turn',
      _known_server_message_ids: ['old-user', 'old-assistant'],
      created_at: '2099-01-01T12:10:00Z',
    }
    const fallbackAssistant = {
      id: 'stream-new-turn',
      role: 'assistant',
      content: 'Newest answer',
      client_turn_id: 'new-turn',
      progress: { complete: true, status: 'Response complete' },
      sources: [{ source_id: 'authority:nd-1', case_name: 'ND authority' }],
      citation_annotations: [{ claim_id: 'claim-1', source_ids: ['authority:nd-1'] }],
      created_at: '2099-01-01T12:10:01Z',
    }
    const merged = mergeRefreshedTranscript([
      { id: 'old-user', role: 'user', content: 'Repeat question', created_at: '2099-01-01T11:00:00Z' },
      { id: 'old-assistant', role: 'assistant', content: 'Old answer', created_at: '2099-01-01T11:00:01Z' },
      // The server clock is ten minutes behind the browser. Order and the set
      // of IDs known before send identify this as the new turn.
      { id: 'new-user', role: 'user', content: 'Repeat question', created_at: '2099-01-01T12:00:00Z' },
      { id: 'new-assistant', role: 'assistant', content: 'Newest answer', created_at: '2099-01-01T12:00:01Z' },
    ], optimisticUser, fallbackAssistant)

    expect(merged.map((message) => message.id)).toEqual([
      'old-user',
      'old-assistant',
      'new-user',
      'new-assistant',
    ])
    expect(merged[3].progress).toEqual(fallbackAssistant.progress)
    expect(merged[3].sources).toEqual(fallbackAssistant.sources)
    expect(merged[3].citation_annotations).toEqual(fallbackAssistant.citation_annotations)
  })

  it('prefers explicit client-message and reply identities over timestamps or text', () => {
    const optimisticUser = {
      id: 'temp-identity-turn',
      role: 'user',
      content: 'Original browser text',
      created_at: '2099-01-01T12:10:00Z',
    }
    const fallbackAssistant = {
      id: 'stream-identity-turn',
      role: 'assistant',
      content: 'Fallback answer',
      progress: { complete: true },
      created_at: '2099-01-01T12:10:01Z',
    }
    const merged = mergeRefreshedTranscript([
      {
        id: 'server-user',
        client_message_id: 'temp-identity-turn',
        role: 'user',
        content: 'Server-normalized text',
        created_at: '2099-01-01T11:00:00Z',
      },
      {
        id: 'server-assistant',
        parent_message_id: 'server-user',
        role: 'assistant',
        content: 'Persisted answer',
        created_at: '2099-01-01T11:00:01Z',
      },
    ], optimisticUser, fallbackAssistant)

    expect(merged.map((message) => message.id)).toEqual(['server-user', 'server-assistant'])
    expect(merged[1].progress).toEqual(fallbackAssistant.progress)
  })

  it('keeps the completed fallback directly after its persisted user when the assistant is not visible yet', () => {
    const optimisticUser = {
      id: 'temp-pending-turn',
      role: 'user',
      content: 'Pending question',
      _known_server_message_ids: [],
      created_at: '2099-01-01T12:10:00Z',
    }
    const fallbackAssistant = {
      id: 'stream-pending-turn',
      role: 'assistant',
      content: 'Completed streamed answer',
      created_at: '2099-01-01T12:10:01Z',
    }
    const merged = mergeRefreshedTranscript([
      { id: 'server-user', role: 'user', content: 'Pending question', created_at: '2099-01-01T11:00:00Z' },
      { id: 'later-user', role: 'user', content: 'Another turn', created_at: '2099-01-01T11:01:00Z' },
    ], optimisticUser, fallbackAssistant)

    expect(merged.map((message) => message.id)).toEqual([
      'server-user',
      'stream-pending-turn',
      'later-user',
    ])
  })
})

describe('ChatPage guarded stream lifecycle', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    routerHarness.query = 'conv=conversation-a'
    routerHarness.navigate.mockImplementation((target) => {
      const query = String(target || '').split('?')[1] || ''
      routerHarness.query = query
    })
    shellHarness.value = {
      conversations: [
        { id: 'conversation-a', title: 'Conversation A' },
        { id: 'conversation-b', title: 'Conversation B' },
      ],
      setConversations: vi.fn((update) => {
        shellHarness.value.conversations = typeof update === 'function'
          ? update(shellHarness.value.conversations)
          : update
      }),
      activeConvId: 'conversation-a',
      setActiveConvId: vi.fn((id) => { shellHarness.value.activeConvId = id }),
      onConversationDeleted: vi.fn(),
      documents: [],
      onDocumentUploaded: vi.fn(),
      onDocumentDeleted: vi.fn(),
    }
    apiMocks.getMattersV2.mockResolvedValue({ items: [] })
    apiMocks.getLegalSourceHealth.mockResolvedValue({
      available: false,
      status: 'unavailable',
      sources: [],
      partitions: [],
    })
    apiMocks.createConversation.mockResolvedValue({ id: 'conversation-new', title: 'New Conversation' })
    apiMocks.updateConversation.mockImplementation(async (_id, data) => data)
  })

  afterEach(() => cleanup())

  it('keeps streamed answer text visible and offers a retry when source metadata refresh fails', async () => {
    const source = {
      source_id: 'document:one',
      case_name: 'Document One.pdf',
      source_type: 'tenant_document',
    }
    apiMocks.getConversation
      .mockResolvedValueOnce(conversation('conversation-a', 'Conversation A'))
      .mockRejectedValueOnce(new Error('metadata service unavailable'))
      .mockResolvedValueOnce(conversation('conversation-a', 'Conversation A', [
        {
          id: 'server-user',
          role: 'user',
          content: 'Analyze the agreement',
          sources: [],
          created_at: '2099-01-01T00:00:01Z',
        },
        assistantMessage('server-answer', 'Streamed answer [source: document:one]', [source]),
      ]))
    apiMocks.streamMessage.mockImplementation(async function* () {
      yield {
        type: 'progress',
        event: 'citation_metadata',
        sources: [source],
        citation_annotations: [],
      }
      yield 'Streamed answer [source: document:one]'
      yield '[STREAM_COMPLETE]'
    })

    render(<ChatPage />)
    await waitFor(() => expect(apiMocks.getConversation).toHaveBeenCalledTimes(1))

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Message the assistant'), 'Analyze the agreement')
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    expect(await screen.findByText('Source metadata could not be verified')).toBeInTheDocument()
    expect(screen.getByText(/Streamed answer/)).toBeInTheDocument()
    expect(screen.getByText('Document One.pdf')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry source metadata' }))

    expect(await screen.findByText('Sources refreshed')).toBeInTheDocument()
    expect(screen.getByText('Document One.pdf')).toBeInTheDocument()
  })

  it('shows whether chat uses only a profile or a profile plus matter context', async () => {
    shellHarness.value.conversations = [{ id: 'conversation-a', title: 'Conversation A', matter_id: 'matter-1' }]
    apiMocks.getMattersV2.mockResolvedValue({ items: [{ id: 'matter-1', matter_name: 'Acme advisory', case_number: 'AC-42' }] })
    apiMocks.getConversation.mockResolvedValue(conversation('conversation-a', 'Conversation A'))

    render(<ChatPage />)

    expect(await screen.findByText('Using your profile + Acme advisory')).toBeInTheDocument()
    expect(screen.getByText('AC-42')).toBeInTheDocument()
  })

  it('shows profile-only context when no matter is linked', async () => {
    apiMocks.getConversation.mockResolvedValue(conversation('conversation-a', 'Conversation A'))

    render(<ChatPage />)

    expect(await screen.findByText('Using your profile')).toBeInTheDocument()
  })

  it('does not let an old metadata retry overwrite a newly selected conversation', async () => {
    let resolveMetadataRetry
    const delayedMetadataRetry = new Promise((resolve) => { resolveMetadataRetry = resolve })
    apiMocks.getConversation
      .mockResolvedValueOnce(conversation('conversation-a', 'Conversation A'))
      .mockRejectedValueOnce(new Error('metadata service unavailable'))
      .mockImplementationOnce(() => delayedMetadataRetry)
      .mockResolvedValueOnce(conversation('conversation-b', 'Conversation B', [
        assistantMessage('answer-b', 'Conversation B remains selected'),
      ]))
    apiMocks.streamMessage.mockImplementation(async function* () {
      yield 'Answer in conversation A'
      yield '[STREAM_COMPLETE]'
    })

    render(<ChatPage />)
    await waitFor(() => expect(apiMocks.getConversation).toHaveBeenCalledTimes(1))
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Message the assistant'), 'Question for A')
    await user.click(screen.getByRole('button', { name: 'Send message' }))
    await user.click(await screen.findByRole('button', { name: 'Retry source metadata' }))
    expect(await screen.findByText('Refreshing source metadata')).toBeInTheDocument()

    await user.click(screen.getAllByRole('button', { name: 'Conversation B' })[0])
    expect(await screen.findByText('Conversation B remains selected')).toBeInTheDocument()

    await act(async () => {
      resolveMetadataRetry(conversation('conversation-a', 'Conversation A', [
        assistantMessage('answer-a', 'Stale refreshed answer from A'),
      ]))
      await Promise.resolve()
    })
    expect(screen.getByText('Conversation B remains selected')).toBeInTheDocument()
    expect(screen.queryByText('Stale refreshed answer from A')).not.toBeInTheDocument()
    expect(screen.queryByText('Sources refreshed')).not.toBeInTheDocument()
  })

  it('turns a failed stream placeholder into one terminal error message', async () => {
    apiMocks.getConversation.mockResolvedValueOnce(
      conversation('conversation-a', 'Conversation A'),
    )
    apiMocks.streamMessage.mockImplementation(async function* () {
      yield 'Partial answer that must not remain authoritative'
      throw new Error('The assistant stream ended before completion. Please retry.')
    })

    render(<ChatPage />)
    await waitFor(() => expect(apiMocks.getConversation).toHaveBeenCalledTimes(1))
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Message the assistant'), 'Question with a broken stream')
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    expect(await screen.findByText('Message could not be sent')).toBeInTheDocument()
    expect(screen.getAllByText(/An error occurred: The assistant stream ended/)).toHaveLength(1)
    expect(screen.queryByText('Partial answer that must not remain authoritative')).not.toBeInTheDocument()
  })

  it('does not let a late stream from conversation A overwrite conversation B', async () => {
    let releaseStream
    let observedSignal
    let streamFinished = false
    const streamGate = new Promise((resolve) => { releaseStream = resolve })
    apiMocks.getConversation.mockImplementation(async (id) => (
      id === 'conversation-b'
        ? conversation('conversation-b', 'Conversation B', [
            assistantMessage('answer-b', 'Conversation B transcript'),
          ])
        : conversation('conversation-a', 'Conversation A')
    ))
    apiMocks.streamMessage.mockImplementation(async function* (...args) {
      observedSignal = args[5]?.signal
      try {
        yield 'Conversation A partial answer'
        await streamGate
        yield ' and late completion'
        yield '[STREAM_COMPLETE]'
      } finally {
        streamFinished = true
      }
    })

    render(<ChatPage />)
    await waitFor(() => expect(apiMocks.getConversation).toHaveBeenCalledWith('conversation-a'))
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Message the assistant'), 'Question for A')
    await user.click(screen.getByRole('button', { name: 'Send message' }))
    expect(await screen.findByText('Conversation A partial answer')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Link matter/i })).toBeDisabled()

    await user.click(screen.getAllByRole('button', { name: 'Conversation B' })[0])
    expect(await screen.findByText('Conversation B transcript')).toBeInTheDocument()
    expect(observedSignal.aborted).toBe(false)
    expect(screen.getByRole('status')).toHaveTextContent(/response is finishing in the background/i)
    await user.type(screen.getByLabelText('Message the assistant'), 'Question for B')
    expect(screen.getByRole('button', {
      name: 'Another conversation response is finishing',
    })).toBeDisabled()
    expect(apiMocks.streamMessage).toHaveBeenCalledTimes(1)

    await act(async () => {
      releaseStream()
      await Promise.resolve()
    })
    expect(screen.getByText('Conversation B transcript')).toBeInTheDocument()
    expect(screen.queryByText(/late completion/)).not.toBeInTheDocument()
    await waitFor(() => expect(streamFinished).toBe(true))
    expect(screen.getByRole('button', { name: 'Send message' })).toBeEnabled()
  })

  it('keeps matter linking disabled in an empty thread while another response finishes', async () => {
    let releaseStream
    const streamGate = new Promise((resolve) => { releaseStream = resolve })
    apiMocks.getConversation.mockImplementation(async (id) => (
      id === 'conversation-b'
        ? conversation('conversation-b', 'Conversation B')
        : conversation('conversation-a', 'Conversation A')
    ))
    apiMocks.streamMessage.mockImplementation(async function* () {
      yield 'Conversation A partial answer'
      await streamGate
      yield ' complete'
      yield '[STREAM_COMPLETE]'
    })

    render(<ChatPage />)
    await waitFor(() => expect(apiMocks.getConversation).toHaveBeenCalledWith('conversation-a'))
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Message the assistant'), 'Question for A')
    await user.click(screen.getByRole('button', { name: 'Send message' }))
    expect(await screen.findByText('Conversation A partial answer')).toBeInTheDocument()

    await user.click(screen.getAllByRole('button', { name: 'Conversation B' })[0])
    await waitFor(() => expect(apiMocks.getConversation).toHaveBeenCalledWith('conversation-b'))
    expect(screen.getByRole('button', { name: /Link matter/i })).toBeDisabled()

    await act(async () => {
      releaseStream()
      await Promise.resolve()
    })
    await waitFor(() => expect(screen.getByRole('button', { name: /Link matter/i })).toBeEnabled())
  })

  it('does not send or mount a new-conversation turn after the user switches threads', async () => {
    routerHarness.query = ''
    shellHarness.value.conversations = []
    shellHarness.value.activeConvId = null
    apiMocks.createConversation.mockResolvedValue({ id: 'conversation-new', title: 'New Conversation' })
    apiMocks.getConversation.mockImplementation(async (id) => (
      id === 'conversation-b'
        ? conversation('conversation-b', 'Conversation B', [
            assistantMessage('answer-b', 'Conversation B remains clean'),
          ])
        : conversation(id, 'New Conversation')
    ))
    const view = render(<ChatPage />)
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Message the assistant'), 'Question intended for the new thread')
    await user.click(screen.getByRole('button', { name: 'Send message' }))
    await waitFor(() => expect(apiMocks.createConversation).toHaveBeenCalledOnce())

    shellHarness.value.conversations = [{ id: 'conversation-b', title: 'Conversation B' }]
    shellHarness.value.activeConvId = 'conversation-b'
    routerHarness.query = 'conv=conversation-b'
    view.rerender(<ChatPage />)

    expect(await screen.findByText('Conversation B remains clean')).toBeInTheDocument()
    await new Promise((resolve) => setTimeout(resolve, 180))
    expect(apiMocks.streamMessage).not.toHaveBeenCalled()
    expect(within(screen.getByTestId('messages')).queryByText('Question intended for the new thread')).not.toBeInTheDocument()
    expect(screen.getByText('Conversation B remains clean')).toBeInTheDocument()
  })

  it('locks matter context after a persisted message exists', async () => {
    apiMocks.getConversation.mockResolvedValueOnce(
      conversation('conversation-a', 'Conversation A', [
        assistantMessage('answer-a', 'Persisted legal analysis'),
      ]),
    )

    render(<ChatPage />)
    expect(await screen.findByText('Persisted legal analysis')).toBeInTheDocument()
    const matterButton = screen.getByRole('button', { name: /Link matter/i })
    expect(matterButton).toBeDisabled()
    expect(matterButton).toHaveAttribute('title', expect.stringMatching(/locked after messages or attachments/i))
  })

  it('locks matter context when a refreshed attachment-only conversation reports persisted files', async () => {
    apiMocks.getConversation.mockResolvedValueOnce({
      conversation: {
        id: 'conversation-a',
        title: 'Conversation A',
        message_count: 0,
        attachment_count: 1,
        updated_at: '2099-01-01T00:00:00Z',
      },
      messages: [],
    })

    render(<ChatPage />)
    await waitFor(() => expect(apiMocks.getConversation).toHaveBeenCalledWith('conversation-a'))
    const matterButton = screen.getByRole('button', { name: /Link matter/i })
    await waitFor(() => expect(matterButton).toBeDisabled())
    expect(matterButton).toHaveAttribute('title', expect.stringMatching(/locked after messages or attachments/i))
  })

  it('refreshes the persisted interruption when a detached stream fails after the user returns', async () => {
    let rejectStream
    let conversationALoads = 0
    const streamGate = new Promise((_resolve, reject) => { rejectStream = reject })
    apiMocks.getConversation.mockImplementation(async (id) => {
      if (id === 'conversation-b') {
        return conversation('conversation-b', 'Conversation B', [
          assistantMessage('answer-b', 'Conversation B transcript'),
        ])
      }
      conversationALoads += 1
      if (conversationALoads >= 3) {
        return conversation('conversation-a', 'Conversation A', [
          {
            id: 'server-user-a',
            role: 'user',
            content: 'Question for A',
            sources: [],
            created_at: '2099-01-01T00:00:01Z',
          },
          assistantMessage(
            'server-failure-a',
            'The assistant could not complete this response. Please retry.',
          ),
        ])
      }
      if (conversationALoads === 2) {
        return conversation('conversation-a', 'Conversation A', [{
          id: 'server-user-a',
          role: 'user',
          content: 'Question for A',
          sources: [],
          created_at: '2099-01-01T00:00:01Z',
        }])
      }
      return conversation('conversation-a', 'Conversation A')
    })
    apiMocks.streamMessage.mockImplementation(async function* () {
      yield 'Partial answer'
      await streamGate
      yield '[STREAM_COMPLETE]'
    })

    render(<ChatPage />)
    await waitFor(() => expect(conversationALoads).toBe(1))
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Message the assistant'), 'Question for A')
    await user.click(screen.getByRole('button', { name: 'Send message' }))
    expect(await screen.findByText('Partial answer')).toBeInTheDocument()

    await user.click(screen.getAllByRole('button', { name: 'Conversation B' })[0])
    expect(await screen.findByText('Conversation B transcript')).toBeInTheDocument()
    await user.click(screen.getAllByRole('button', { name: 'Conversation A' })[0])
    await waitFor(() => expect(conversationALoads).toBe(2))

    await act(async () => {
      rejectStream(new Error('Connection lost after navigation'))
      await Promise.resolve()
    })

    expect(await screen.findByText(/could not complete this response/i)).toBeInTheDocument()
    expect(conversationALoads).toBe(3)
    expect(screen.queryByText('Partial answer')).not.toBeInTheDocument()
  })

  it('aborts a detached generation when its non-active conversation is deleted', async () => {
    let observedSignal
    let streamFinished = false
    apiMocks.getConversation.mockImplementation(async (id) => (
      id === 'conversation-b'
        ? conversation('conversation-b', 'Conversation B', [
            assistantMessage('answer-b', 'Conversation B transcript'),
          ])
        : conversation('conversation-a', 'Conversation A')
    ))
    apiMocks.streamMessage.mockImplementation(async function* (...args) {
      observedSignal = args[5]?.signal
      try {
        yield 'Conversation A partial answer'
        await new Promise((_resolve, reject) => {
          observedSignal.addEventListener('abort', () => {
            const error = new Error('Aborted')
            error.name = 'AbortError'
            reject(error)
          }, { once: true })
        })
      } finally {
        streamFinished = true
      }
    })

    render(<ChatPage />)
    await waitFor(() => expect(apiMocks.getConversation).toHaveBeenCalledWith('conversation-a'))
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Message the assistant'), 'Question for A')
    await user.click(screen.getByRole('button', { name: 'Send message' }))
    expect(await screen.findByText('Conversation A partial answer')).toBeInTheDocument()

    await user.click(screen.getAllByRole('button', { name: 'Conversation B' })[0])
    expect(await screen.findByText('Conversation B transcript')).toBeInTheDocument()
    expect(observedSignal.aborted).toBe(false)

    await user.click(screen.getAllByRole('button', { name: 'Delete Conversation A' })[0])

    await waitFor(() => expect(observedSignal.aborted).toBe(true))
    await waitFor(() => expect(streamFinished).toBe(true))
    expect(screen.getByText('Conversation B transcript')).toBeInTheDocument()
  })

  it('ignores an older conversation load that resolves after the current selection', async () => {
    let resolveConversationA
    const delayedConversationA = new Promise((resolve) => { resolveConversationA = resolve })
    apiMocks.getConversation.mockImplementation((id) => (
      id === 'conversation-a'
        ? delayedConversationA
        : Promise.resolve(conversation('conversation-b', 'Conversation B', [
            assistantMessage('answer-b', 'Newest selected transcript'),
          ]))
    ))

    render(<ChatPage />)
    await waitFor(() => expect(apiMocks.getConversation).toHaveBeenCalledWith('conversation-a'))
    await userEvent.click(screen.getAllByRole('button', { name: 'Conversation B' })[0])
    expect(await screen.findByText('Newest selected transcript')).toBeInTheDocument()

    await act(async () => {
      resolveConversationA(conversation('conversation-a', 'Conversation A', [
        assistantMessage('answer-a', 'Stale transcript from A'),
      ]))
      await Promise.resolve()
    })
    expect(screen.getByText('Newest selected transcript')).toBeInTheDocument()
    expect(screen.queryByText('Stale transcript from A')).not.toBeInTheDocument()
  })
})
