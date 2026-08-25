import { useState } from 'react'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ChatHeader from './ChatHeader'
import ChatInput from './ChatInput'
import ChatMessage, { citedSourceCount, citedSources, linkSourceReferences } from './ChatMessage'
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
      '[[1]](https://www.courtlistener.com/opinion/675482/)',
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
      'https://www.courtlistener.com/opinion/675482/',
    )
    expect(screen.getByRole('link', { name: '15 Ohio St.3d 284 (1984)' })).toHaveAttribute(
      'href',
      'https://www.courtlistener.com/opinion/675482/',
    )
    expect(screen.getByText('Cited Authorities and Sources')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Link to Retrieved passage 3' })).toHaveAttribute(
      'href',
      '#source-answer-1-1',
    )
  })

  it('counts only exact source markers as citations, not every retrieved row', () => {
    const sources = [
      { source_id: 'authority:nd-1', case_name: 'ND authority' },
      { source_id: 'tenant:retainer-1', case_name: 'Retainer agreement' },
    ]

    expect(citedSourceCount(
      'The rule is supported. [source: authority:nd-1] [verify]',
      sources,
    )).toBe(1)
    expect(citedSourceCount('No inline authority.', sources)).toBe(0)
    expect(citedSources(
      'The rule is supported. [source: authority:nd-1] [verify]',
      sources,
    )).toEqual([sources[0]])
  })

  it('renders directly supported claims with the source-backed cited tag', () => {
    const content = 'The statute requires notice. [source: authority:nd-2] [cited]'
    const markerStart = content.indexOf('[source:')
    const tagStart = content.indexOf('[cited]')
    const sources = [{
      source_id: 'authority:nd-2',
      case_name: 'North Dakota authority',
      url: 'https://example.test/authority/nd-2',
      source_type: 'public_authority',
      official_status: 'official',
      authority_tier: 'primary law',
      relevance_score: 0.91,
    }]
    const citationAnnotations = [{
      claim_id: 'claim-1',
      start: 0,
      end: content.length,
      text: 'The statute requires notice.',
      support: 'cited',
      source_ids: ['authority:nd-2'],
      source_markers: [{
        source_id: 'authority:nd-2',
        start: markerStart,
        end: markerStart + '[source: authority:nd-2]'.length,
      }],
      support_tag: { start: tagStart, end: tagStart + '[cited]'.length },
    }]

    render(
      <ChatMessage
        message={{
          id: 'answer-cited',
          role: 'assistant',
          content,
          sources,
          citation_annotations: citationAnnotations,
          created_at: '2026-08-15T12:00:00Z',
        }}
      />,
    )

    expect(screen.getByRole('link', { name: 'cited' })).toHaveAttribute(
      'href',
      '#source-answer-cited-1',
    )
    expect(screen.getByRole('link', { name: '[1]' })).toHaveAttribute(
      'href',
      'https://example.test/authority/nd-2',
    )
    expect(screen.getByText(/official.*primary law.*91% match/i)).toBeInTheDocument()
  })

  it('links annotated and remaining raw source markers in the same answer', () => {
    const firstMarker = '[source: authority:nd-1]'
    const secondMarker = '[source: authority:nd-2]'
    const content = `First supported claim. ${firstMarker} [cited]\n\nSecond source. ${secondMarker} [verify]`
    const firstStart = content.indexOf(firstMarker)
    const citedStart = content.indexOf('[cited]')
    const sources = [
      {
        source_id: 'authority:nd-1',
        case_name: 'First authority',
        url: 'https://example.test/authority/nd-1',
        source_type: 'public_authority',
      },
      {
        source_id: 'authority:nd-2',
        case_name: 'Second authority',
        url: 'https://example.test/authority/nd-2',
        source_type: 'public_authority',
      },
    ]
    const annotations = [{
      claim_id: 'claim-1',
      start: 0,
      end: citedStart + '[cited]'.length,
      text: 'First supported claim.',
      support: 'cited',
      source_ids: ['authority:nd-1'],
      source_markers: [{
        source_id: 'authority:nd-1',
        start: firstStart,
        end: firstStart + firstMarker.length,
      }],
      support_tag: { start: citedStart, end: citedStart + '[cited]'.length },
    }]

    const linked = linkSourceReferences(content, sources, 'answer-mixed', annotations)
    expect(linked).toContain('[[1]](https://example.test/authority/nd-1)')
    expect(linked).toContain('[[2]](https://example.test/authority/nd-2)')
    expect(linked).not.toContain('[source:')
    expect(linked.match(/https:\/\/example\.test\/authority\/nd-1/g)).toHaveLength(1)
    expect(citedSources(content, sources, annotations)).toEqual(sources)

    render(
      <ChatMessage
        message={{
          id: 'answer-mixed',
          role: 'assistant',
          content,
          sources,
          citation_annotations: annotations,
        }}
      />,
    )

    expect(screen.getByRole('link', { name: '[1]' })).toHaveAttribute(
      'href',
      'https://example.test/authority/nd-1',
    )
    expect(screen.getByRole('link', { name: '[2]' })).toHaveAttribute(
      'href',
      'https://example.test/authority/nd-2',
    )
    expect(screen.getByText('First authority')).toBeInTheDocument()
    expect(screen.getByText('Second authority')).toBeInTheDocument()
  })

  it('keeps uncited retrieval out of the visible source ledger', () => {
    const sources = [
      {
        source_id: 'tenant:retainer-1',
        case_name: 'Monthly Retainer Agreement.docx',
        source_type: 'tenant_document',
        source_label: 'Firm context',
      },
    ]

    render(
      <ChatMessage
        message={{
          id: 'answer-gap',
          role: 'assistant',
          content: '## Authority coverage gap\n\nNo supported answer.',
          sources,
          created_at: '2026-08-14T22:33:00Z',
        }}
      />,
    )

    expect(screen.getByText(/0 cited.*1 retrieved/)).toBeInTheDocument()
    expect(screen.queryByText('Monthly Retainer Agreement.docx')).not.toBeInTheDocument()
    expect(screen.queryByText('Retrieved only')).not.toBeInTheDocument()
  })

  it('does not present legacy generic context as an unknown legal case', () => {
    render(
      <ChatMessage
        message={{
          id: 'legacy-answer',
          role: 'assistant',
          content: 'Legacy answer. [source: legacy-1] [verify]',
          sources: [{ source_id: 'legacy-1', case_name: 'Unknown Case', source_type: 'general' }],
          created_at: '2026-08-14T22:33:00Z',
        }}
      />,
    )

    expect(screen.queryByText('Unknown Case')).not.toBeInTheDocument()
    expect(screen.getByText('Retrieved context')).toBeInTheDocument()
  })

  it('keeps attached-document hyperlinks on the authenticated LawHand origin', () => {
    const documentId = '3b99740d-63af-4d25-a937-268786947f8d'
    const sources = [
      {
        source_id: `document:${documentId}`,
        case_name: 'Project Atlas Letter of Intent.docx',
        citation: 'Project Atlas Letter of Intent.docx',
        url: `/api/documents/${documentId}/download`,
        source_type: 'tenant_document',
        source_label: 'Attached document',
        locator: 'LOI §§5–9',
      },
    ]

    render(
      <ChatMessage
        message={{
          id: 'answer-attachment',
          role: 'assistant',
          content: `The exclusivity covenant is binding. [source: document:${documentId}] [verify]`,
          sources,
          created_at: '2026-08-13T20:26:00Z',
        }}
      />,
    )

    expect(screen.getByRole('link', { name: '[1]' })).toHaveAttribute(
      'href',
      `/api/documents/${documentId}/download`,
    )
    expect(
      screen.getByRole('link', { name: 'Project Atlas Letter of Intent.docx' }),
    ).toHaveAttribute('href', `/api/documents/${documentId}/download`)
  })

  it('does not trust arbitrary internal API paths from source metadata', () => {
    render(
      <ChatMessage
        message={{
          id: 'answer-untrusted-internal-link',
          role: 'assistant',
          content: 'Review the purported source. [source: untrusted]',
          sources: [{
            source_id: 'untrusted',
            case_name: 'Untrusted source',
            citation: 'Internal API',
            url: '/api/admin/users',
            source_type: 'tenant_document',
          }],
          created_at: '2026-08-13T20:26:00Z',
        }}
      />,
    )

    expect(screen.queryByRole('link', { name: 'Internal API' })).not.toBeInTheDocument()
  })

  it('renders consent trackers as semantic Markdown tables', () => {
    const content = [
      '| Contract / item | Trigger | Required action | Priority |',
      '|---|---|---|---:|',
      '| Orion Enterprise MSA | Merger deemed assignment | Obtain written consent | Critical |',
    ].join('\n')

    render(
      <ChatMessage
        message={{
          id: 'answer-consent-table',
          role: 'assistant',
          content,
          created_at: '2026-08-13T20:26:00Z',
        }}
      />,
    )

    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Contract / item' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'Orion Enterprise MSA' })).toBeInTheDocument()
  })

  it('uses the available chat width and lets readers collapse answer sections', async () => {
    const user = userEvent.setup()
    render(
      <ChatMessage
        message={{
          id: 'answer-sections',
          role: 'assistant',
          content: "## Filing path\n\nFile in the child's home state.\n\n## Support path\n\nConfirm personal jurisdiction.",
        }}
      />,
    )

    expect(screen.getByTestId('assistant-response')).toHaveClass('w-full', 'max-w-none')
    expect(screen.getByText("File in the child's home state.")).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Collapse sections' }))
    expect(screen.queryByText("File in the child's home state.")).not.toBeVisible()
    expect(screen.getByText('Confirm personal jurisdiction.')).not.toBeVisible()

    await user.click(screen.getByRole('button', { name: 'Expand sections' }))
    expect(screen.getByText("File in the child's home state.")).toBeVisible()
  })

  it('copies a formatted assistant response for Word-compatible paste', async () => {
    const user = userEvent.setup()
    const write = vi.fn().mockResolvedValue(undefined)
    const previousClipboard = navigator.clipboard
    const previousClipboardItem = window.ClipboardItem

    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { write },
    })
    Object.defineProperty(window, 'ClipboardItem', {
      configurable: true,
      value: class ClipboardItem {
        constructor(data) {
          this.data = data
        }
      },
    })

    render(
      <ChatMessage
        message={{
          id: 'answer-copy',
          role: 'assistant',
          content: '## Filing path\n\n| State | Requirement |\n| --- | --- |\n| Ohio | Six months |',
        }}
      />,
    )
    await user.click(screen.getByRole('button', { name: 'Copy formatted response' }))

    await waitFor(() => expect(write).toHaveBeenCalledTimes(1))
    const clipboardItem = write.mock.calls[0][0][0]
    expect(clipboardItem.data['text/html']).toBeInstanceOf(Blob)
    expect(clipboardItem.data['text/plain']).toBeInstanceOf(Blob)

    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: previousClipboard,
    })
    Object.defineProperty(window, 'ClipboardItem', {
      configurable: true,
      value: previousClipboardItem,
    })
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
        activeConversationId="conversation-1"
        linkedMatter={{ id: 'matter-1', matter_name: 'Acme lease', case_number: '24-CV-1' }}
        matters={[{ id: 'matter-1', matter_name: 'Acme lease', case_number: '24-CV-1' }]}
        onLinkMatter={vi.fn()}
        onOpenMatter={vi.fn()}
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

  it('keeps drafting available but blocks button and keyboard sends while another response drains', async () => {
    const user = userEvent.setup()
    const onSend = vi.fn()

    function Harness() {
      const [value, setValue] = useState('Draft while waiting')
      return (
        <ChatInput
          inputValue={value}
          onInputChange={setValue}
          onSend={onSend}
          onUploadClick={vi.fn()}
          onDropFiles={vi.fn()}
          isSending={false}
          disabled={false}
          sendDisabled
          sendDisabledLabel="Another conversation response is finishing"
        />
      )
    }

    render(<Harness />)
    const composer = screen.getByRole('textbox', { name: 'Message the assistant' })
    await user.click(composer)
    await user.type(composer, ' safely')
    expect(composer).toHaveValue('Draft while waiting safely')

    const send = screen.getByRole('button', { name: 'Another conversation response is finishing' })
    expect(send).toBeDisabled()
    await user.keyboard('{Enter}')
    expect(onSend).not.toHaveBeenCalled()
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

  it('keeps the review-tag legend visible for populated and loading chats', () => {
    const { rerender } = render(
      <Messages
        messages={[{ id: 'user-1', role: 'user', content: 'Question' }]}
        isLoading={false}
        isSending={false}
      />,
    )

    const legend = screen.getByLabelText('Review tag legend')
    expect(legend).toHaveClass('sticky')
    expect(legend).not.toHaveClass('hidden')
    expect(screen.getByText('Tag legend:')).toBeInTheDocument()
    expect(screen.getByText('(Source-backed)')).toBeInTheDocument()
    expect(screen.getByText('(Confirm before relying)')).toBeInTheDocument()
    expect(screen.getByText('(General reasoning)')).toBeInTheDocument()

    rerender(
      <Messages
        messages={[{ id: 'user-1', role: 'user', content: 'Question' }]}
        isLoading
        isSending={false}
      />,
    )
    expect(screen.getByLabelText('Review tag legend')).toBeInTheDocument()
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
