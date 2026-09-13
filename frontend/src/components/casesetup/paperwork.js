// Shared model for the client-paperwork kickoff and the live status strip.
//
// The matter page treats paperwork as one lifecycle: choose the documents,
// review and send them, then watch each one until it comes back signed. These
// helpers keep the drawer and the strip describing that lifecycle the same way.

const DAY = 24 * 60 * 60 * 1000

function zoneOffsetMinutes(date, timeZone) {
  try {
    const parts = Object.fromEntries(
      new Intl.DateTimeFormat('en-US', {
        timeZone, hour12: false,
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit',
      })
        .formatToParts(date)
        .filter(part => part.type !== 'literal')
        .map(part => [part.type, part.value]),
    )
    const asUtc = Date.UTC(
      Number(parts.year), Number(parts.month) - 1, Number(parts.day),
      Number(parts.hour) % 24, Number(parts.minute), Number(parts.second),
    )
    return (asUtc - date.getTime()) / 60000
  } catch {
    // An unrecognized zone falls back to the browser's own offset rather than
    // silently dating the deadline in UTC.
    return -date.getTimezoneOffset()
  }
}

// A due date is entered as a calendar day. It lands at 5pm in the client's own
// timezone, which is the hour a deadline actually means to them, and is sent
// with an offset so the server never has to guess.
export function dueDateToIso(day, timeZone) {
  if (!day) return null
  const guess = new Date(`${day}T17:00:00Z`)
  if (Number.isNaN(guess.getTime())) return null
  return new Date(guess.getTime() - zoneOffsetMinutes(guess, timeZone) * 60000).toISOString()
}

export function isoToDueDate(iso, timeZone) {
  if (!iso) return ''
  const value = new Date(iso)
  if (Number.isNaN(value.getTime())) return ''
  try {
    return new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(value)
  } catch {
    return value.toISOString().slice(0, 10)
  }
}

export function formatDue(iso, timeZone) {
  if (!iso) return ''
  const value = new Date(iso)
  if (Number.isNaN(value.getTime())) return ''
  try {
    return new Intl.DateTimeFormat(undefined, { timeZone, month: 'short', day: 'numeric' }).format(value)
  } catch {
    return value.toISOString().slice(0, 10)
  }
}

export function dueTone(iso, completed) {
  if (!iso || completed) return null
  const due = new Date(iso).getTime()
  if (Number.isNaN(due)) return null
  const remaining = due - Date.now()
  if (remaining < 0) return 'overdue'
  if (remaining < 2 * DAY) return 'soon'
  return 'upcoming'
}

// Requirement keys are internal. Every row the firm reads is labelled from the
// packet, falling back to the two requirements that are always present.
export function requirementLabel(key, requirement) {
  if (requirement?.label) return requirement.label
  if (key === 'fee_agreement') return 'Fee agreement'
  if (key === 'questionnaire') return 'Client questionnaire'
  return key.replaceAll('_', ' ')
}

// Fee agreements and `kind: signature` forms are signed inside the portal;
// records (`kind: upload`) and unsigned forms (`kind: document`) are received.
// The fee agreement and the legacy questionnaire carry no kind at all.
function isSigned(requirement, key) {
  return requirement.kind === 'signature' || key === 'fee_agreement' || (!requirement.kind && key !== 'questionnaire')
}

export function requirementState(requirement, key = '') {
  const signed = isSigned(requirement, key)
  if (requirement.completed) {
    if (signed) return { label: 'Signed', tone: 'done' }
    return { label: requirement.kind ? 'Received' : 'Complete', tone: 'done' }
  }
  if (requirement.submitted_document_id) return { label: 'Awaiting review', tone: 'review' }
  // Every signer signed; the executed copy is still being filed to storage.
  if (requirement.signed_pending_filing) return { label: 'Signed — filing', tone: 'waiting' }
  return { label: signed ? 'Needs signature' : 'Outstanding', tone: 'waiting' }
}

