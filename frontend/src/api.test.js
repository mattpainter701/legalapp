import { afterEach, describe, expect, it, vi } from 'vitest'
import { API_BASE_URL, getMatterDocumentDownloadUrl, buildOAuthLoginUrl, isSafeInternalReturnTo, readBlobErrorDetail, streamMessage } from './api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('binary API errors', () => {
  it('extracts the actionable detail from a JSON error blob', async () => {
    const blob = new Blob([JSON.stringify({ detail: 'This PDF has no fillable AcroForm fields.' })], { type: 'application/json' })
    expect(await readBlobErrorDetail(blob)).toBe('This PDF has no fillable AcroForm fields.')
  })
})
describe('OAuth return paths', () => {
  it('accepts only internal paths without control characters or backslashes', () => {
    expect(isSafeInternalReturnTo('/workspace-mcp/authorize?request_id=abc')).toBe(true)
    expect(isSafeInternalReturnTo('//attacker.example/callback')).toBe(false)
    expect(isSafeInternalReturnTo('/\\attacker.example')).toBe(false)
    expect(isSafeInternalReturnTo('/matters\nnext')).toBe(false)
    expect(isSafeInternalReturnTo('https://attacker.example')).toBe(false)
    expect(isSafeInternalReturnTo('/' + 'a'.repeat(2048))).toBe(false)
  })

  it('sends a validated continuation only to the LawHand OAuth login endpoint', () => {
    expect(buildOAuthLoginUrl('google', '/workspace-mcp/authorize?request_id=abc')).toBe(
      '/api/auth/google/login?return_to=%2Fworkspace-mcp%2Fauthorize%3Frequest_id%3Dabc',
    )
    expect(buildOAuthLoginUrl('google', '//attacker.example/callback')).toBe(
      '/api/auth/google/login',
    )
  })
})


describe('chat event stream', () => {
  const collectStream = async (...args) => {
    const events = []
    for await (const event of streamMessage(...args)) events.push(event)
    return events
  }

  it('decodes structured activity and preserves answer Markdown newlines', async () => {
    const body = [
      'data: [PROGRESS]{"type":"progress","event":"activity","activity":{"id":"firm_search","state":"started","label":"Searching firm knowledge"}}\n\n',
      'data: [TOKEN]"## Analysis\\n\\n- First point"\n\n',
      'data: [STREAM_COMPLETE]\n\n',
    ].join('')
    vi.stubGlobal('fetch', vi.fn(async () => new Response(body, { status: 200 })))

    const events = []
    for await (const event of streamMessage('conversation-1', 'Analyze this')) {
      events.push(event)
    }

    expect(events[0].activity.id).toBe('firm_search')
    expect(events[1]).toBe('## Analysis\n\n- First point')
    expect(events[2]).toBe('[STREAM_COMPLETE]')
  })

  it('normalizes the backend ERROR-colon format as a terminal error event', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      'data: [ERROR: Assistant service temporarily unavailable. Retry this message.]\n\n',
      { status: 200 },
    )))

    await expect(collectStream('conversation-1', 'Analyze this')).resolves.toEqual([
      '[ERROR]Assistant service temporarily unavailable. Retry this message.',
    ])
  })

  it('decodes a completion marker that arrives as the final line without a newline', async () => {
    const body = [
      `data: [TOKEN]${JSON.stringify('Complete answer')}\n\n`,
      'data: [STREAM_COMPLETE]',
    ].join('')
    vi.stubGlobal('fetch', vi.fn(async () => new Response(body, { status: 200 })))

    await expect(collectStream('conversation-1', 'Analyze this')).resolves.toEqual([
      'Complete answer',
      '[STREAM_COMPLETE]',
    ])
  })

  it('stops and cancels the response body as soon as a terminal event arrives', async () => {
    const cancel = vi.fn()
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('data: [STREAM_COMPLETE]\n\n'))
      },
      cancel,
    })
    vi.stubGlobal('fetch', vi.fn(async () => new Response(body, { status: 200 })))

    await expect(collectStream('conversation-1', 'Analyze this')).resolves.toEqual([
      '[STREAM_COMPLETE]',
    ])
    expect(cancel).toHaveBeenCalledOnce()
  })

  it('rejects an ordinary EOF that has no terminal event', async () => {
    const body = `data: [TOKEN]${JSON.stringify('Partial answer')}\n\n`
    vi.stubGlobal('fetch', vi.fn(async () => new Response(body, { status: 200 })))

    await expect(collectStream('conversation-1', 'Analyze this')).rejects.toThrow(
      'stream ended before completion',
    )
  })

  it('cancels a stream that stops producing events past the inactivity deadline', async () => {
    const cancel = vi.fn()
    const body = new ReadableStream({ cancel })
    vi.stubGlobal('fetch', vi.fn(async () => new Response(body, { status: 200 })))

    await expect(collectStream(
      'conversation-1',
      'Analyze this',
      true,
      false,
      [],
      { inactivityTimeoutMs: 5 },
    )).rejects.toThrow('stopped sending updates for too long')
    expect(cancel).toHaveBeenCalledOnce()
  })
})


describe('matter document download links', () => {
  it('retains the configured API base and ordinary document identities', () => {
    expect(getMatterDocumentDownloadUrl('matter-id', 'document-id')).toBe(`${API_BASE_URL}/matters/matter-id/documents/document-id/download`)
  })

  it.each([
    '../other?redirect=https://attacker.example/#fragment',
    '"><img src=x onerror=alert(1)>',
    'javascript:alert(1)',
    '//attacker.example/path',
    'folder\\document',
    '%2f..%2fother',
  ])('keeps malformed ID %s inside its own path component', id => {
    const link = getMatterDocumentDownloadUrl(id, id)
    expect(link).toBe(`${API_BASE_URL}/matters/${encodeURIComponent(id)}/documents/${encodeURIComponent(id)}/download`)
    const url = new URL(link, 'https://lawhand.example')
    const baseline = new URL(API_BASE_URL, 'https://lawhand.example')
    expect(url.origin).toBe(baseline.origin)
    expect(url.search).toBe('')
    expect(url.hash).toBe('')
    expect(url.pathname.split('/').slice(-5)).toEqual(['matters', encodeURIComponent(id), 'documents', encodeURIComponent(id), 'download'])
    expect(link).not.toContain('<')
    expect(link).not.toContain('>')
  })

  it.each(['', '.', '..', null, undefined])('omits a navigable link for missing/dot ID %s', id => {
    expect(getMatterDocumentDownloadUrl(id, 'document-id')).toBeUndefined()
    expect(getMatterDocumentDownloadUrl('matter-id', id)).toBeUndefined()
  })
})
