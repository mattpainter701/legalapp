import api from './api'

export const DOCUMENT_SEARCH_SCOPES = Object.freeze({
  ALL: 'all',
  ON_PREM: 'on_prem',
  CLOUD: 'cloud',
  SELECTED: 'selected',
})

export const COVERAGE_STATES = Object.freeze([
  'ready',
  'partial',
  'indexing',
  'stale',
  'offline',
  'unsupported',
  'unauthorized',
])

const list = (value) => (Array.isArray(value) ? value.filter(Boolean) : [])
const first = (...values) => values.find((value) => value !== undefined && value !== null && value !== '')

function normalizeCoverage(raw = [], response = {}) {
  const sources = list(raw).map((source) => {
    const requested = String(first(source.state, source.status, 'partial')).toLowerCase()
    return {
      id: String(first(source.id, source.source_id, '')),
      label: first(source.label, source.name, source.source_name, 'Authorized source'),
      state: COVERAGE_STATES.includes(requested) ? requested : 'partial',
      searched: source.searched === true,
      reason: first(source.reason, ''),
    }
  })
  const states = sources.map((source) => source.state)
  const complete = response.complete === true && response.partial !== true
  const state = complete && states.every((value) => value === 'ready')
    ? 'ready'
    : (['unauthorized', 'unsupported', 'offline', 'indexing', 'stale'].find((value) => states.includes(value)) || 'partial')
  return {
    state,
    complete,
    message: first(response.coverage_message, response.message, ''),
    checkedSources: sources.filter((source) => source.searched).length,
    totalSources: sources.length,
    sources,
  }
}

function normalizeMatter(matter) {
  return {
    id: String(first(matter?.id, matter?.matter_id, '')),
    label: first(matter?.label, matter?.name, matter?.matter_name, matter?.number, 'Linked matter'),
  }
}

function normalizeResult(item = {}) {
  const source = item.source || item.provenance || {}
  const actionList = list(item.actions)
  const action = (type) => actionList.find((candidate) => first(candidate.type, candidate.kind) === type) || {}
  const legacyActions = Array.isArray(item.actions) ? {} : (item.actions || {})
  const rawKind = String(first(source.kind, source.source_kind, item.source_kind, item.kind, 'unknown')).toLowerCase()
  const kind = rawKind === 'smb' ? 'on_prem' : rawKind
  return {
    id: String(first(item.id, item.result_id, item.document_id, '')),
    title: first(item.title, item.filename, item.name, 'Untitled document'),
    snippet: first(item.snippet, item.excerpt, ''),
    fileType: String(first(item.file_type, item.file_extension, item.extension, item.ext, '')).replace(/^\./, '').toUpperCase(),
    modifiedAt: first(item.modified_at, item.modified_time, item.updated_at, ''),
    pageNumber: first(item.page_number, source.page_number, item.page, null),
    score: first(item.score, item.rank_score, null),
    source: {
      id: String(first(source.id, source.source_id, item.source_id, '')),
      kind,
      label: first(source.label, source.name, source.source_name, item.source_label, kind === 'on_prem' ? 'On-prem file share' : 'Cloud source'),
      provider: first(source.provider, item.provider, ''),
      share: first(source.share, source.share_name, item.share_name, ''),
      relativeLocation: first(source.relative_location, item.relative_location, item.relative_path, ''),
      path: first(source.path, item.path, item.unc_path, source.relative_location, item.relative_location, ''),
      freshness: first(source.freshness, source.indexed_at, item.index_freshness, item.indexed_at, ''),
    },
    linkedMatters: list(first(item.linked_matters, item.matters, item.matter_ids, [])).map((matter) => (
      typeof matter === 'string' ? { id: matter, label: matter } : normalizeMatter(matter)
    )),
    actions: {
      openOnComputerUrl: first(action('open_on_device').href, action('open_on_device').url, legacyActions.open_on_computer_url, item.open_on_computer_url, ''),
      providerUrl: first(action('provider_open').href, action('provider_open').url, legacyActions.provider_url, legacyActions.open_url, item.provider_url, item.web_url, ''),
      providerLabel: first(action('provider_open').label, source.provider, item.provider, ''),
      lawHandUrl: first(action('lawhand_result').href, action('lawhand_result').url, legacyActions.lawhand_url, item.lawhand_url, ''),
    },
  }
}

export function normalizeDocumentSearchResponse(data = {}) {
  return {
    results: list(first(data.results, data.hits, [])).map(normalizeResult),
    coverage: Array.isArray(data.coverage)
      ? normalizeCoverage(data.coverage, data)
      : normalizeCoverage(data.coverage?.sources || [], {
        ...data,
        complete: data.coverage?.complete ?? data.complete,
        coverage_message: first(data.coverage?.message, list(data.errors)[0]),
      }),
    durationMs: Number(first(data.duration_ms, data.durationMs, 0)) || 0,
    facets: data.facets || {},
  }
}

export function buildDocumentSearchRequest(input = {}) {
  const filters = input.filters || {}
  return {
    schema_version: 1,
    query: String(input.query || '').trim(),
    source_scope: Object.values(DOCUMENT_SEARCH_SCOPES).includes(input.scope) ? input.scope : DOCUMENT_SEARCH_SCOPES.ALL,
    matter_ids: list(filters.matterIds),
    source_ids: list(filters.sourceIds),
    collection_ids: list(filters.collectionIds),
    filters: {
      file_extensions: list(filters.fileTypes).map((value) => `.${String(value).replace(/^\./, '').toLowerCase()}`),
      modified_from: filters.modifiedFrom || null,
      modified_to: filters.modifiedTo || null,
    },
    limit: Math.min(Math.max(Number(input.limit) || 25, 1), 100),
    audit_correlation_id: input.auditCorrelationId || null,
  }
}

// Temporary client seam for FM-01's additive authorization/search contract.
// Authorization, source visibility, and ACL enforcement remain server-owned.
export const searchAuthorizedDocuments = (input) => api
  .post('/v1/firm-memory/search', buildDocumentSearchRequest(input))
  .then((response) => normalizeDocumentSearchResponse(response.data))

export const listAuthorizedDocumentSources = (matterIds = []) => {
  const params = new URLSearchParams()
  list(matterIds).forEach((matterId) => params.append('matter_ids', matterId))
  return api.get('/v1/firm-memory/sources', { params })
    .then((response) => list(response.data?.sources || response.data).map((source) => ({
      id: String(first(source.id, source.source_id, '')),
      label: first(source.label, source.name, source.display_name, 'Authorized source'),
      kind: String(first(source.kind, source.source_kind, 'unknown')).toLowerCase() === 'smb' ? 'on_prem' : String(first(source.kind, source.source_kind, 'unknown')).toLowerCase(),
      provider: first(source.provider, source.provider_label, source.provider_key, ''),
      providerId: String(first(source.provider_id, source.provider_key, source.provider, '')),
      share: first(source.share, source.share_name, ''),
      shareId: String(first(source.share_id, '')),
    })))
}

export const getFirmMemoryCapabilities = () => api
  .get('/v1/firm-memory/capabilities')
  .then((response) => ({
    searchEntitled: response.data?.search_entitled === true,
    generalizedSearchEnabled: response.data?.generalized_search_enabled === true,
    unifiedResearchAvailable: response.data?.unified_research_available === true,
    contractVersions: list(first(response.data?.contract_versions, response.data?.supported_contract_versions, [])),
    supportedScopes: list(first(response.data?.source_scopes, response.data?.supported_scopes, [])),
  }))
