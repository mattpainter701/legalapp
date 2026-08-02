import React, { useState } from 'react'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ChatHeader from './ChatHeader'
import ChatInput from './ChatInput'
import ChatMessage, { linkSourceReferences } from './ChatMessage'
import Messages from './Messages'
import ChatRail from './chat/ChatRail'

vi.mock('./AppShell', () => ({
  useAppShell: () => ({
    conversations: [
      {
        id: 'conversation-1',
        title: 'Lease research',
        updated_at: '2026-07-23T12:00:00Z',
      },
    ],
    documents: [
      {
        id: 'document-1',
        filename: 'lease.pdf',
        status: 'indexed',
      },
    ],
    activeConvId: 'conversation-1',
    onConversationDeleted: vi.fn(),
    onDocumentUploaded: vi.fn(),
    onDocumentDeleted: vi.fn(),
  }),
}))

vi.mock('./FileUpload', () => ({
  default: () => <button type="button">Upload source</button>,
}))

afterEach(() => {
  cleanup()
  document.body.style.overflow = ''
  window.localStorage.clear()
})

describe('Chat assistant experience', () => {
  it('turns source tags into numbered source hyperlinks', () => {
    const sources = [
      {
        source_id: 'courtlistener:gries-1',
        case_name: 'Gries Sports Enterprises, Inc. v. Modell',
        citation: '15 Ohio St.3d 284 (1984)',
        url: 'https://www.courtlistener.com/opinion/675482/',
        source_type: 'public_authority',
        locator: 'Retrieved passage 3',
      },
    ]
    const content = 'Ohio applies its choice-of-law framework. [source: courtlistener:gries-1] [verify]'

    expect(linkSourceReferences(content, sources, 'answer-1')).toContain(
      '[[1]](#source-answer-1-1)',
    )

    render(
      <ChatMessage
        message={{
          id: 'answer-1',
          role: 'assistant',
          content,
          sources,
          created_at: '2026-08-02T12:00:00Z',
        }}
      />,
    )

    expect(screen.getByRole('link', { name: '[1]' })).toHaveAttribute(
      'href',
      '#source-answer-1-1',
    )
    expect(screen.getByRole('link', { name: '15 Ohio St.3d 284 (1984)' })).toHaveAttribute(
      'href',
      'https://www.courtlistener.com/opinion/675482/',
    )
    expect(screen.getByText('Authorities Referenced')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Link to Retrieved passage 3' })).toHaveAttribute(
      'href',
      '#source-answer-1-1',
    )
  })

  it('renders truthful timed retrieval activity with live source previews', () => {
    render(
      <ChatMessage
        message={{
          id: 'working-1',
          role: 'assistant',
          content: '',
          sources: [],
          progress: {
            status: 'Checking cases, statutes, and rules',
            counts: { matter: 1, uploads: 0, firm: 2, courtlistener: 1, total: 4 },
            activities: [
              { id: 'working_context', state: 'completed', label: 'Working context ready', elapsed_ms: 240 },
              {
                id: 'public_authority',
                state: 'progress',
                label: 'Checking cases, statutes, and rules',
                detail: 'CourtListener and public authority search',
                elapsed_ms: 1250,
                sources: [{ source_id: 'courtlistener:1', case_name: 'Smith v. Jones', citation: '123 Ohio St.3d 1' }],
              },
            ],
          },
        }}
      />,
    )

    expect(screen.getAllByText('Checking cases, statutes, and rules').length).toBeGreaterThan(0)
    expect(screen.getByText('1.3s')).toBeInTheDocument()
    expect(screen.getByText('Smith v. Jones')).toBeInTheDocument()
    expect(screen.getByLabelText('Sources found')).toBeInTheDocument()
  })

  it('exposes response model and source settings at every viewport', async () => {
    const user = userEvent.setup()
    const setUsePremium = vi.fn()
    const setIncludePublic = vi.fn()

    render(
      <ChatHeader
        activeRef="01"
        activeConvTitle="Lease research"
        usePremium={false}
        setUsePremium={setUsePremium}
        includePublic
        setIncludePublic={setIncludePublic}
        onRenameConversation={vi.fn()}
        onExportConversation={vi.fn()}
        onOpenSidebar={vi.fn()}
      />,
    )

    const settings = screen.getByRole('button', { name: 'Response settings' })
    await user.click(settings)

    expect(screen.getByRole('dialog', { name: 'Response settings' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Premium/ }))
    expect(setUsePremium).toHaveBeenCalledWith(true)

    await user.click(screen.getByRole('switch', { name: /Public case law/ }))
    expect(setIncludePublic).toHaveBeenCalledWith(expect.any(Function))
    const updater = setIncludePublic.mock.calls[0][0]
    expect(updater(true)).toBe(false)
  })

  it('turns suggested work into an editable prompt and keeps keyboard sending', async () => {
    const user = userEvent.setup()
    const onSend = vi.fn()

    function Harness() {
      const [value, setValue] = useState('')
      return (
        <ChatInput
          inputValue={value}
          onInputChange={setValue}
          onSend={onSend}
          onUploadClick={vi.fn()}
          onDropFiles={vi.fn()}
          isSending={false}
          disabled={false}
        />
      )
    }

    render(<Harness />)
    await user.click(screen.getByRole('button', { name: /Build a chronology/ }))

    const composer = screen.getByRole('textbox', { name: 'Message the assistant' })
    expect(composer).toHaveValue('Build a chronology from the available sources')
    expect(composer).toHaveFocus()

    await user.keyboard('{Enter}')
    expect(onSend).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Attach a document' })).toBeInTheDocument()
  })

  it('offers practical starter actions without relying on decorative emoji', async () => {
    const user = userEvent.setup()
    const onPromptSelect = vi.fn()

    render(
      <Messages
        messages={[]}
        isLoading={false}
        isSending={false}
        onPromptSelect={onPromptSelect}
      />,
    )

    await user.click(screen.getByRole('button', { name: /Review a source/ }))
    expect(onPromptSelect).toHaveBeenCalledWith(expect.stringContaining('Summarize the key issues'))
    expect(screen.getByText('What do you want to move forward?')).toBeInTheDocument()
  })
})

describe('Chat workspace drawer', () => {
  it('uses modal semantics, separates sources from conversations, and restores focus', async () => {
    const user = userEvent.setup()

    function Harness() {
      const [open, setOpen] = useState(false)
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>Open assistant workspace</button>
          <ChatRail
            isOpen={open}
            onClose={() => setOpen(false)}
            onNewConversation={vi.fn()}
            onSelectConversation={vi.fn()}
            onDeleteConversation={vi.fn()}
          />
        </>
      )
    }

    render(<Harness />)
    const trigger = screen.getByRole('button', { name: 'Open assistant workspace' })
    await user.click(trigger)

    expect(screen.getByRole('dialog', { name: 'Conversations and sources' })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Close conversations and sources' })).toHaveFocus())
    expect(document.body.style.overflow).toBe('hidden')

    await user.click(screen.getByRole('tab', { name: /Sources/ }))
    expect(screen.getByText('lease.pdf')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Upload source' })).toBeInTheDocument()

    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Conversations and sources' })).not.toBeInTheDocument())
    await waitFor(() => expect(trigger).toHaveFocus())
    expect(document.body.style.overflow).toBe('')
  })
})
