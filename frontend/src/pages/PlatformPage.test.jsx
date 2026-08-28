import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ConfirmProvider } from '../components/dialog/ConfirmProvider'
import { AIRoutingTab } from './PlatformPage'
import {
  getLLMGatewayStatus,
  getLLMModelCatalog,
  getLLMProviderKeys,
  getLLMProviderPresets,
  getLLMRoutingProfiles,
  getLLMRoutes,
  deleteLLMProviderKey,
  recommendLLMRoutes,
  saveLLMRoutes,
  testLLMRoute,
  updateLLMRoutingProfile,
  getBackgroundAssistantUsage,
  updateBackgroundAssistantQuota,
} from '../api'

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal()),
  getLLMGatewayStatus: vi.fn(),
  getLLMModelCatalog: vi.fn(),
  getLLMProviderKeys: vi.fn(),
  getLLMProviderPresets: vi.fn(),
  getLLMRoutingProfiles: vi.fn(),
  getLLMRoutes: vi.fn(),
  deleteLLMProviderKey: vi.fn(),
  recommendLLMRoutes: vi.fn(),
  saveLLMRoutes: vi.fn(),
  testLLMRoute: vi.fn(),
  updateLLMRoutingProfile: vi.fn(),
  getBackgroundAssistantUsage: vi.fn(),
  updateBackgroundAssistantQuota: vi.fn(),
}))

const activeAliases = {
  standard: 'clarity-standard-rabc123',
  premium: 'clarity-premium-rabc123',
}

const standard = {
  key_id: 'key-1',
  provider_id: 'openrouter',
  model: 'provider/standard',
  capacity: 100,
  alternates: [],
  fallbacks: [],
  allow_matter_context: false,
}

const premium = {
  ...standard,
  model: 'provider/premium',
  allow_matter_context: true,
}

const defaultProfile = {
  id: '00000000-0000-0000-0000-000000000001',
  name: 'Default',
  is_default: true,
  is_demo_default: false,
  is_active: true,
  assignable: true,
  standard_allow_matter_context: false,
  premium_allow_matter_context: true,
}

