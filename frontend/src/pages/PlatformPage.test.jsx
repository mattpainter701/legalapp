import React from 'react'
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
  getLLMRoutes,
  deleteLLMProviderKey,
  saveLLMRoutes,
} from '../api'

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal()),
  getLLMGatewayStatus: vi.fn(),
  getLLMModelCatalog: vi.fn(),
  getLLMProviderKeys: vi.fn(),
  getLLMProviderPresets: vi.fn(),
  getLLMRoutes: vi.fn(),
  deleteLLMProviderKey: vi.fn(),
  saveLLMRoutes: vi.fn(),
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
}

const premium = {
  ...standard,
  model: 'provider/premium',
}

beforeEach(() => {
  vi.clearAllMocks()
  getLLMProviderKeys.mockResolvedValue({
    keys: [{ id: 'key-1', name: 'Production', provider_id: 'openrouter' }],
  })
  getLLMProviderPresets.mockResolvedValue({
    providers: [{ id: 'openrouter', name: 'OpenRouter', description: 'Router' }],
  })
  getLLMRoutes.mockResolvedValue({
    standard,
    premium,
    activation: { status: 'active', revision: 'abc123', aliases: activeAliases },
  })
  getLLMModelCatalog.mockResolvedValue({ models: [] })
  getLLMGatewayStatus.mockResolvedValue({ reachable: true, aliases: {} })
  saveLLMRoutes.mockResolvedValue({
    activated: true,
    litellm_updated: true,
    app_aliases: activeAliases,
    models_registered: 2,
    fallbacks_registered: 0,
  })
  deleteLLMProviderKey.mockResolvedValue({ deleted: true })
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
    expect(screen.getAllByRole('button', { name: 'Test provider' })).toHaveLength(2)

    expect(getLLMProviderKeys).toHaveBeenCalledWith('platform-token')
    expect(getLLMProviderPresets).toHaveBeenCalledWith('platform-token')
    expect(getLLMRoutes).toHaveBeenCalledWith('platform-token')
    expect(getLLMModelCatalog).toHaveBeenCalledWith('platform-token')
    expect(getLLMGatewayStatus).toHaveBeenCalledWith('platform-token')
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
      })
    })
    expect(await screen.findByText(/Saved and reloaded LiteLLM/)).toBeInTheDocument()
  })

  it('shows the paid-capacity policy message from a structured rejection', async () => {
    const user = userEvent.setup()
    saveLLMRoutes.mockRejectedValue({
      response: {
        status: 409,
        data: {
          detail: {
            code: 'free_capacity_not_allowed',
            message: 'Standard and Premium customer routes require paid capacity.',
          },
        },
      },
    })
    renderRouting()

    await user.click(await screen.findByRole('button', { name: 'Validate & Activate' }))

    expect(
      await screen.findByText('Standard and Premium customer routes require paid capacity.'),
    ).toBeInTheDocument()
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
})
