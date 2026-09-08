import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import TemplateAIProfilePanel from './TemplateAIProfilePanel'
import { getTemplateAIProfile, saveTemplateAIProfile, saveLLMRoutes } from '../api'

vi.mock('../api', () => ({ getTemplateAIProfile: vi.fn(), saveTemplateAIProfile: vi.fn(), saveLLMRoutes: vi.fn() }))
const settings = { enabled: false, key_id: null, provider_id: 'openrouter', model: 'anthropic/claude-opus-5', input_usd_per_million: '5', output_usd_per_million: '25' }
const keys = [{ id: 'openrouter-key', name: 'Existing OpenRouter key', provider_id: 'openrouter' }, { id: 'cheap-key', name: 'Cheap background key', provider_id: 'opencode-go' }]
beforeEach(() => {
  vi.resetAllMocks()
  getTemplateAIProfile.mockResolvedValue({ settings, activation: { status: 'not_configured' } })
  saveTemplateAIProfile.mockImplementation(async (_, value) => ({ settings: value, activation: { status: value.enabled ? 'active' : 'disabled' } }))
})
afterEach(cleanup)

it('loads existing vault choices and saves only the template profile with explicit rates', async () => {
  const user = userEvent.setup()
  render(<TemplateAIProfilePanel platformKey="platform-session" providerKeys={keys} />)
  await screen.findByLabelText('Stored OpenRouter key')
  expect(screen.queryByRole('option', { name: 'Cheap background key' })).not.toBeInTheDocument()
  await user.click(screen.getByRole('checkbox'))
  expect(screen.getByRole('button', { name: 'Save template profile' })).toBeDisabled()
  await user.selectOptions(screen.getByLabelText('Stored OpenRouter key'), 'openrouter-key')
  const rate = screen.getByLabelText('Input USD per million tokens')
  await user.clear(rate); await user.type(rate, '6')
  await user.click(screen.getByRole('button', { name: 'Save template profile' }))
  await screen.findByText('Template profile saved and validated.')
  expect(saveTemplateAIProfile).toHaveBeenCalledWith('platform-session', { ...settings, enabled: true, key_id: 'openrouter-key', input_usd_per_million: '6' })
  expect(saveLLMRoutes).not.toHaveBeenCalled()
  await user.click(screen.getByRole('checkbox'))
  await user.click(screen.getByRole('button', { name: 'Save template profile' }))
  await screen.findByText('Template profile saved.')
})

it('shows activation errors without claiming the new configuration is active', async () => {
  saveTemplateAIProfile.mockRejectedValue({ response: { status: 409, data: { detail: 'Template AI activation failed.' } } })
  render(<TemplateAIProfilePanel platformKey="session" providerKeys={keys} />)
  await userEvent.click(await screen.findByRole('button', { name: 'Save template profile' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Template AI activation failed.')
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  expect(screen.getByText('Active configuration: not configured')).toBeInTheDocument()
})

it('reports load authorization failure and does not show editable defaults', async () => {
  const onAuthError = vi.fn()
  getTemplateAIProfile.mockRejectedValue({ response: { status: 403 } })
  render(<TemplateAIProfilePanel platformKey="session" providerKeys={keys} onAuthError={onAuthError} />)
  await screen.findByRole('alert')
  await waitFor(() => expect(onAuthError).toHaveBeenCalledOnce())
  expect(screen.queryByRole('button')).not.toBeInTheDocument()
})