beforeEach(() => {
  vi.clearAllMocks()
  getLLMProviderKeys.mockResolvedValue({
    keys: [{ id: 'key-1', name: 'Production', provider_id: 'openrouter' }],
  })
  getLLMProviderPresets.mockResolvedValue({
    providers: [{ id: 'openrouter', name: 'OpenRouter', description: 'Router' }],
  })
  getLLMRoutingProfiles.mockResolvedValue({ profiles: [defaultProfile] })
  getLLMRoutes.mockResolvedValue({
    standard,
    premium,
    activation: { status: 'active', revision: 'abc123', aliases: activeAliases },
  })
  getLLMModelCatalog.mockResolvedValue({ models: [] })
  getLLMGatewayStatus.mockResolvedValue({ reachable: true, aliases: {} })
  getBackgroundAssistantUsage.mockResolvedValue({
    limits: { account_five_hour: 2050, account_weekly: 5100, account_monthly: 10250, tenant_five_hour: 250, tenant_weekly: 750, tenant_monthly: 1500, reservation_ttl_minutes: 15, account_five_hour_micros: 12000000, account_weekly_micros: 30000000, account_monthly_micros: 60000000, tenant_five_hour_micros: 3000000, tenant_weekly_micros: 8000000, tenant_monthly_micros: 15000000 },
    five_hour: { used: 0, remaining: 2050, percent: 0 },
    weekly: { used: 0, remaining: 5100, percent: 0 },
    monthly: { used: 0, remaining: 10250, percent: 0, month_elapsed_percent: 50, projected_over_budget: false },
    value: {
      five_hour: { spent_usd: 0, limit_usd: 12, remaining_usd: 12, percent: 0 },
      weekly: { spent_usd: 0, limit_usd: 30, remaining_usd: 30, percent: 0 },
      monthly: { spent_usd: 0, limit_usd: 60, remaining_usd: 60, percent: 0, month_elapsed_percent: 50, projected_month_usd: 0, projected_over_budget: false },
      unreconciled: { requests: 0, held_usd: 0 },
    },
  })
  updateBackgroundAssistantQuota.mockResolvedValue({
    limits: { account_five_hour: 2050, account_weekly: 5100, account_monthly: 10250, tenant_five_hour: 250, tenant_weekly: 750, tenant_monthly: 1500, reservation_ttl_minutes: 15, account_five_hour_micros: 12000000, account_weekly_micros: 30000000, account_monthly_micros: 60000000, tenant_five_hour_micros: 3000000, tenant_weekly_micros: 8000000, tenant_monthly_micros: 15000000 },
    five_hour: { used: 0, remaining: 2050, percent: 0 },
    weekly: { used: 0, remaining: 5100, percent: 0 },
    monthly: { used: 0, remaining: 10250, percent: 0, month_elapsed_percent: 50, projected_over_budget: false },
    value: {
      five_hour: { spent_usd: 0, limit_usd: 12, remaining_usd: 12, percent: 0 },
      weekly: { spent_usd: 0, limit_usd: 30, remaining_usd: 30, percent: 0 },
      monthly: { spent_usd: 0, limit_usd: 60, remaining_usd: 60, percent: 0, month_elapsed_percent: 50, projected_month_usd: 0, projected_over_budget: false },
      unreconciled: { requests: 0, held_usd: 0 },
    },
  })
  saveLLMRoutes.mockResolvedValue({
    activated: true,
    litellm_updated: true,
    app_aliases: activeAliases,
    models_registered: 2,
    fallbacks_registered: 0,
  })
  deleteLLMProviderKey.mockResolvedValue({ deleted: true })
  recommendLLMRoutes.mockResolvedValue({
    route: 'standard',
    eligible_count: 3,
    warnings: [],
    candidates: [
      { key_id: 'key-1', key_name: 'Production', provider_id: 'openrouter', provider_name: 'OpenRouter', model: 'provider/fast', score: 700, canary_ok: true, is_free: true },
      { key_id: 'key-1', key_name: 'Production', provider_id: 'openrouter', provider_name: 'OpenRouter', model: 'provider/backup-a', score: 650, canary_ok: true, is_free: true },
      { key_id: 'key-1', key_name: 'Production', provider_id: 'openrouter', provider_name: 'OpenRouter', model: 'provider/backup-b', score: 600, canary_ok: true, is_free: false },
    ],
  })
  testLLMRoute.mockResolvedValue({
    ok: false,
    error: 'Billing or provider policy blocked the canary; credential validity is indeterminate.',
    error_category: 'billing_or_provider_policy',
    credential_state: 'indeterminate_policy_block',
    provider_latency_ms: 123,
  })
  updateLLMRoutingProfile.mockImplementation(async (_key, id, update) => ({
    ...defaultProfile,
    id,
    ...update,
  }))
})

afterEach(cleanup)

function renderRouting() {
  return render(
    <ConfirmProvider>
      <AIRoutingTab platformKey="platform-token" />
    </ConfirmProvider>,
  )
}

