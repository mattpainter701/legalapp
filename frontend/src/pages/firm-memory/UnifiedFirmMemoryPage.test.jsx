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

  it('keeps source provenance and source-specific actions visible', async () => {
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
    expect(screen.getByRole('link', { name: 'Open on this computer' })).toHaveAttribute('href', '/v1/document-search/results/local-1/open')
    expect(screen.getByRole('link', { name: 'Open in Microsoft 365' })).toHaveAttribute('href', 'https://contoso.sharepoint.com/document')
    expect(screen.getAllByText('Acme v. Northstar')).toHaveLength(2)
    expect(screen.getAllByText('None')).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'Copy path' }))
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith('\\\\server\\cases\\2019\\Order.pdf'))
  })
})
