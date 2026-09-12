// Wording shared between the in-document signing form and the Signatures tab
// that hosts it. The tab keeps showing the outcome after the form unmounts, so
// both sides must say exactly the same thing.

export const CONSENT_TEXT_VERSION = 'clarity-esign-consent-v1'

export const CONSENT_TEXT = 'I consent to use an electronic signature for this acknowledgment. I understand my typed name and audit evidence will be attached to an evidence certificate linked by hash to the source document, and the source document itself is not modified.'

export const SIGNED_MESSAGE = 'Signed. Your legal team has the signed copy and an evidence certificate.'

// Storage outages must never read as a failed signature. The signature is
// recorded; only the filing of the executed copy is retried later.
export const FILING_PENDING_MESSAGE = 'Your signature is recorded. Your signed copy is being filed and your legal team will be notified.'

export const SIGNED_COPY_RECEIVED_MESSAGE = 'Signed copy received — awaiting your legal team’s review'

export function signedOutcomeMessage(result) {
  return result?.completion_pending ? FILING_PENDING_MESSAGE : SIGNED_MESSAGE
}