describe('platform AI routing', () => {
  it('shows the active versioned aliases and distinguishes provider probes', async () => {
    renderRouting()

    expect(await screen.findAllByText(activeAliases.standard)).not.toHaveLength(0)
    expect(screen.getAllByText(activeAliases.premium)).not.toHaveLength(0)
    expect(screen.getByText('revision abc123')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Test provider' })).toHaveLength(3)

    expect(getLLMProviderKeys).toHaveBeenCalledWith('platform-token')
    expect(getLLMProviderPresets).toHaveBeenCalledWith('platform-token')
    expect(getLLMRoutes).toHaveBeenCalledWith('platform-token', defaultProfile.id)
    expect(getLLMModelCatalog).toHaveBeenCalledWith('platform-token')
    expect(getLLMGatewayStatus).toHaveBeenCalledWith('platform-token')
    expect(getBackgroundAssistantUsage).toHaveBeenCalledWith('platform-token')
    expect(screen.getAllByText('Background Automations (global)')).toHaveLength(2)
    expect(screen.getByText('Background pool budget')).toBeInTheDocument()
  })

  it('reports the background pool budget in spend, not request counts', async () => {
    getBackgroundAssistantUsage.mockResolvedValue({
      limits: { account_five_hour: 2050, account_weekly: 5100, account_monthly: 10250, tenant_five_hour: 250, tenant_weekly: 750, tenant_monthly: 1500, reservation_ttl_minutes: 15, account_monthly_micros: 60000000 },
      five_hour: { used: 40, remaining: 2010, percent: 2 },
      weekly: { used: 120, remaining: 4980, percent: 2.4 },
      monthly: { used: 300, remaining: 9950, percent: 2.9, month_elapsed_percent: 25 },
      value: {
        five_hour: { spent_usd: 9.5, limit_usd: 12, remaining_usd: 2.5, percent: 79.17 },
        weekly: { spent_usd: 21, limit_usd: 30, remaining_usd: 9, percent: 70 },
        // Only 300 of ~10,250 requests used, but a quarter into the month the
        // spend is already tracking well past the $60 ceiling.
        monthly: { spent_usd: 44, limit_usd: 60, remaining_usd: 16, percent: 73.33, month_elapsed_percent: 25, projected_month_usd: 176, projected_over_budget: true },
        unreconciled: { requests: 0, held_usd: 0 },
      },
    })
    renderRouting()

    expect(await screen.findByText('$9.50')).toBeInTheDocument()
    expect(screen.getByText('$44.00')).toBeInTheDocument()
    expect(screen.getByText(/tracking to \$176\.00/)).toBeInTheDocument()
    expect(screen.getByText('Projected over budget')).toBeInTheDocument()
    // Requests stay visible, but only as the secondary figure.
    expect(screen.getByText(/\$2\.50 left · 40 requests/)).toBeInTheDocument()
  })

  it('warns when unconfirmed requests are still holding budget', async () => {
    getBackgroundAssistantUsage.mockResolvedValue({
      limits: { account_monthly: 10250, reservation_ttl_minutes: 15 },
      five_hour: { used: 0 }, weekly: { used: 0 }, monthly: { used: 0 },
      value: {
        five_hour: { spent_usd: 0, limit_usd: 12, remaining_usd: 12, percent: 0 },
        weekly: { spent_usd: 0, limit_usd: 30, remaining_usd: 30, percent: 0 },
        monthly: { spent_usd: 3.2, limit_usd: 60, remaining_usd: 56.8, percent: 5.3, month_elapsed_percent: 40, projected_month_usd: 8, projected_over_budget: false },
        unreconciled: { requests: 7, held_usd: 3.2 },
      },
    })
    renderRouting()

    expect(await screen.findByText(/7 unconfirmed requests hold \$3\.20/)).toBeInTheDocument()
  })

  it('keeps aged-out unknown spend visible to operators', async () => {
    getBackgroundAssistantUsage.mockResolvedValue({
      limits: { account_monthly: 10250, reservation_ttl_minutes: 15 },
      five_hour: { used: 0 }, weekly: { used: 0 }, monthly: { used: 0 },
      value: {
        five_hour: { spent_usd: 0.6, limit_usd: 12, remaining_usd: 11.4, percent: 5 },
        weekly: { spent_usd: 0.6, limit_usd: 30, remaining_usd: 29.4, percent: 2 },
        monthly: { spent_usd: 0.6, limit_usd: 60, remaining_usd: 59.4, percent: 1, month_elapsed_percent: 40, projected_month_usd: 1.5, projected_over_budget: false },
        unreconciled: { requests: 1, held_usd: 0.6, aged_out_requests: 1, aged_out_held_usd: 0.6 },
      },
    })
    renderRouting()

    expect(await screen.findByText(/1 unconfirmed request holds \$0\.60/)).toBeInTheDocument()
    expect(screen.getByText(/could not be resolved before the retry deadline/)).toBeInTheDocument()
  })

  it('accepts sub-dollar background spend limits shown by the input', async () => {
    const user = userEvent.setup()
    renderRouting()

    const fiveHour = await screen.findByLabelText('Pool / 5 hours', {
      selector: 'input[step="0.01"]',
    })
    await user.clear(fiveHour)
    await user.type(fiveHour, '0.50')
    await user.click(screen.getByRole('button', { name: 'Save budget' }))

    await waitFor(() => {
      expect(updateBackgroundAssistantQuota).toHaveBeenCalledWith(
        'platform-token',
        expect.objectContaining({ account_five_hour_usd: 0.5 }),
      )
    })
  })

  it('allows Responses-only Luna on the Background route', async () => {
    const user = userEvent.setup()
    getLLMProviderKeys.mockResolvedValue({
      keys: [{ id: 'key-go', name: 'Go', provider_id: 'opencode-go', key_hint: '1234' }],
    })
    getLLMProviderPresets.mockResolvedValue({
      providers: [{ id: 'opencode-go', name: 'OpenCode Go', description: 'Go subscription' }],
    })
    getLLMModelCatalog.mockResolvedValue({
      models: [{
        id: 'gpt-5.6-luna',
        name: 'GPT 5.6 Luna',
        provider_id: 'opencode-go',
        key_id: 'key-go',
        key_ids: ['key-go'],
        api_mode: 'responses',
        route_compatible: false,
        confidential_data_allowed: true,
      }],
    })
    renderRouting()

    await waitFor(() => expect(document.getElementById('models-background-primary-provider')).not.toBeNull())
    const provider = document.getElementById('models-background-primary-provider')
    await user.selectOptions(provider, 'opencode-go')
    await user.selectOptions(document.getElementById('models-background-primary-key'), 'key-go')
    const model = document.getElementById('models-background-primary-model')
    await waitFor(() => expect(model.querySelector('option[value="gpt-5.6-luna"]')).not.toBeNull())
    const luna = model.querySelector('option[value="gpt-5.6-luna"]')

    expect(luna).not.toBeDisabled()
    expect(luna).not.toHaveTextContent('Unsupported endpoint')
  })

  it('validates and activates both complete routes as one operation', async () => {
    const user = userEvent.setup()
    renderRouting()

    const activate = await screen.findByRole('button', {
      name: 'Validate & Activate',
    })
    await user.click(activate)

    await waitFor(() => {
      expect(saveLLMRoutes).toHaveBeenCalledWith('platform-token', {
        standard,
        premium,
      }, defaultProfile.id)
    })
    expect(await screen.findByText(/Saved and reloaded LiteLLM/)).toBeInTheDocument()
  })

  it('assigns an approved matter-aware profile to new demo workspaces', async () => {
    getLLMRoutingProfiles.mockResolvedValueOnce({
      profiles: [{
        ...defaultProfile,
        standard_allow_matter_context: true,
      }],
    })
    getLLMRoutes.mockResolvedValueOnce({
      standard: { ...standard, allow_matter_context: true },
      premium,
      activation: { status: 'active', revision: 'abc123', aliases: activeAliases },
    })
    const user = userEvent.setup()
    renderRouting()

    await user.click(await screen.findByRole('button', { name: 'Use for demos' }))

    await waitFor(() => {
      expect(updateLLMRoutingProfile).toHaveBeenCalledWith(
        'platform-token',
        defaultProfile.id,
        { is_demo_default: true },
      )
    })
    expect(await screen.findByText(/will be assigned to new demo workspaces/i)).toBeInTheDocument()
  })

  it('shows the confidential-data policy message from a structured rejection', async () => {
    const user = userEvent.setup()
    saveLLMRoutes.mockRejectedValue({
      response: {
        status: 409,
        data: {
          detail: {
            code: 'confidential_data_not_allowed',
            message: 'This model is approved only for synthetic or sanitized demo data.',
          },
        },
      },
    })
    renderRouting()

    await user.click(await screen.findByRole('button', { name: 'Validate & Activate' }))

    expect(
      await screen.findByText('This model is approved only for synthetic or sanitized demo data.'),
    ).toBeInTheDocument()
  })

  it('shows every catalog provider and uses provider-key-specific model selects', async () => {
    getLLMProviderKeys.mockResolvedValue({
      keys: [
        { id: 'key-1', name: 'Production', provider_id: 'openrouter' },
        { id: 'key-go', name: 'Go', provider_id: 'opencode-go' },
        { id: 'key-zen', name: 'Zen', provider_id: 'opencode-zen' },
      ],
    })
    getLLMProviderPresets.mockResolvedValue({
      providers: [
        { id: 'openrouter', name: 'OpenRouter', description: 'Router' },
        { id: 'opencode-go', name: 'OpenCode Go', description: 'Go' },
        { id: 'opencode-zen', name: 'OpenCode Zen', description: 'Zen' },
      ],
    })
    getLLMModelCatalog.mockResolvedValue({
      model_count: 4,
      models: [
        { id: 'provider/standard', name: 'Standard', provider_id: 'openrouter', provider_name: 'OpenRouter', key_id: 'key-1', key_ids: ['key-1'], legal_eligible: true, legal_tier: 'usable' },
        { id: 'provider/premium', name: 'Premium', provider_id: 'openrouter', provider_name: 'OpenRouter', key_id: 'key-1', key_ids: ['key-1'], legal_eligible: true, legal_tier: 'recommended' },
        { id: 'deepseek-v4-pro', name: 'DeepSeek V4 Pro', provider_id: 'opencode-go', provider_name: 'OpenCode Go', key_id: 'key-go', key_ids: ['key-go'], legal_eligible: true, legal_tier: 'recommended' },
        { id: 'laguna-s-2.1-free', name: 'Laguna', provider_id: 'opencode-zen', provider_name: 'OpenCode Zen', key_id: 'key-zen', key_ids: ['key-zen'], is_free: true, legal_eligible: true, legal_tier: 'usable', confidential_data_allowed: false },
      ],
    })

    renderRouting()

    const providerFilter = await screen.findByRole('combobox', { name: 'Catalog provider' })
    expect(providerFilter).toHaveTextContent('OpenRouter (2)')
    expect(providerFilter).toHaveTextContent('OpenCode Go (1)')
    expect(providerFilter).toHaveTextContent('OpenCode Zen (1)')
    expect(screen.getAllByRole('combobox', { name: 'Model' })).toHaveLength(3)
    expect(screen.getByText('Demo-only data policy')).toBeInTheDocument()
  })

  it('surfaces a key deletion conflict from an active route', async () => {
    const user = userEvent.setup()
    deleteLLMProviderKey.mockRejectedValue({
      response: { data: { detail: 'Provider key is used by the active standard route.' } },
    })
    renderRouting()

    await user.click(await screen.findByTitle('Delete this key from the vault'))
    await user.click(screen.getByRole('button', { name: 'Delete key' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Provider key is used by the active standard route.',
    )
  })

  it('shows redacted provider credential evidence from a canary', async () => {
    const user = userEvent.setup()
    renderRouting()

    const tests = await screen.findAllByRole('button', { name: 'Test provider' })
    await user.click(tests[0])

    expect(
      await screen.findByText(
        'Test failed: Billing or provider policy blocked the canary; credential validity is indeterminate.',
      ),
    ).toBeInTheDocument()
    expect(screen.getByText('Credential indeterminate policy block')).toBeInTheDocument()
    expect(screen.getByText('billing or provider policy')).toBeInTheDocument()
  })

  it('builds and applies a top-three route without activating it automatically', async () => {
    const user = userEvent.setup()
    renderRouting()

    await user.click(await screen.findByRole('button', { name: 'Recommend top 3' }))

    await waitFor(() => {
      expect(recommendLLMRoutes).toHaveBeenCalledWith('platform-token', expect.objectContaining({
        route: 'standard',
        cost_preference: 'cost_optimized',
        data_mode: 'customer',
        count: 3,
        provider_diversity: true,
      }))
    })
    expect(await screen.findByText('provider/fast')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Apply recommendation' }))
    expect(await screen.findByText(/Applied the top 3 standard targets/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Validate & Activate' }))
    await waitFor(() => {
      expect(saveLLMRoutes).toHaveBeenCalledWith('platform-token', expect.objectContaining({
        standard: expect.objectContaining({
          model: 'provider/fast',
          fallbacks: [
            expect.objectContaining({ model: 'provider/backup-a' }),
            expect.objectContaining({ model: 'provider/backup-b' }),
          ],
        }),
      }), defaultProfile.id)
    })
  })

  it('renders a structured model-test error as text', async () => {
    const user = userEvent.setup()
    testLLMRoute.mockRejectedValue({
      response: { data: { detail: { message: 'Selected provider key is not authorized.' } } },
    })
    renderRouting()

    const tests = await screen.findAllByRole('button', { name: 'Test provider' })
    await user.click(tests[0])

    expect(await screen.findByText('Test failed: Selected provider key is not authorized.')).toBeInTheDocument()
  })
})