// The fee agreement leads: it opens the portal and starts the follow-up clock,
// so it stays at the top of the strip however the packet was assembled.
export function orderedRequirements(packet) {
  if (!packet?.requirements) return []
  const rank = key => (key === 'fee_agreement' ? 0 : key === 'questionnaire' ? 2 : 1)
  return Object.entries(packet.requirements)
    // A packet sent without the legacy questionnaire still carries its row,
    // pre-completed and not required; the firm never needs to see it.
    .filter(([key, requirement]) => !(key === 'questionnaire' && requirement.required === false && requirement.completed))
    .map(([key, requirement]) => ({ key, requirement }))
    .sort((a, b) => rank(a.key) - rank(b.key) || requirementLabel(a.key, a.requirement).localeCompare(requirementLabel(b.key, b.requirement)))
}

export function packetProgress(packet) {
  const rows = orderedRequirements(packet).filter(row => row.requirement.required !== false)
  const done = rows.filter(row => row.requirement.completed).length
  return { done, total: rows.length }
}

// Delivery keys read "welcome:email". The firm cares which message went out on
// which channel, and only ever acts on the ones that did not arrive.
export function deliveryRows(packet) {
  return Object.entries(packet?.delivery || {}).map(([key, state]) => {
    const [kind, channel] = key.split(':')
    return {
      key,
      channel,
      kind: kind.replaceAll('_', ' '),
      state: state.state,
      detail: state.detail || '',
      needsAttention: ['failed', 'blocked', 'unknown'].includes(state.state),
    }
  })
}

// Build the request the intake endpoint expects, carrying a due date for each
// dated item so the server can raise its own assigned follow-up task.
//
// The intake form and questionnaire are PDFs the firm supplies, so they travel
// as `selected_documents` alongside the additional forms; the server's own
// free-text questionnaire is never requested any more.
export function paperworkOptions(draft, timeZone) {
  const lines = value => (value || '').split('\n').map(line => line.trim()).filter(Boolean)
  const forms = draft.forms || []
  return {
    email: draft.email,
    channels: draft.channels,
    timezone: timeZone,
    owner_id: draft.ownerId || null,
    sms_permission_verified: draft.smsPermissionVerified,
    sms_case_updates_verified: Boolean(draft.smsCaseUpdatesVerified),
    agreement_document_id: draft.agreementDocumentId || null,
    agreement_due_at: dueDateToIso(draft.agreementDue, timeZone),
    questionnaire_due_at: null,
    include_questionnaire: false,
    portal_after_signing: draft.portalAfterSigning !== false,
    questions: [],
    selected_documents: [
      ...(draft.intakeFormDocumentId ? [{
        document_id: draft.intakeFormDocumentId,
        label: draft.intakeFormLabel || 'Client intake form',
        requires_signature: draft.intakeFormRequiresSignature !== false,
        due_at: dueDateToIso(draft.intakeFormDue, timeZone),
      }] : []),
      ...(draft.questionnaireDocumentId ? [{
        document_id: draft.questionnaireDocumentId,
        label: draft.questionnaireLabel || 'Client questionnaire',
        requires_signature: draft.questionnaireRequiresSignature !== false,
        due_at: dueDateToIso(draft.questionnaireDue, timeZone),
      }] : []),
      ...forms.map(form => ({
        document_id: form.documentId,
        label: form.label,
        requires_signature: form.requiresSignature,
        due_at: dueDateToIso(form.due, timeZone),
      })),
    ],
    // Records are an explicit opt-in; an unticked section sends nothing even
    // if the textarea still holds a suggested list.
    upload_requirements: draft.requestUploads
      ? lines(draft.uploads).map((label, index) => ({
        key: `upload_${index + 1}`,
        label,
        required: true,
        due_at: dueDateToIso(draft.uploadsDue, timeZone),
      }))
      : [],
    confirm_send: true,
  }
}

export const emptyDraft = {
  agreementDocumentId: '',
  agreementDue: '',
  intakeFormDocumentId: '',
  intakeFormLabel: '',
  intakeFormRequiresSignature: true,
  intakeFormDue: '',
  questionnaireDocumentId: '',
  questionnaireLabel: '',
  questionnaireRequiresSignature: true,
  questionnaireDue: '',
  forms: [],
  requestUploads: false,
  uploads: '',
  uploadsDue: '',
  email: '',
  channels: ['email'],
  smsPermissionVerified: false,
  smsCaseUpdatesVerified: false,
  ownerId: '',
  portalAfterSigning: true,
}
