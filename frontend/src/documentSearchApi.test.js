import { beforeEach, describe, expect, it, vi } from 'vitest'
import api from './api'
import {
  buildDocumentSearchRequest,
  getFirmMemoryCapabilities,
  listAuthorizedDocumentSources,
  normalizeDocumentSearchResponse,
  searchAuthorizedDocuments,
} from './documentSearchApi'

vi.mock('./api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}))

describe('document search client contract', () => {
  beforeEach(() => vi.clearAllMocks())

  it('builds the additive generalized request without requiring a matter', () => {
    expect(buildDocumentSearchRequest({
      query: '  notice history ',
      scope: 'cloud',
      filters: { fileTypes: ['PDF'] },
    })).toEqual({
      query: 'notice history',
      schema_version: 1,
      source_scope: 'cloud',
      matter_ids: [],
      source_ids: [],
      collection_ids: [],
      filters: {
        file_extensions: ['.pdf'],
        modified_from: null,
        modified_to: null,
      },
      limit: 25,
      audit_correlation_id: null,
    })
  })

  it('normalizes provenance, linked matters, actions, and honest coverage', () => {
    const data = normalizeDocumentSearchResponse({
      complete: false,
      partial: true,
      coverage: [{ source_id: 'source-1', source_name: 'Archive', state: 'stale', searched: true }],
      results: [{
        result_id: 'result-1',
        filename: 'Advice.pdf',
        source: { kind: 'on_prem', name: 'Archive', share_name: 'Cases', relative_location: '2018/Advice.pdf' },
        linked_matters: [{ matter_id: 'matter-1', name: 'Acme v. Northstar' }],
        actions: [{ type: 'lawhand_result', href: '/firm-memory?result=result-1' }],
      }],
    })
    expect(data.coverage).toMatchObject({ state: 'stale', complete: false, checkedSources: 1, totalSources: 1 })
    expect(data.results[0]).toMatchObject({
      id: 'result-1',
      source: { kind: 'on_prem', label: 'Archive', share: 'Cases', relativeLocation: '2018/Advice.pdf' },
      linkedMatters: [{ id: 'matter-1', label: 'Acme v. Northstar' }],
      actions: { lawHandUrl: '/firm-memory?result=result-1' },
    })
  })

  it('preserves landed unsupported and unauthorized coverage states', () => {
    const unsupported = normalizeDocumentSearchResponse({
      complete: false,
      partial: true,
      coverage: [{ source_id: 'source-1', source_name: 'Archive', state: 'unsupported', reason: 'native_document_authorization_required' }],
    })
    expect(unsupported.coverage).toMatchObject({
      state: 'unsupported',
      complete: false,
      sources: [expect.objectContaining({ state: 'unsupported', reason: 'native_document_authorization_required' })],
    })

    const unauthorized = normalizeDocumentSearchResponse({
      complete: false,
      partial: true,
      coverage: [{ source_id: 'source-2', state: 'unauthorized', authorization: 'denied' }],
    })
    expect(unauthorized.coverage.state).toBe('unauthorized')
  })

  it('posts only the normalized request to the foundation endpoint', async () => {
    api.post.mockResolvedValue({ data: { coverage: { state: 'ready', complete: true }, results: [] } })
    await searchAuthorizedDocuments({ query: 'indemnity', scope: 'all' })
    expect(api.post).toHaveBeenCalledWith('/v1/firm-memory/search', expect.objectContaining({
      query: 'indemnity',
      source_scope: 'all',
      matter_ids: [],
    }))
  })

  it('uses the server-computed rollout decision without inferring it locally', async () => {
    api.get.mockResolvedValue({ data: {
      search_entitled: true,
      generalized_search_enabled: false,
      unified_research_available: false,
      contract_versions: [1],
      source_scopes: ['all', 'on_prem', 'cloud', 'selected'],
    } })
    await expect(getFirmMemoryCapabilities()).resolves.toMatchObject({
      searchEntitled: true,
      generalizedSearchEnabled: false,
      unifiedResearchAvailable: false,
      contractVersions: [1],
    })
    expect(api.get).toHaveBeenCalledWith('/v1/firm-memory/capabilities')
  })

  it('requests only the authorized source list for the optional matter filter', async () => {
    api.get.mockResolvedValue({ data: { sources: [{
      id: 'source-1',
      display_name: 'SharePoint research',
      source_kind: 'cloud',
      provider_key: 'microsoft365',
      coverage_state: 'ready',
    }] } })
    await expect(listAuthorizedDocumentSources(['matter-1'])).resolves.toEqual([expect.objectContaining({
      id: 'source-1',
      label: 'SharePoint research',
      providerId: 'microsoft365',
    })])
    const [, config] = api.get.mock.calls[0]
    expect(api.get.mock.calls[0][0]).toBe('/v1/firm-memory/sources')
    expect(config.params.getAll('matter_ids')).toEqual(['matter-1'])
  })
})
