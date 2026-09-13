import { useState } from 'react'
import { Link } from 'react-router-dom'
import { PlugZap, X } from 'lucide-react'
import { useAuth } from '../App'

const DISMISS_PREFIX = 'lawhand.mcp-reconnect.dismissed'

export function dismissedReconnectKey(userId, disconnectedAt) {
  return `${DISMISS_PREFIX}.${userId}.${disconnectedAt}`
}

function wasDismissed(userId, disconnectedAt) {
  try {
    return window.localStorage.getItem(dismissedReconnectKey(userId, disconnectedAt)) === '1'
  } catch {
    return false
  }
}

function rememberDismissed(userId, disconnectedAt) {
  try {
    window.localStorage.setItem(dismissedReconnectKey(userId, disconnectedAt), '1')
  } catch {
    // The prompt stays optional when device storage is unavailable; the grants
    // panel still lists every assistant waiting to be reconnected.
  }
}

export function reconnectSentence(names) {
  if (names.length === 1) return `${names[0]} was disconnected`
  if (names.length === 2) return `${names[0]} and ${names[1]} were disconnected`
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]} were disconnected`
}

// Resetting a password disconnects this user's Workspace MCP assistants, which
// is the point — but reconnecting is an OAuth flow only the assistant itself can
// start, so nothing we do server-side can restore it. All the product can do is
// make sure nobody has to work out *that* it happened, or to which assistant.
// Without this the symptom is an assistant that silently stops answering.
export default function WorkspaceMcpReconnectBanner() {
  const { user } = useAuth()
  const pending = user?.workspace_mcp_reconnect || []
  const latest = pending[0]?.disconnected_at || ''
  const [dismissed, setDismissed] = useState(false)

  if (!user?.id || pending.length === 0) return null
  if (dismissed || wasDismissed(user.id, latest)) return null

  const names = pending.map((item) => item.client_name || 'An assistant')

  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-start gap-3 border-b border-amber-300 bg-amber-50 px-4 py-3 text-amber-950"
    >
      <PlugZap size={18} className="mt-0.5 shrink-0" aria-hidden="true" />
      <p className="flex-1 font-sans text-sm leading-5">
        <span className="font-semibold">{reconnectSentence(names)} when you reset your password.</span>{' '}
        That was deliberate — a reset cuts off anything connected to your account. Reconnect{' '}
        {pending.length === 1 ? 'it' : 'them'} from the assistant itself when you are ready.{' '}
        <Link to="/profile" className="font-semibold underline underline-offset-2">
          How to reconnect
        </Link>
      </p>
      <button
        type="button"
        onClick={() => {
          rememberDismissed(user.id, latest)
          setDismissed(true)
        }}
        aria-label="Dismiss reconnect reminder"
        className="shrink-0 rounded p-1 hover:bg-amber-100"
      >
        <X size={16} aria-hidden="true" />
      </button>
    </div>
  )
}
