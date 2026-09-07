import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getMattersV2 } from '../../api'
import {
  listAuthorizedDocumentSources,
  searchAuthorizedDocuments,
} from '../../documentSearchApi'
import UnifiedFirmMemoryPage from './UnifiedFirmMemoryPage'

vi.mock('../../api', () => ({ getMattersV2: vi.fn() }))
vi.mock('../../documentSearchApi', async () => {
  const actual = await vi.importActual('../../documentSearchApi')
  return {
    ...actual,
    listAuthorizedDocumentSources: vi.fn(),
    searchAuthorizedDocuments: vi.fn(),
  }
})

const submit = () => fireEvent.click(screen.getByRole('button', { name: /^search$/i }))
const openRefine = () => fireEvent.click(screen.getByRole('button', { name: /refine/i }))
const typeQuery = (value) => fireEvent.change(screen.getByLabelText('Research query'), { target: { value } })

describe('UnifiedFirmMemoryPage', () => {
  afterEach(cleanup)

  beforeEach(() => {
    vi.clearAllMocks()
    window.history.replaceState({}, '', '/firm-memory')
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockResolvedValue(undefined) } })
    getMattersV2.mockResolvedValue({ items: [
      { id: 'matter-1', name: 'Acme v. Northstar' },
      { id: 'matter-2', name: 'Rivera Estate' },
    ] })
    listAuthorizedDocumentSources.mockResolvedValue([
      { id: 'source-local', label: 'Legacy archive', kind: 'on_prem', share: 'Cases', shareId: 'share-1', provider: '', providerId: '' },
      { id: 'source-cloud', label: 'SharePoint', kind: 'cloud', share: '', shareId: '', provider: 'Microsoft 365', providerId: 'm365' },
    ])
  })

  it('searches firm-wide by default and treats matter as an optional filter', async () => {
    searchAuthorizedDocuments.mockResolvedValue({
      results: [],
      coverage: { state: 'ready', complete: true, checkedSources: 2, totalSources: 2, sources: [] },
      durationMs: 18,
    })
    render(<UnifiedFirmMemoryPage />)
    typeQuery('notice history')
    submit()

    await waitFor(() => expect(searchAuthorizedDocuments).toHaveBeenCalledWith(expect.objectContaining({
      query: 'notice history',
      scope: 'all',
      filters: expect.objectContaining({ matterIds: [] }),
    })))
    expect(screen.getByRole('heading', { name: 'No matching documents' })).toBeInTheDocument()
    // Completeness is asserted, not implied by the lack of a warning.
    expect(screen.getByText('All authorized sources searched')).toBeInTheDocument()
  })

  it('keeps the query box and scope in front and every narrowing filter behind Refine', async () => {
    render(<UnifiedFirmMemoryPage />)
    await screen.findByRole('button', { name: /on-premises/i })
    // A matter is the least relevant filter for an unlinked on-premises
    // archive, so it must not greet the reader before they have searched.
    expect(screen.queryByLabelText('Matter filter')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Source filter')).not.toBeInTheDocument()

    openRefine()
    expect(await screen.findByLabelText('Matter filter')).toBeInTheDocument()
    expect(screen.getByLabelText('Matter filter')).toHaveValue('')
    expect(screen.getByRole('option', { name: 'Any matter, including unlinked documents' })).toBeInTheDocument()
  })

  it('runs an example research query so the box reads as a question, not a filename', async () => {
    searchAuthorizedDocuments.mockResolvedValue({ results: [], coverage: { state: 'ready', complete: true, sources: [] }, durationMs: 2 })
    render(<UnifiedFirmMemoryPage />)
    fireEvent.click(screen.getByRole('button', { name: /indemnification carve-out/i }))
    await waitFor(() => expect(searchAuthorizedDocuments).toHaveBeenCalledWith(expect.objectContaining({
      query: 'indemnification carve-out for vendor negligence',
    })))
  })

  it('sends source, file type, matter, and date filters', async () => {
    searchAuthorizedDocuments.mockResolvedValue({ results: [], coverage: { state: 'ready', complete: true, sources: [] }, durationMs: 1 })
    render(<UnifiedFirmMemoryPage />)
    openRefine()
    await screen.findByRole('option', { name: 'Legacy archive · Cases' })
    typeQuery('prior advice')
    fireEvent.change(screen.getByLabelText('Matter filter'), { target: { value: 'matter-1' } })
    fireEvent.change(screen.getByLabelText('Source filter'), { target: { value: 'source:source-local' } })
    fireEvent.change(screen.getByLabelText('Modified after'), { target: { value: '2025-01-01' } })
    fireEvent.click(screen.getByRole('button', { name: 'PDF' }))
    submit()
    await waitFor(() => expect(searchAuthorizedDocuments).toHaveBeenCalledWith(expect.objectContaining({
      scope: 'selected',
      filters: expect.objectContaining({
        matterIds: ['matter-1'],
        sourceIds: ['source-local'],
        fileTypes: ['PDF'],
        modifiedFrom: '2025-01-01',
      }),
    })))
  })

  it('selects every source behind a cloud provider from one control', async () => {
    listAuthorizedDocumentSources.mockResolvedValue([
      { id: 'source-sp', label: 'SharePoint sites', kind: 'cloud', share: '', shareId: '', provider: 'Microsoft 365', providerId: 'm365' },
      { id: 'source-od', label: 'OneDrive', kind: 'cloud', share: '', shareId: '', provider: 'Microsoft 365', providerId: 'm365' },
    ])
    searchAuthorizedDocuments.mockResolvedValue({ results: [], coverage: { state: 'ready', complete: true, sources: [] }, durationMs: 1 })
    render(<UnifiedFirmMemoryPage />)
    openRefine()
    await screen.findByRole('option', { name: 'Microsoft 365' })
    typeQuery('vendor notice')
    fireEvent.change(screen.getByLabelText('Source filter'), { target: { value: 'provider:m365' } })
    submit()
    await waitFor(() => expect(searchAuthorizedDocuments).toHaveBeenCalledWith(expect.objectContaining({
      scope: 'selected',
      filters: expect.objectContaining({ sourceIds: ['source-sp', 'source-od'] }),
    })))
  })

  it('shows collapsed refinements as removable chips so hidden state never shapes results silently', async () => {
    searchAuthorizedDocuments.mockResolvedValue({ results: [], coverage: { state: 'ready', complete: true, sources: [] }, durationMs: 1 })
    render(<UnifiedFirmMemoryPage />)
    openRefine()
    await screen.findByLabelText('Matter filter')
    fireEvent.change(screen.getByLabelText('Matter filter'), { target: { value: 'matter-1' } })
    fireEvent.click(screen.getByRole('button', { name: 'PDF' }))
    openRefine()

    expect(screen.queryByLabelText('Matter filter')).not.toBeInTheDocument()
    const chip = screen.getByRole('button', { name: /Acme v\. Northstar/ })
    expect(chip).toBeInTheDocument()

    fireEvent.click(chip)
    typeQuery('vendor notice')
    submit()
    await waitFor(() => expect(searchAuthorizedDocuments).toHaveBeenCalledWith(expect.objectContaining({
      filters: expect.objectContaining({ matterIds: [], fileTypes: ['PDF'] }),
    })))
  })

  it('does not claim no matches when coverage is incomplete', async () => {
    searchAuthorizedDocuments.mockResolvedValue({
      results: [],
      coverage: { state: 'offline', complete: false, message: 'Archive agent is offline.', checkedSources: 1, totalSources: 2, sources: [] },
      durationMs: 20,
    })
    render(<UnifiedFirmMemoryPage />)
    typeQuery('indemnity')
    submit()
    expect(await screen.findByRole('heading', { name: 'No matches in available sources' })).toBeInTheDocument()
    expect(screen.getByText('Archive agent is offline.')).toBeInTheDocument()
  })

  it('clears a source refinement the matter change no longer offers', async () => {
    listAuthorizedDocumentSources.mockImplementation((matterIds) => Promise.resolve(
      matterIds[0] === 'matter-2'
        ? [{ id: 'source-rivera', label: 'Rivera archive', kind: 'on_prem', share: 'Rivera', shareId: 'share-2', provider: '', providerId: '' }]
        : [
          { id: 'source-local', label: 'Legacy archive', kind: 'on_prem', share: 'Cases', shareId: 'share-1', provider: '', providerId: '' },
          { id: 'source-cloud', label: 'SharePoint', kind: 'cloud', share: '', shareId: '', provider: 'Microsoft 365', providerId: 'm365' },
        ],
    ))
    render(<UnifiedFirmMemoryPage />)
    openRefine()
    await screen.findByRole('option', { name: 'Legacy archive · Cases' })
    fireEvent.change(screen.getByLabelText('Source filter'), { target: { value: 'source:source-local' } })

    fireEvent.change(screen.getByLabelText('Matter filter'), { target: { value: 'matter-2' } })

    await waitFor(() => expect(listAuthorizedDocumentSources).toHaveBeenLastCalledWith(['matter-2']))
    expect(await screen.findByRole('option', { name: 'Rivera archive · Rivera' })).toBeInTheDocument()
    expect(screen.getByLabelText('Source filter')).toHaveValue('')
  })

  it('cannot submit a selected source that contradicts the displayed scope', async () => {
    searchAuthorizedDocuments.mockResolvedValue({ results: [], coverage: { state: 'partial', complete: false, sources: [] }, durationMs: 1 })
    render(<UnifiedFirmMemoryPage />)
    openRefine()
    await screen.findByRole('option', { name: 'Legacy archive · Cases' })
    fireEvent.change(screen.getByLabelText('Source filter'), { target: { value: 'source:source-local' } })
    fireEvent.click(screen.getByRole('button', { name: /^cloud$/i }))

    await waitFor(() => expect(screen.getByLabelText('Source filter')).toHaveValue(''))
    expect(screen.queryByRole('option', { name: 'Legacy archive · Cases' })).not.toBeInTheDocument()
    typeQuery('cloud advice')
    submit()

    await waitFor(() => expect(searchAuthorizedDocuments).toHaveBeenCalledWith(expect.objectContaining({
      scope: 'cloud',
      filters: expect.objectContaining({ sourceIds: [] }),
    })))
  })

  it('shows unsupported coverage without collapsing it to a generic ready state', async () => {
    searchAuthorizedDocuments.mockResolvedValue({
      results: [],
      coverage: {
        state: 'unsupported',
        complete: false,
        checkedSources: 0,
        totalSources: 1,
        sources: [{ id: 'source-cloud', label: 'SharePoint', state: 'unsupported', reason: 'native_document_authorization_required' }],
      },
      durationMs: 4,
    })
    render(<UnifiedFirmMemoryPage />)
    typeQuery('indemnity')
    submit()

    expect(await screen.findByText('Search unavailable')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'No matches in available sources' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'No matching documents' })).not.toBeInTheDocument()
  })

  it('names the administrator action behind an unsearchable file share', async () => {
    searchAuthorizedDocuments.mockResolvedValue({
      results: [],
      coverage: {
        state: 'unsupported',
        complete: false,
        message: 'A file share can only be searched through the matters it is bound to.',
        checkedSources: 0,
        totalSources: 1,
        sources: [{ id: 'source-local', label: 'Legacy archive', state: 'unsupported', reason: 'matter_binding_required' }],
      },
      durationMs: 3,
    })
    render(<UnifiedFirmMemoryPage />)
    typeQuery('old vendor file')
    submit()

    // The reader cannot rephrase their way past an unbound share, so the panel
    // has to name whose job it is rather than repeat a coverage token.
    expect(await screen.findByText(/ask an administrator to bind this file share/i)).toBeInTheDocument()
  })

  it.each(['Win32', 'iPhone'])('keeps source actions honest on %s', async platform => {
    vi.spyOn(navigator, 'platform', 'get').mockReturnValue(platform)
    searchAuthorizedDocuments.mockResolvedValue({
      coverage: { state: 'partial', complete: false, sources: [] },
      durationMs: 25,
      results: [
        {
          id: 'local-1', title: 'Order.pdf', snippet: 'Prior notice analysis', fileType: 'PDF', modifiedAt: '', pageNumber: 7, score: 0.9,
          source: { kind: 'on_prem', label: 'Legacy archive', provider: '', share: 'Cases', relativeLocation: '2019/Order.pdf', path: '\\\\server\\cases\\2019\\Order.pdf', freshness: '2026-08-30T12:00:00Z' },
          linkedMatters: [{ id: 'matter-1', label: 'Acme v. Northstar' }],
          actions: { openOnComputerUrl: '/v1/document-search/results/local-1/open', providerUrl: '', lawHandUrl: '' },
        },
        {
          id: 'cloud-1', title: 'Memo.docx', snippet: 'Cloud memo', fileType: 'DOCX', modifiedAt: '', pageNumber: null, score: null,
          source: { kind: 'cloud', label: 'SharePoint', provider: 'Microsoft 365', share: '', relativeLocation: '', path: '', freshness: '' },
          linkedMatters: [],
          actions: { openOnComputerUrl: '', providerUrl: 'https://contoso.sharepoint.com/document', lawHandUrl: '' },
        },
      ],
    })
    render(<UnifiedFirmMemoryPage />)
    typeQuery('notice')
    submit()

    expect(await screen.findByText('Legacy archive')).toBeInTheDocument()
    expect(screen.getByText('2019/Order.pdf')).toBeInTheDocument()
    if (platform === 'Win32') expect(screen.getByRole('link', { name: 'Open on this computer' })).toHaveAttribute('href', '/v1/document-search/results/local-1/open')
    else {
      expect(screen.getByRole('button', { name: 'Open on this computer' })).toBeDisabled()
      expect(screen.getByText(/This is not a phone document preview/)).toBeVisible()
    }
    vi.restoreAllMocks()
    expect(screen.getByRole('link', { name: 'Open in Microsoft 365' })).toHaveAttribute('href', 'https://contoso.sharepoint.com/document')
    expect(screen.getByText('Acme v. Northstar')).toBeInTheDocument()
    expect(screen.getByText('No linked matter')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Copy path' }))
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith('\\\\server\\cases\\2019\\Order.pdf'))
  })

  it('renders search-node highlights as emphasis instead of literal markup', async () => {
    searchAuthorizedDocuments.mockResolvedValue({
      coverage: { state: 'partial', complete: false, sources: [] },
      results: [
        {
          id: 'local-1', title: 'Order.pdf', fileType: 'PDF', modifiedAt: '', pageNumber: null, score: 0.5,
          snippet: 'the <mark>indemnification</mark> clause in Smith &amp; Co &lt;draft&gt;',
          source: { kind: 'on_prem', label: 'Legacy archive', provider: '', share: '', relativeLocation: 'a.pdf', path: 'a.pdf', freshness: '' },
          linkedMatters: [],
          actions: { openOnComputerUrl: '', providerUrl: '', lawHandUrl: '' },
        },
      ],
    })
    render(<UnifiedFirmMemoryPage />)
    typeQuery('indemnification')
    submit()

    const highlight = await screen.findByText('indemnification')
    expect(highlight.tagName).toBe('MARK')
    // Entities are unescaped as text; corpus markup is never interpreted.
    expect(screen.getByText(/Smith & Co <draft>/)).toBeInTheDocument()
    expect(screen.queryByText(/<mark>/)).not.toBeInTheDocument()
  })

  it('explains a firm-wide search that reached nothing instead of showing a bare zero', async () => {
    searchAuthorizedDocuments.mockResolvedValue({
      results: [],
      coverage: {
        state: 'unauthorized',
        complete: false,
        message: 'You are not authorized on any matter bound to one or more of these sources, so they were not searched.',
        checkedSources: 0,
        totalSources: 1,
        sources: [{ id: 'source-local', label: 'Legacy archive', state: 'unauthorized', searched: false, reason: 'no_authorized_matter_scope' }],
      },
    })
    render(<UnifiedFirmMemoryPage />)
    typeQuery('notice')
    submit()

    expect(await screen.findByText(/not authorized on any matter bound to one or more/i)).toBeInTheDocument()
    expect(screen.getByText(/ask an administrator for access to the matter/i)).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'No matching documents' })).not.toBeInTheDocument()
  })
})
