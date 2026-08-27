import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

vi.mock('./IntegrationsPanel', () => ({ default: () => <div>Cloud configuration</div> }))
vi.mock('./TeamsPanel', () => ({ default: () => <div>Teams configuration</div> }))
vi.mock('./ZoomPanel', () => ({ default: () => <div>Zoom configuration</div> }))
vi.mock('./QBOPanel', () => ({ default: () => <div>QuickBooks configuration</div> }))
vi.mock('../pages/MCPPage', () => ({ default: () => <div>MCP configuration</div> }))
vi.mock('../pages/CloudSearchAdmin', () => ({ default: () => <div>Search configuration</div> }))
vi.mock('../pages/SmbAdminPage', () => ({ default: () => <div>File share configuration</div> }))

import IntegrationsHub, {
  LEGACY_INTEGRATION_TABS,
  availableIntegrationSections,
} from './IntegrationsHub'

describe('IntegrationsHub', () => {
  it('keeps every full-platform integration in one admin catalog', () => {
    const sections = availableIntegrationSections({ role: 'admin', plan: 'professional' })

    expect(sections.map((section) => section.id)).toEqual([
      'cloud',
      'cloud-search',
      'file-shares',
      'teams',
      'zoom',
      'quickbooks',
      'mcp',
    ])
    expect(LEGACY_INTEGRATION_TABS).toMatchObject({ mcp: 'mcp', smb: 'file-shares', qbo: 'quickbooks' })
  })

  it('shows purpose, expandable permissions, setup requirements, and guide links', async () => {
    const onSectionChange = vi.fn()
    render(
      <MemoryRouter>
        <IntegrationsHub
          user={{ role: 'admin', plan: 'professional' }}
          section="overview"
          onSectionChange={onSectionChange}
        />
      </MemoryRouter>
    )

    expect(screen.getByText('Every external connection, in one place.')).toBeInTheDocument()
    expect(screen.getByText('Connect Microsoft 365 or Google Workspace, choose where matter documents live, and manage approved imports.')).toBeInTheDocument()

    const details = screen.getAllByText('Permissions & setup')[0]
    await userEvent.click(details)
    expect(screen.getByText('Directory profiles for user provisioning')).toBeVisible()
    expect(screen.getByRole('link', { name: /Integration setup guide/ })).toHaveAttribute('href', '/guide/integrations')

    await userEvent.click(screen.getAllByText('Open configuration')[0])
    expect(onSectionChange).toHaveBeenCalledWith('cloud')
  })

  it('limits the catalog for accountant and intake-only roles', () => {
    expect(availableIntegrationSections({ role: 'accountant' }).map((section) => section.id)).toEqual(['quickbooks'])
    expect(availableIntegrationSections({ role: 'admin', plan: 'intake-only' }).map((section) => section.id)).toEqual(['zoom'])
  })
})
