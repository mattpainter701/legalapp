import { afterEach, describe, expect, it, vi } from 'vitest'
import { readBlobErrorDetail, streamMessage } from './api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('binary API errors', () => {
  it('extracts the actionable detail from a JSON error blob', async () => {
    const blob = new Blob([JSON.stringify({ detail: 'This PDF has no fillable AcroForm fields.' })], { type: 'application/json' })
    expect(await readBlobErrorDetail(blob)).toBe('This PDF has no fillable AcroForm fields.')
  })
})

describe('chat event stream', () => {
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
})
