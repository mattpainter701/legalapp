export const WORKSPACE_MCP_SCOPE_LABELS = {
  'matters:read': 'Find matters and read bounded matter context',
  'tasks:read': 'Read work-board tasks and review history',
  'contacts:read': 'Read client and matter contact records',
  'intakes:read': 'Read intake leads and their prospect context',
  'documents:read': 'Read bounded matter document metadata and text',
  'templates:read': 'Read active firm templates and bounded template text',
  'tasks:propose': 'Create tasks that start in human review',
  'communications:propose': 'Draft client email proposals without sending',
  'documents:propose': 'Create cloud-backed DOCX drafts for staged review',
}

const scopeId = (scope) => scope?.id || scope?.name || scope?.scope || scope?.value

const fallbackScopeLabel = (scope) =>
  WORKSPACE_MCP_SCOPE_LABELS[scope] ||
  scope.replace(/[._:-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())

export function normalizeWorkspaceMcpScopes(value) {
  let raw
  if (Array.isArray(value)) {
    raw = value
  } else if (typeof value === 'string') {
    raw = value.split(/\s+/).filter(Boolean)
  } else if (value && typeof value === 'object' && scopeId(value)) {
    raw = [value]
  } else if (value && typeof value === 'object') {
    raw = Object.keys(value).filter((key) => value[key])
  } else {
    raw = []
  }

  return raw
    .map((scope) => {
      if (typeof scope === 'string') {
        return { id: scope, label: fallbackScopeLabel(scope) }
      }
      const id = scopeId(scope)
      return id
        ? {
            id,
            label: scope.description || scope.label || fallbackScopeLabel(id),
          }
        : null
    })
    .filter(Boolean)
}

export function workspaceMcpOrganizationName(value) {
  const organization = value?.tenant || value?.organization
  if (value?.tenant_name || value?.organization_name) {
    return value.tenant_name || value.organization_name
  }
  if (typeof organization === 'string') return organization
  return organization?.name || value?.workspace_name || 'Current workspace'
}
