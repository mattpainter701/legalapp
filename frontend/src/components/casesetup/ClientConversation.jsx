import { useCallback, useEffect, useRef, useState } from 'react'
import { MessageSquare } from 'lucide-react'
import {
  getMatterPortalMessages,
  markMatterPortalMessagesRead,
  sendMatterPortalMessage,
} from '../../api'

const errorText = caught => (typeof caught?.response?.data?.detail === 'string'
  ? caught.response.data.detail
  : 'Could not send the message. Try again.')

function when(value) {
  try {
    return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(new Date(value))
  } catch {
    return ''
  }
}

/**
 * The firm's half of the client portal thread.
 *
 * The client could always write in; nothing could write back, so replies left
 * the matter and went out through somebody's personal mailbox. This keeps the
 * conversation on the case, and marks it read so the unread badge means
 * something.
 */
export default function ClientConversation({ matterId, onUnreadChange }) {
  const [messages, setMessages] = useState([])
  const [unread, setUnread] = useState(0)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const reportUnread = useRef(onUnreadChange)
  reportUnread.current = onUnreadChange

  const load = useCallback(async () => {
    try {
      const data = await getMatterPortalMessages(matterId, { limit: 50 })
      setMessages(Array.isArray(data?.messages) ? data.messages : [])
      setUnread(data?.unread_count || 0)
      reportUnread.current?.(data?.unread_count || 0)
      setError('')
    } catch {
      // A thread that will not load must not blank the Overview; the compose
      // box below still works.
      setMessages([])
    } finally {
      setLoading(false)
    }
  }, [matterId])

  useEffect(() => {
    setLoading(true)
    load()
    const timer = setInterval(load, 60000)
    return () => clearInterval(timer)
  }, [load])

  async function markRead() {
    if (!unread) return
    try {
      await markMatterPortalMessagesRead(matterId)
      setUnread(0)
      reportUnread.current?.(0)
      setMessages(previous => previous.map(message => ({ ...message, unread: false })))
    } catch { /* The badge clears on the next load. */ }
  }

  async function send() {
    const body = draft.trim()
    if (!body) return
    setBusy(true)
    setError('')
    try {
      const message = await sendMatterPortalMessage(matterId, { body })
      setMessages(previous => [...previous, message])
      setDraft('')
    } catch (caught) {
      setError(errorText(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section aria-label="Client conversation" className="rounded-2xl border border-brand-line bg-brand-surface p-5 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 font-serif text-lg font-bold text-brand-ink">
          <MessageSquare size={17} className="text-brand-accent" />
          Client messages
          {unread > 0 && (
            <span className="rounded-full bg-brand-rose px-2 py-0.5 text-[11px] font-bold text-white">{unread} new</span>
          )}
        </h2>
        {unread > 0 && (
          <button type="button" onClick={markRead} className="min-h-11 text-[13px] font-semibold text-brand-accent underline">
            Mark as read
          </button>
        )}
      </div>

      {loading ? (
        <p role="status" className="mt-3 text-[13px] text-brand-muted">Loading messages…</p>
      ) : messages.length === 0 ? (
        <p className="mt-3 text-[13px] text-brand-muted">
          No messages yet. Anything you send here appears in the client&apos;s secure portal, and they get an email telling them to look.
        </p>
      ) : (
        <ul className="mt-4 max-h-80 space-y-2 overflow-y-auto pr-1">
          {messages.map(message => {
            const fromClient = message.direction === 'inbound'
            return (
              <li
                key={message.id}
                className={`rounded-xl border px-4 py-3 ${fromClient
                  ? `bg-brand-bg-soft ${message.unread ? 'border-brand-rose/40' : 'border-brand-line'}`
                  : 'ml-6 border-brand-line bg-brand-surface'}`}
              >
                <div className="flex items-center justify-between gap-2 text-[11px] font-semibold uppercase tracking-wider text-brand-muted">
                  <span>{fromClient ? 'Client' : 'Your firm'}</span>
                  <span>{when(message.occurred_at)}</span>
                </div>
                {message.subject && message.subject !== 'Portal message' && (
                  <p className="mt-1 text-[13px] font-semibold text-brand-ink">{message.subject}</p>
                )}
                <p className="mt-1 whitespace-pre-wrap text-[13px] text-brand-ink-2">{message.body}</p>
              </li>
            )
          })}
        </ul>
      )}

      <div className="mt-4">
        <label htmlFor="client-message" className="mb-1.5 block text-[12px] font-semibold uppercase tracking-wider text-brand-muted">
          Reply to the client
        </label>
        <textarea
          id="client-message"
          rows={3}
          value={draft}
          onChange={event => setDraft(event.target.value)}
          placeholder="Your message appears in the client's secure portal."
          className="block w-full rounded-lg border border-brand-line bg-brand-surface px-3 py-2 text-sm text-brand-ink focus:border-brand-accent focus:outline-none focus:ring-1 focus:ring-brand-accent"
        />
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
          <p className="text-[12px] text-brand-muted">The email tells them a message is waiting; it never contains the message.</p>
          <button
            type="button"
            disabled={busy || !draft.trim()}
            onClick={send}
            className="min-h-11 rounded-lg bg-brand-ink px-5 text-[13px] font-semibold text-white disabled:opacity-50"
          >
            {busy ? 'Sending…' : 'Send message'}
          </button>
        </div>
      </div>

      {error && <p role="alert" className="mt-3 text-[13px] text-brand-rose">{error}</p>}
    </section>
  )
}
