import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import PluginsPage, { PluginCard, stateFor } from './PluginsPage'
import { getPlugins } from '../api'

vi.mock('../App', () => ({ useAuth: () => ({ user: { role: 'staff' } }) }))
vi.mock('react-router-dom', () => ({ useNavigate: () => vi.fn() }))
vi.mock('../api', () => ({ getPlugins: vi.fn(), updatePluginEntitlement: vi.fn() }))
afterEach(cleanup)
beforeEach(() => vi.clearAllMocks())

describe('add-on catalog states', () => {
  it.each(['expired', 'scheduled', 'available'])('does not treat a configured %s add-on as active', (entitlement_status) => {
    const navigate = vi.fn()
    const plugin = { plugin_name: 'mediation-legal', display_name: 'Mediation', primary_route: '/plugins/mediation/cases', entitlement_status, setup_status: 'complete', profile_is_complete: true }
    expect(stateFor(plugin)).toBe('available')
    render(<PluginCard plugin={plugin} onNavigate={navigate} />)
    screen.getByRole('button', { name: 'View Add-on' }).click()
    expect(navigate).toHaveBeenCalledWith('/plugins/mediation-legal')
    expect(screen.queryByText('Active')).not.toBeInTheDocument()
  })

  it.each([
    [{ entitlement_status: 'included', setup_status: 'complete' }, 'purchased'],
    [{ entitlement_status: 'purchased', setup_status: 'not_started' }, 'setup-required'],
    [{ entitlement_status: 'trial' }, 'trials'],
    [{ entitlement_status: 'disabled', profile_is_complete: true }, 'locked'],
    [{ profile_is_complete: true }, 'available'],
  ])('classifies each add-on into one category', (plugin, category) => {
    expect(stateFor(plugin)).toBe(category)
  })

  it('keeps an intentionally selected empty category visible', async () => {
    getPlugins.mockResolvedValue({ plugins: [{ plugin_name: 'mediation-legal', display_name: 'Mediation', entitlement_status: 'purchased', setup_status: 'complete', is_purchased: true }] })
    const user = userEvent.setup()
    render(<PluginsPage />)
    await screen.findByText('Mediation')
    await user.click(screen.getByRole('button', { name: /^Trials/ }))
    await waitFor(() => expect(screen.getByText('No Active Trials')).toBeInTheDocument())
    expect(screen.queryByText('Mediation')).not.toBeInTheDocument()
  })
})
