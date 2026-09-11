import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import TenantPanelSettings from './TenantPanelSettings'
const update = vi.hoisted(() => vi.fn())
vi.mock('../api', () => ({ updatePlatformTenant: update }))
afterEach(() => { cleanup(); vi.clearAllMocks() })
it('saves tenant presentation policy and leaves failure retryable', async () => {
  update.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({})
  const saved = vi.fn()
  render(<TenantPanelSettings tenantId="firm" platformKey="key" hiddenPanels={['team']} onSaved={saved} />)
  expect(screen.getByLabelText('People')).not.toBeChecked()
  fireEvent.click(screen.getByLabelText('Workflow'))
  fireEvent.click(screen.getByText('Save panel settings'))
  await screen.findByText('Could not save panel settings. Try again.')
  expect(saved).not.toHaveBeenCalled()
  fireEvent.click(screen.getByText('Save panel settings'))
  await waitFor(() => expect(saved).toHaveBeenCalledWith(['team', 'workflow']))
  expect(update).toHaveBeenLastCalledWith('key', 'firm', { hidden_matter_panels: ['team', 'workflow'] })
  fireEvent.click(screen.getByText('Reset to default'))
  expect(screen.getByLabelText('People')).toBeChecked()
})
