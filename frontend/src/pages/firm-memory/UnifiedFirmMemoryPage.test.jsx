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
    fireEvent.change(screen.getByLabelText('Research query'), { target: { value: 'notice history' } })
    fireEvent.click(screen.getByRole('button', { name: /search firm memory/i }))

    await waitFor(() => expect(searchAuthorizedDocuments).toHaveBeenCalledWith(expect.objectContaining({
      query: 'notice history',
      scope: 'all',
      filters: expect.objectContaining({ matterIds: [] }),
    })))
    expect(screen.getByRole('heading', { name: 'No matching documents' })).toBeInTheDocument()
  })

  it('sends source, share, provider, file type, matter, and date filters', async () => {
    searchAuthorizedDocuments.mockResolvedValue({ results: [], coverage: { state: 'ready', complete: true, sources: [] }, durationMs: 1 })
    render(<UnifiedFirmMemoryPage />)
    await screen.findByRole('option', { name: 'Legacy archive' })
    fireEvent.change(screen.getByLabelText('Research query'), { target: { value: 'prior advice' } })
    fireEvent.change(screen.getByLabelText('Matter filter'), { target: { value: 'matter-1' } })
    fireEvent.change(screen.getByLabelText('Source filter'), { target: { value: 'source-local' } })
    fireEvent.change(screen.getByLabelText('File share filter'), { target: { value: 'share-1' } })
    fireEvent.change(screen.getByLabelText('Modified after'), { target: { value: '2025-01-01' } })
    fireEvent.click(screen.getByRole('button', { name: 'PDF' }))
    fireEvent.click(screen.getByRole('button', { name: /search firm memory/i }))
    await waitFor(() => expect(searchAuthorizedDocuments).toHaveBeenCalledWith(expect.objectContaining({
      filters: expect.objectContaining({
        matterIds: ['matter-1'],
        sourceIds: ['source-local'],
        fileTypes: ['PDF'],
        modifiedFrom: '2025-01-01',
      }),
    })))
  })

  it('does not claim no matches when coverage is incomplete', async () => {
    searchAuthorizedDocuments.mockResolvedValue({
      results: [],
      coverage: { state: 'offline', complete: false, message: 'Archive agent is offline.', checkedSources: 1, totalSources: 2, sources: [] },
      durationMs: 20,
    })
    render(<UnifiedFirmMemoryPage />)
    fireEvent.change(screen.getByLabelText('Research query'), { target: { value: 'indemnity' } })
    fireEvent.click(screen.getByRole('button', { name: /search firm memory/i }))
    expect(await screen.findByRole('heading', { name: 'No matches in available sources' })).toBeInTheDocument()
    expect(screen.getByText('Archive agent is offline.')).toBeInTheDocument()
  })

  it('clears source-dependent filters when the matter changes', async () => {
    listAuthorizedDocumentSources.mockImplementation((matterIds) => Promise.resolve(
      matterIds[0] === 'matter-2'
        ? [{ id: 'source-rivera', label: 'Rivera archive', kind: 'on_prem', share: 'Rivera', shareId: 'share-2', provider: '', providerId: '' }]
        : [
          { id: 'source-local', label: 'Legacy archive', kind: 'on_prem', share: 'Cases', shareId: 'share-1', provider: '', providerId: '' },
          { id: 'source-cloud', label: 'SharePoint', kind: 'cloud', share: '', shareId: '', provider: 'Microsoft 365', providerId: 'm365' },
        ],
    ))
    render(<UnifiedFirmMemoryPage />)
    await screen.findByRole('option', { name: 'Legacy archive' })
    fireEvent.change(screen.getByLabelText('Source filter'), { target: { value: 'source-local' } })
    fireEvent.change(screen.getByLabelText('File share filter'), { target: { value: 'share-1' } })
    fireEvent.change(screen.getByLabelText('Cloud provider filter'), { target: { value: 'm365' } })

    fireEvent.change(screen.getByLabelText('Matter filter'), { target: { value: 'matter-2' } })

    expect(screen.getByLabelText('Source filter')).toHaveValue('')
    expect(screen.getByLabelText('File share filter')).toHaveValue('')
    expect(screen.getByLabelText('Cloud provider filter')).toHaveValue('')
    await waitFor(() => expect(listAuthorizedDocumentSources).toHaveBeenLastCalledWith(['matter-2']))
    expect(await screen.findByRole('option', { name: 'Rivera archive' })).toBeInTheDocument()
  })

  it('cannot submit a selected source that contradicts the displayed scope', async () => {
    searchAuthorizedDocuments.mockResolvedValue({ results: [], coverage: { state: 'partial', complete: false, sources: [] }, durationMs: 1 })
    render(<UnifiedFirmMemoryPage />)
    await screen.findByRole('option', { name: 'Legacy archive' })
    fireEvent.change(screen.getByLabelText('Source filter'), { target: { value: 'source-local' } })
    fireEvent.change(screen.getByLabelText('Search scope'), { target: { value: 'cloud' } })

    expect(screen.getByLabelText('Source filter')).toHaveValue('')
    expect(screen.queryByRole('option', { name: 'Legacy archive' })).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Research query'), { target: { value: 'cloud advice' } })
    fireEvent.click(screen.getByRole('button', { name: /search firm memory/i }))

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
    fireEvent.change(screen.getByLabelText('Research query'), { target: { value: 'indemnity' } })
    fireEvent.click(screen.getByRole('button', { name: /search firm memory/i }))

    expect(await screen.findByText('Search unavailable')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'No matches in available sources' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'No matching documents' })).not.toBeInTheDocument()
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
    fireEvent.change(screen.getByLabelText('Research query'), { target: { value: 'notice' } })
    fireEvent.click(screen.getByRole('button', { name: /search firm memory/i }))

    expect((await screen.findAllByText('Legacy archive')).length).toBeGreaterThan(1)
    expect(screen.getByText('2019/Order.pdf')).toBeInTheDocument()
    if (platform === 'Win32') expect(screen.getByRole('link', { name: 'Open on this computer' })).toHaveAttribute('href', '/v1/document-search/results/local-1/open')
    else {
      expect(screen.getByRole('button', { name: 'Open on this computer' })).toBeDisabled()
      expect(screen.getByText(/This is not a phone document preview/)).toBeVisible()
    }
    vi.restoreAllMocks()
    expect(screen.getByRole('link', { name: 'Open in Microsoft 365' })).toHaveAttribute('href', 'https://contoso.sharepoint.com/document')
    expect(screen.getAllByText('Acme v. Northstar')).toHaveLength(2)
    expect(screen.getAllByText('None')).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'Copy path' }))
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith('\\\\server\\cases\\2019\\Order.pdf'))
  })

  it('opens a server-issued LawHand result link and says why a device open is not offered', async () => {
    searchAuthorizedDocuments.mockResolvedValue({
      coverage: { state: 'partial', complete: false, sources: [] },
      results: [
        {
          id: 'local-1', title: 'Motion.pdf', snippet: 'Prior notice analysis', fileType: 'PDF', modifiedAt: '', pageNumber: null, score: 0.5,
          source: { kind: 'on_prem', label: 'Legacy archive', provider: '', share: 'Cases', relativeLocation: 'Acme/Motion.pdf', path: 'Acme/Motion.pdf', freshness: '', indexKind: 'smb_metadata_fts' },
          linkedMatters: [{ id: 'matter-1', label: 'Acme v. Northstar' }],
          actions: {
            openOnComputerUrl: '',
            openOnComputerReason: 'Open from the lawhand result page',
            providerUrl: '',
            lawHandUrl: '/firm-memory?matter=matter-1&file=file-1',
          },
        },
      ],
    })
    render(<UnifiedFirmMemoryPage />)
    fireEvent.change(screen.getByLabelText('Research query'), { target: { value: 'notice' } })
    fireEvent.click(screen.getByRole('button', { name: /search firm memory/i }))

    expect(await screen.findByRole('link', { name: 'Open LawHand result' }))
      .toHaveAttribute('href', '/firm-memory?matter=matter-1&file=file-1')
    expect(screen.getByRole('button', { name: 'Open on this computer' }))
      .toHaveAttribute('title', 'Open from the lawhand result page')
    // A relative location is not pasteable into Explorer, so it is not called a path.
    expect(screen.getByRole('button', { name: 'Copy location' })).toBeInTheDocument()
    expect(screen.getByText(/not the full document/i)).toBeInTheDocument()
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
    fireEvent.change(screen.getByLabelText('Research query'), { target: { value: 'notice' } })
    fireEvent.click(screen.getByRole('button', { name: /search firm memory/i }))

    expect(await screen.findByText(/not authorized on any matter/i)).toBeInTheDocument()
    expect(screen.getByText(/choosing a matter searches the file shares bound to it/i)).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'No matching documents' })).not.toBeInTheDocument()
  })
})
