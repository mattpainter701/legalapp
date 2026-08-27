import { describe, expect, it } from 'vitest'
import { normalizeWorkspaceMcpScopes, workspaceMcpOrganizationName } from './workspaceMcp'

describe('Workspace MCP display normalization', () => {
  it('normalizes strings, response objects, and scope maps', () => {
    expect(
      normalizeWorkspaceMcpScopes(
        'matters:read intakes:read documents:read templates:read tasks:propose',
      ),
    ).toEqual([
      { id: 'matters:read', label: 'Find matters and read bounded matter context' },
      { id: 'intakes:read', label: 'Read intake leads and their prospect context' },
      { id: 'documents:read', label: 'Read bounded matter document metadata and text' },
      { id: 'templates:read', label: 'Read active firm templates and bounded template text' },
      { id: 'tasks:propose', label: 'Create tasks that start in human review' },
    ])
    expect(normalizeWorkspaceMcpScopes({
      id: 'documents:propose',
      description: 'Prepare a reviewable DOCX draft',
    })).toEqual([
      { id: 'documents:propose', label: 'Prepare a reviewable DOCX draft' },
    ])
    expect(normalizeWorkspaceMcpScopes({ 'tasks:read': true, 'contacts:read': false })).toEqual([
      { id: 'tasks:read', label: 'Read work-board tasks and review history' },
    ])
  })

  it('renders an explicit workspace identity from supported grant shapes', () => {
    expect(workspaceMcpOrganizationName({ organization: { name: 'Pilot Firm' } })).toBe('Pilot Firm')
    expect(workspaceMcpOrganizationName({ tenant_name: 'Litigation Team' })).toBe('Litigation Team')
    expect(workspaceMcpOrganizationName({})).toBe('Current workspace')
  })
})
