import { beforeEach, describe, expect, it, vi } from 'vitest'

const client = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(),
  interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
}))

vi.mock('axios', () => ({ default: { create: () => client } }))

const { createResearchSnapshot, createResearchWorkspace } = await import('./api')

describe('research workspace idempotency headers', () => {
  beforeEach(() => { vi.clearAllMocks(); client.post.mockResolvedValue({ data: {} }) })

  it('sends the caller-owned key on workspace and snapshot creation', async () => {
    await createResearchWorkspace('matter-1', { title: 'Issue set' }, 'workspace-attempt-1')
    await createResearchSnapshot('matter-1', 'workspace-1', {}, 'snapshot-attempt-1')

    expect(client.post).toHaveBeenNthCalledWith(1, '/matters/matter-1/research-workspaces', { title: 'Issue set' }, { headers: { 'Idempotency-Key': 'workspace-attempt-1' } })
    expect(client.post).toHaveBeenNthCalledWith(2, '/matters/matter-1/research-workspaces/workspace-1/snapshots', {}, { headers: { 'Idempotency-Key': 'snapshot-attempt-1' } })
  })
})
