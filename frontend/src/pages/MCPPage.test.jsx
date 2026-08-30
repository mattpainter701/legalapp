import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  createMcpProductKey: vi.fn(),
  getAdminMcpOverview: vi.fn(),
  getMcpProductKeys: vi.fn(),
  revokeMcpProductKey: vi.fn(),
  updateAdminSettings: vi.fn(),
  updateMcpProductKey: vi.fn(),
  confirmAction: vi.fn(),
}))

vi.mock('../api', () => ({
  createMcpProductKey: mocks.createMcpProductKey,
  getAdminMcpOverview: mocks.getAdminMcpOverview,
  getMcpProductKeys: mocks.getMcpProductKeys,
  revokeMcpProductKey: mocks.revokeMcpProductKey,
  updateAdminSettings: mocks.updateAdminSettings,
  updateMcpProductKey: mocks.updateMcpProductKey,
}))

vi.mock('../App', () => ({
  useAuth: () => ({ user: { id: 'admin-1', role: 'admin' } }),
}))

vi.mock('../components/dialog/ConfirmProvider', () => ({
  useConfirm: () => mocks.confirmAction,
}))

import MCPPage from './MCPPage'

const workspaceTools = [
  { name: 'find_matter', description: 'Find matters', effect: 'read' },
  { name: 'propose_task', description: 'Create a reviewable task proposal', effect: 'propose' },
]

function workspaceOverview(overrides = {}) {
  return {
    workspace: {
      deployment_enabled: true,
      tenant_enabled: true,
      default_user_enabled: false,
      official_url: 'https://mcp.getlawhand.com/api/mcp/workspace',
      shorthand: 'https://mcp.getlawhand.com',
      tools: workspaceTools,
      users: { active: 3, licensed: 2, enabled: 1, privacy_mode_blocked: 1 },
      active_grants: 1,
      ...overrides,
    },
  }
}

function researchData(overrides = {}) {
  return {
    product_enabled: false,
    tools: ['search_caselaw', 'get_case_details'],
    keys: [],
    usage: { total_calls: 0, total_results: 0 },
    ...overrides,
  }
}

function renderPage() {
  return render(
    <MemoryRouter>
      <MCPPage embedded />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.confirmAction.mockResolvedValue(true)
  mocks.getAdminMcpOverview.mockResolvedValue(workspaceOverview())
  mocks.getMcpProductKeys.mockResolvedValue(researchData())
  mocks.updateAdminSettings.mockResolvedValue({})
  mocks.createMcpProductKey.mockResolvedValue({ api_key: 'lhrk_test_secret' })
  mocks.revokeMcpProductKey.mockResolvedValue({})
  mocks.updateMcpProductKey.mockResolvedValue({ updated: true })
})

afterEach(cleanup)

