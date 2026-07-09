import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import useCallDrafts from './useCallDrafts'

vi.mock('../api', () => ({
  getIntakeDrafts: vi.fn().mockResolvedValue([]),
  upsertIntakeDraft: vi.fn().mockImplementation(async (id, body) => ({ id, payload: body.payload, updated_at: new Date().toISOString() })),
  deleteIntakeDraft: vi.fn().mockResolvedValue(undefined),
  createIntakeDashboardCall: vi.fn(),
  assignNextPartner: vi.fn(),
  normalizeApiError: (error) => error,
}))

describe('intake draft privacy', () => {
  beforeEach(() => window.localStorage.clear())

  it('purges legacy PII and never persists draft contents in browser storage', async () => {
    window.localStorage.setItem('intake.drafts.legacy', JSON.stringify({ caller_name: 'Private Client' }))
    const { result } = renderHook(() => useCallDrafts())
    await waitFor(() => expect(result.current.loading).toBe(false))
    act(() => result.current.updateDraftField(result.current.activeDraftId, { caller_name: 'Jane Client', phone: '555-0100' }))
    expect(Object.keys(window.localStorage).filter((key) => key.startsWith('intake.drafts.'))).toEqual([])
    expect(JSON.stringify(window.localStorage)).not.toContain('Jane Client')
  })
})
