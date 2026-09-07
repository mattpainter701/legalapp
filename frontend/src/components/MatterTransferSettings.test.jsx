import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import MatterTransferSettings from './MatterTransferSettings'
import { getMatterPortalUploadLink, setMatterPortalUploadLink } from '../api'
vi.mock('../api', () => ({ getMatterPortalUploadLink: vi.fn(), setMatterPortalUploadLink: vi.fn() }))
afterEach(() => { cleanup(); vi.resetAllMocks() })
it('loads, publishes, removes, and reports a failed save', async () => {
  getMatterPortalUploadLink.mockResolvedValue({ url: 'https://files.example/old' })
  setMatterPortalUploadLink.mockResolvedValue({})
  const user = userEvent.setup()
  render(<MatterTransferSettings matterId="matter-1" />)
  const input = await screen.findByDisplayValue('https://files.example/old')
  await user.clear(input)
  await user.type(input, 'https://files.example/new')
  await user.click(screen.getByRole('button', { name: 'Save portal upload link' }))
  expect(setMatterPortalUploadLink).toHaveBeenCalledWith('matter-1', 'https://files.example/new')
  expect(await screen.findByRole('status')).toHaveTextContent('published')
  await user.clear(input)
  await user.click(screen.getByRole('button'))
  expect(await screen.findByRole('status')).toHaveTextContent('removed')
  expect(setMatterPortalUploadLink).toHaveBeenLastCalledWith('matter-1', null)
  setMatterPortalUploadLink.mockRejectedValue(new Error('offline'))
  await user.click(screen.getByRole('button'))
  expect(await screen.findByRole('status')).toHaveTextContent('Could not save')
})
it('keeps saving disabled when the current link could not be loaded', async () => {
  getMatterPortalUploadLink.mockRejectedValue(new Error('offline'))
  render(<MatterTransferSettings matterId="matter-1" />)
  expect(await screen.findByRole('status')).toHaveTextContent('Could not load')
  expect(screen.getByRole('button')).toBeDisabled()
})