describe('Admin MCP servers page', () => {
  it('puts Platform MCP first and separates read tools from proposal tools', async () => {
    renderPage()

    expect(await screen.findByRole('heading', { name: 'MCP Servers', level: 2 })).toBeInTheDocument()
    const platform = await screen.findByRole('heading', { name: 'LawHand Platform MCP', level: 3 })
    const research = screen.getByRole('heading', { name: 'LawHand Research MCP', level: 3 })
    expect(platform.compareDocumentPosition(research) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getByText('find_matter')).toBeInTheDocument()
    expect(screen.getByText('propose_task')).toBeInTheDocument()
    expect(screen.getByText('Reads')).toBeInTheDocument()
    expect(screen.getByText('Proposals · human review')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /create key/i })).not.toBeInTheDocument()
  })

  it('builds Codex and Claude setup from the configured official URL', async () => {
    mocks.getAdminMcpOverview.mockResolvedValueOnce(workspaceOverview({
      official_url: 'https://mcp.staging.example/api/mcp/workspace',
      shorthand: 'https://mcp.staging.example',
    }))

    renderPage()

    expect(await screen.findByText('https://mcp.staging.example/api/mcp/workspace')).toBeInTheDocument()
    expect(screen.getByText(/codex mcp add lawhandWorkspace --url https:\/\/mcp\.staging\.example/)).toBeInTheDocument()
    expect(screen.getByText(/claude mcp add --transport http --scope user lawhand https:\/\/mcp\.staging\.example/)).toBeInTheDocument()
  })

  it('shows an overview error instead of rendering false enabled defaults', async () => {
    mocks.getAdminMcpOverview.mockRejectedValueOnce({ response: { data: { detail: 'Tenant policy unavailable' } } })

    renderPage()

    expect(await screen.findByText('Tenant policy unavailable')).toBeInTheDocument()
    expect(screen.queryByText('Enable Platform MCP for this tenant')).not.toBeInTheDocument()
    expect(screen.queryByText('Enabled users')).not.toBeInTheDocument()
  })

  it('exposes an accessible loading state until both MCP data sources settle', async () => {
    let resolveOverview
    let resolveResearch
    mocks.getAdminMcpOverview.mockReturnValueOnce(new Promise((resolve) => { resolveOverview = resolve }))
    mocks.getMcpProductKeys.mockReturnValueOnce(new Promise((resolve) => { resolveResearch = resolve }))

    renderPage()

    expect(screen.getByRole('status', { name: /loading mcp servers/i })).toBeInTheDocument()
    resolveOverview(workspaceOverview())
    resolveResearch(researchData())
    expect(await screen.findByRole('heading', { name: 'LawHand Platform MCP', level: 3 })).toBeInTheDocument()
  })

  it('confirms tenant disable, saves the setting, and refetches authoritative state', async () => {
    const user = userEvent.setup()
    mocks.getAdminMcpOverview
      .mockResolvedValueOnce(workspaceOverview())
      .mockResolvedValueOnce(workspaceOverview({ tenant_enabled: false, users: { licensed: 2, enabled: 0, privacy_mode_blocked: 1 } }))

    renderPage()
    const toggle = await screen.findByRole('switch', { name: 'Enable Platform MCP for this tenant' })
    await user.click(toggle)

    expect(mocks.confirmAction).toHaveBeenCalledWith(expect.objectContaining({
      title: expect.stringMatching(/disable platform mcp/i),
      destructive: true,
    }))
    await waitFor(() => expect(mocks.updateAdminSettings).toHaveBeenCalledWith({ workspace_mcp_enabled: false }))
    await waitFor(() => expect(mocks.getAdminMcpOverview).toHaveBeenCalledTimes(2))
    expect((await screen.findAllByText('Disabled by tenant')).length).toBeGreaterThan(0)
  })

  it('updates the new-user default without changing the tenant master setting', async () => {
    const user = userEvent.setup()
    renderPage()
    const toggle = await screen.findByRole('switch', { name: 'Enable Platform MCP for new users' })
    await user.click(toggle)

    await waitFor(() => expect(mocks.updateAdminSettings).toHaveBeenCalledWith({ default_workspace_mcp_enabled: true }))
  })

  it('keeps Research key creation hidden while the external release gate is closed', async () => {
    renderPage()

    expect(await screen.findByText('External Research MCP access is not enabled')).toBeInTheDocument()
    expect(screen.getByText('https://research.getlawhand.com/api/mcp')).toBeInTheDocument()
    expect(screen.queryByText('Create product key')).not.toBeInTheDocument()
    expect(mocks.createMcpProductKey).not.toHaveBeenCalled()
  })

  it('shows Research key creation when the external release gate is open', async () => {
    mocks.getMcpProductKeys.mockResolvedValue(researchData({ product_enabled: true }))
    const user = userEvent.setup()

    renderPage()

    expect(await screen.findByText('Create Research product key')).toBeInTheDocument()
    const name = screen.getByDisplayValue('LawHand Research')
    await user.clear(name)
    await user.type(name, 'Demo research')
    await user.click(screen.getByRole('button', { name: 'Create key' }))

    await waitFor(() => expect(mocks.createMcpProductKey).toHaveBeenCalledWith(expect.objectContaining({
      name: 'Demo research',
      allowed_tools: null,
    })))
    expect(await screen.findByText('lhrk_test_secret')).toBeInTheDocument()
  })

  it('does not silently fall back to undocumented tools when Research advertises an empty catalog', async () => {
    mocks.getMcpProductKeys.mockResolvedValueOnce(researchData({ product_enabled: true, tools: [] }))

    renderPage()

    expect(await screen.findByText('Create Research product key')).toBeInTheDocument()
    const form = screen.getByRole('button', { name: 'Create key' }).closest('form')
    expect(within(form).queryByText('search_caselaw')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create key' })).toBeDisabled()
  })

  it('describes citator reads as source-bound review evidence, not a good-law claim', async () => {
    mocks.getMcpProductKeys.mockResolvedValueOnce(researchData({
      product_enabled: true,
      tools: ['get_authority_treatment', 'get_citator_status'],
    }))

    renderPage()

    expect((await screen.findAllByText('get_citator_status')).length).toBeGreaterThan(0)
    expect(screen.getByText(/provisional or attorney-reviewed treatment/i)).toBeInTheDocument()
    expect(screen.getByText(/never a good-law determination/i)).toBeInTheDocument()
  })

  it('shows per-key portal charges and lets an admin update lifecycle controls', async () => {
    mocks.getMcpProductKeys.mockResolvedValue(researchData({
      product_enabled: true,
      billing: { unit_price_usd: 0.45 },
      staff: [{ id: 'staff-1', name: 'Jamie Researcher', email: 'jamie@example.com', is_active: true }],
      keys: [{
        id: 'key-1',
        name: 'Litigation team',
        purpose: 'Trial research',
        api_key_masked: 'lhrk_abc...1234',
        assigned_to_user_id: 'staff-1',
        assigned_to: { id: 'staff-1', name: 'Jamie Researcher', email: 'jamie@example.com' },
        allowed_tools: ['search_caselaw', 'get_case_details'],
        monthly_call_limit: 100,
        monthly_budget_usd: 45,
        budget_remaining_usd: 40.5,
        burst_limit_per_minute: 20,
        expires_at: '2026-12-31T23:59:59Z',
        status: 'active',
        is_active: true,
        usage: { successful_calls: 10, failed_calls: 2, charge_usd: 4.5 },
      }],
    }))
    const user = userEvent.setup()

    renderPage()

    expect(await screen.findByText('$4.50 · 2 failed')).toBeInTheDocument()
    expect(screen.getAllByText('Jamie Researcher')).not.toHaveLength(0)
    await user.click(screen.getByRole('button', { name: 'Manage Litigation team' }))
    const controls = screen.getByRole('form', { name: 'Manage Research product key' })
    const budget = within(controls).getByLabelText('Monthly budget (USD)')
    await user.clear(budget)
    await user.type(budget, '90')
    await user.click(within(controls).getByRole('button', { name: 'Save controls' }))

    await waitFor(() => expect(mocks.updateMcpProductKey).toHaveBeenCalledWith('key-1', expect.objectContaining({
      monthly_budget_cents: 9000,
      assigned_to_user_id: 'staff-1',
    })))
  })
})
