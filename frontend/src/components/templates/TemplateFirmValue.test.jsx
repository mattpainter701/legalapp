import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { getFirmBranding } from '../../api'
import TemplateFirmValue from './TemplateFirmValue'
import { suggestionConfidenceLabel } from './templateFillReview'

vi.mock('../../api', () => ({ getFirmBranding: vi.fn() }))
afterEach(() => { cleanup(); vi.resetAllMocks() })
const view = field => <MemoryRouter><TemplateFirmValue field={field} /></MemoryRouter>

it('shows the saved firm value, settings destination and the selected field', async () => {
  getFirmBranding.mockResolvedValue({ firm_name: 'Example Firm', firm_phone: '555-0100' })
  const { rerender } = render(view({ path: 'firm.name', label: 'Firm name' }))
  expect(await screen.findByText('Example Firm')).toBeInTheDocument()
  expect(screen.getByText('Configured')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Firm settings' })).toHaveAttribute('href', '/admin?tab=settings#firm-branding')
  rerender(view({ path: 'firm.phone', label: 'Firm phone' }))
  expect(screen.getByText('555-0100')).toBeInTheDocument()
  expect(screen.queryByText('Example Firm')).not.toBeInTheDocument()
  expect(getFirmBranding).toHaveBeenCalledTimes(1)
})

it('distinguishes a missing value from a failed request and retries', async () => {
  getFirmBranding.mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ firm_email: '  ' })
  render(view({ path: 'firm.email', label: 'Firm email' }))
  expect(await screen.findByText('Firm details could not be loaded.')).toBeInTheDocument()
  expect(screen.queryByText('Not configured')).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Retry firm details' }))
  expect(await screen.findByText('Not configured')).toBeInTheDocument()
  expect(screen.getByText('Ask a firm administrator to add firm email.')).toBeInTheDocument()
})

it('labels configured profile data separately from confidence estimates', () => {
  expect(suggestionConfidenceLabel({ source: { source_type: 'firm_profile' }, confidence: 100 })).toBe('Saved firm profile value')
  expect(suggestionConfidenceLabel({ source: {}, confidence: 84 })).toBe('84% match confidence')
  expect(suggestionConfidenceLabel({ source: null, confidence: null })).toBe('Confidence unavailable')
})
