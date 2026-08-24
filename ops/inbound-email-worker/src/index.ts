interface Env {
  BACKEND_INGEST_URL: string
  INBOUND_EMAIL_DOMAIN: string
  INBOUND_EMAIL_WEBHOOK_SECRET: string
  MAX_EMAIL_BYTES: string
}

const encoder = new TextEncoder()
const MATTER_ALIAS = /^m-[a-z2-7]{26}$/

function toHex(bytes: ArrayBuffer): string {
  return Array.from(new Uint8Array(bytes))
    .map((value) => value.toString(16).padStart(2, '0'))
    .join('')
}

async function hmacHex(secret: string, payload: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  )
  return toHex(await crypto.subtle.sign('HMAC', key, encoder.encode(payload)))
}

export default {
  async email(message, env): Promise<void> {
    const sender = message.from.trim().toLowerCase()
    const recipient = message.to.trim().toLowerCase()
    const [localPart, domain, extra] = recipient.split('@')
    const expectedDomain = env.INBOUND_EMAIL_DOMAIN.trim().toLowerCase().replace(/\.$/, '')

    if (extra || domain?.replace(/\.$/, '') !== expectedDomain || !MATTER_ALIAS.test(localPart)) {
      message.setReject('Address not recognized')
      return
    }

    const limit = Number.parseInt(env.MAX_EMAIL_BYTES || '26214400', 10)
    if (!Number.isFinite(limit) || message.rawSize <= 0 || message.rawSize > limit) {
      message.setReject('Message exceeds the accepted size')
      return
    }

    const raw = await new Response(message.raw).arrayBuffer()
    if (raw.byteLength !== message.rawSize || raw.byteLength > limit) {
      message.setReject('Message exceeds the accepted size')
      return
    }

    const timestamp = Math.floor(Date.now() / 1000).toString()
    const bodyDigest = toHex(await crypto.subtle.digest('SHA-256', raw))
    const payload = `v1:${timestamp}\n${sender}\n${recipient}\n${bodyDigest}`
    const signature = `v1=${await hmacHex(env.INBOUND_EMAIL_WEBHOOK_SECRET, payload)}`

    const response = await fetch(env.BACKEND_INGEST_URL, {
      method: 'POST',
      headers: {
        'content-type': 'message/rfc822',
        'x-lawhand-envelope-from': sender,
        'x-lawhand-envelope-to': recipient,
        'x-lawhand-timestamp': timestamp,
        'x-lawhand-signature': signature,
      },
      body: raw,
    })

    if (response.status === 413) {
      message.setReject('Message exceeds the accepted size')
      return
    }
    if (!response.ok) {
      throw new Error(`LawHand inbound ingest returned HTTP ${response.status}`)
    }
  },
} satisfies ExportedHandler<Env>
