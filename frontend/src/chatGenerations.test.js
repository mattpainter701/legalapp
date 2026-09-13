import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  GENERATION_ABORTED,
  GENERATION_COMPLETE,
  GENERATION_ERROR,
  GENERATION_STREAMING,
  abortChatGeneration,
  beginChatGeneration,
  countStreamingChatGenerations,
  getChatGeneration,
  patchChatGeneration,
  releaseChatGeneration,
  resetChatGenerations,
  settleChatGeneration,
  subscribeToChatGenerations,
} from './chatGenerations'

const start = (conversationId, clientTurnId = 'turn-1', controller = null) => beginChatGeneration({
  conversationId,
  clientTurnId,
  controller,
  userMessage: { id: `temp-${clientTurnId}`, role: 'user', content: 'Question' },
  assistantMessage: { id: `stream-${clientTurnId}`, role: 'assistant', content: '' },
})

describe('chat generation registry', () => {
  afterEach(() => resetChatGenerations())

  it('keeps a streamed turn readable after the page that started it is gone', () => {
    start('conversation-a')
    patchChatGeneration('conversation-a', 'turn-1', { assistant: { content: 'Partial' } })
    patchChatGeneration('conversation-a', 'turn-1', { assistant: { content: 'Partial answer' } })

    const record = getChatGeneration('conversation-a')
    expect(record.assistantMessage.content).toBe('Partial answer')
    expect(record.assistantMessage.role).toBe('assistant')
    expect(record.status).toBe(GENERATION_STREAMING)
    expect(countStreamingChatGenerations()).toBe(1)
  })

  it('replaces records instead of mutating them so subscribers see a change', () => {
    const listener = vi.fn()
    const unsubscribe = subscribeToChatGenerations(listener)
    const begun = start('conversation-a')

    const patched = patchChatGeneration('conversation-a', 'turn-1', { assistant: { content: 'Token' } })
    expect(patched).not.toBe(begun)
    expect(begun.assistantMessage.content).toBe('')
    expect(listener).toHaveBeenCalledTimes(2)

    unsubscribe()
    patchChatGeneration('conversation-a', 'turn-1', { assistant: { content: 'Token two' } })
    expect(listener).toHaveBeenCalledTimes(2)
  })

  it('ignores writes aimed at a turn the conversation has moved past', () => {
    start('conversation-a', 'turn-1')
    start('conversation-a', 'turn-2')

    expect(patchChatGeneration('conversation-a', 'turn-1', { assistant: { content: 'Stale' } })).toBeNull()
    expect(settleChatGeneration('conversation-a', 'turn-1', { status: GENERATION_COMPLETE })).toBeNull()
    expect(releaseChatGeneration('conversation-a', 'turn-1')).toBe(false)
    expect(getChatGeneration('conversation-a').clientTurnId).toBe('turn-2')
  })

  it('stops counting a settled turn but keeps it for the page that will reconcile it', () => {
    start('conversation-a')
    settleChatGeneration('conversation-a', 'turn-1', {
      status: GENERATION_ERROR,
      error: 'Connection lost',
      attached: false,
      assistant: { content: 'Partial answer' },
    })

    const record = getChatGeneration('conversation-a')
    expect(record.status).toBe(GENERATION_ERROR)
    expect(record.error).toBe('Connection lost')
    expect(record.attached).toBe(false)
    expect(record.assistantMessage.content).toBe('Partial answer')
    expect(countStreamingChatGenerations()).toBe(0)

    expect(releaseChatGeneration('conversation-a', 'turn-1')).toBe(true)
    expect(getChatGeneration('conversation-a')).toBeNull()
  })

  it('counts one streaming turn per conversation and releases the right one', () => {
    start('conversation-a', 'turn-a')
    start('conversation-b', 'turn-b')
    expect(countStreamingChatGenerations()).toBe(2)

    releaseChatGeneration('conversation-a')
    expect(getChatGeneration('conversation-a')).toBeNull()
    expect(getChatGeneration('conversation-b').clientTurnId).toBe('turn-b')
    expect(countStreamingChatGenerations()).toBe(1)
  })

  it('aborts the read only when asked, and never as a side effect of release', () => {
    const released = new AbortController()
    const aborted = new AbortController()
    start('conversation-a', 'turn-a', released)
    start('conversation-b', 'turn-b', aborted)

    releaseChatGeneration('conversation-a', 'turn-a')
    expect(released.signal.aborted).toBe(false)

    expect(abortChatGeneration('conversation-b')).toBe(true)
    expect(aborted.signal.aborted).toBe(true)
    expect(getChatGeneration('conversation-b').status).toBe(GENERATION_ABORTED)
    expect(countStreamingChatGenerations()).toBe(0)
    expect(abortChatGeneration('conversation-missing')).toBe(false)
  })

  it('evicts long-settled turns rather than holding every answer for the tab', () => {
    vi.useFakeTimers()
    try {
      start('conversation-old')
      settleChatGeneration('conversation-old', 'turn-1', { status: GENERATION_COMPLETE })
      vi.advanceTimersByTime(16 * 60 * 1000)

      start('conversation-new')
      expect(getChatGeneration('conversation-old')).toBeNull()
      expect(getChatGeneration('conversation-new')).not.toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it('keeps a streaming turn alive however long it runs', () => {
    vi.useFakeTimers()
    try {
      start('conversation-slow')
      vi.advanceTimersByTime(60 * 60 * 1000)

      start('conversation-other')
      expect(getChatGeneration('conversation-slow').status).toBe(GENERATION_STREAMING)
    } finally {
      vi.useRealTimers()
    }
  })

  it('keeps publishing to the remaining subscribers when one of them throws', () => {
    const healthy = vi.fn()
    subscribeToChatGenerations(() => { throw new Error('subscriber blew up') })
    subscribeToChatGenerations(healthy)

    expect(() => start('conversation-a')).not.toThrow()
    expect(healthy).toHaveBeenCalledTimes(1)
  })

  it('refuses to register a turn it could not address later', () => {
    expect(beginChatGeneration({ conversationId: null, clientTurnId: 'turn-1' })).toBeNull()
    expect(beginChatGeneration({ conversationId: 'conversation-a', clientTurnId: '' })).toBeNull()
    expect(countStreamingChatGenerations()).toBe(0)
  })
})
