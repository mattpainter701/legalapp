import { cleanup, render, screen, fireEvent, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ArtifactReviewPolicy from './ArtifactReviewPolicy'
import api from '../../api'

vi.mock('../../api', () => ({ default: { get: vi.fn(), put: vi.fn() } }))
beforeEach(() => { vi.resetAllMocks() })

it('saves attorney-only policy with the previous value for concurrency protection', async () => {
  api.get.mockResolvedValue({ data: { policy: 'staff_then_attorney', can_edit: true } })
  api.put.mockResolvedValue({ data: { policy: 'attorney_only', can_edit: true } })
  render(<ArtifactReviewPolicy />)
  fireEvent.change(await screen.findByRole('combobox'), { target: { value: 'attorney_only' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save review policy' }))
  await waitFor(() => expect(api.put).toHaveBeenCalledWith('/artifact-reviews/policy', { policy: 'attorney_only', expected_policy: 'staff_then_attorney' }))
  expect(await screen.findByRole('status')).toHaveTextContent('Existing reviews keep their assigned reviewers')
})

it('keeps non-admin reviewers read-only', async () => {
  api.get.mockResolvedValue({ data: { policy: 'attorney_only', can_edit: false } })
  render(<ArtifactReviewPolicy />)
  expect(await screen.findByRole('combobox')).toBeDisabled()
  expect(screen.queryByRole('button')).not.toBeInTheDocument()
})

afterEach(cleanup)
