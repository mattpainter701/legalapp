import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'
import axios from 'axios'
import * as api from '../api'
import PlatformInfrastructurePage from './PlatformInfrastructurePage'

vi.mock('axios')
vi.mock('../api', () => ({ createPlatformSession: vi.fn() }))

test('authenticates and renders fenced DR status', async () => {
  api.createPlatformSession.mockResolvedValue({ access_token: 'session-token' })
  axios.get.mockResolvedValue({ data: {
    status: 'healthy', checked_at: '2026-08-28T00:00:00Z', alerts: [],
    services: [{ id: 'dr', label: 'Skynet DR', role: 'disaster-recovery', status: 'healthy', checked_at: '2026-08-28T00:00:00Z', writer_enabled: false, detail: 'Health probe passed' }],
  } })
  render(<PlatformInfrastructurePage />)
  await userEvent.type(screen.getByLabelText(/bootstrap secret/i), 'operator-secret')
  await userEvent.click(screen.getByRole('button', { name: /open status/i }))
  expect(await screen.findByText('Skynet DR')).toBeInTheDocument()
  expect(screen.getByText('Writer: fenced')).toBeInTheDocument()
  await waitFor(() => expect(axios.get).toHaveBeenCalledWith('/api/platform/infrastructure', expect.objectContaining({ headers: { Authorization: 'Bearer session-token' } })))
})
