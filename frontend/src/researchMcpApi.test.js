import { beforeEach, describe, expect, it, vi } from 'vitest'

const client = {
  get: vi.fn(() => Promise.resolve({ data: {} })),
  post: vi.fn(() => Promise.resolve({ data: {} })),
  interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
}

vi.mock('axios', () => ({ default: { create: () => client } }))

const {
  decideResearchMcpAuthorizationRequest,
  getResearchMcpAuthorizationRequest,
} = await import('./api')

beforeEach(() => vi.clearAllMocks())

describe('Research MCP OAuth API helpers', () => {
  it('uses the planned request endpoint and URL-encodes request ids', async () => {
    await getResearchMcpAuthorizationRequest('request/with spaces')
    expect(client.get).toHaveBeenCalledWith('/research-mcp/oauth/requests/request%2Fwith%20spaces')
  })

  it('posts approval decisions to the planned decision endpoint', async () => {
    await decideResearchMcpAuthorizationRequest('request-123', true)
    expect(client.post).toHaveBeenCalledWith('/research-mcp/oauth/requests/request-123/decision', { approved: true })
  })
})
