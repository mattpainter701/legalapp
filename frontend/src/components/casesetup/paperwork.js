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

export function requirementState(requirement) {
  if (requirement.completed) return { label: 'Signed', tone: 'done' }
  if (requirement.submitted_document_id) return { label: 'Awaiting review', tone: 'review' }
  return { label: 'Outstanding', tone: 'waiting' }
}

// The fee agreement leads: it opens the portal and starts the follow-up clock,
// so it stays at the top of the strip however the packet was assembled.
export function orderedRequirements(packet) {
  if (!packet?.requirements) return []
  const rank = key => (key === 'fee_agreement' ? 0 : key === 'questionnaire' ? 2 : 1)
  return Object.entries(packet.requirements)
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
export function paperworkOptions(draft, timeZone) {
  const lines = value => (value || '').split('\n').map(line => line.trim()).filter(Boolean)
  return {
    email: draft.email,
    channels: draft.channels,
    timezone: timeZone,
    owner_id: draft.ownerId || null,
    sms_permission_verified: draft.smsPermissionVerified,
    sms_case_updates_verified: Boolean(draft.smsCaseUpdatesVerified),
    agreement_document_id: draft.agreementDocumentId || null,
    agreement_due_at: dueDateToIso(draft.agreementDue, timeZone),
    questionnaire_due_at: draft.includeQuestionnaire ? dueDateToIso(draft.questionnaireDue, timeZone) : null,
    include_questionnaire: draft.includeQuestionnaire,
    portal_after_signing: draft.portalAfterSigning !== false,
    questions: draft.includeQuestionnaire
      ? lines(draft.questions).map((label, index) => ({ key: `question_${index + 1}`, label, required: true }))
      : [],
    selected_documents: [
      ...draft.forms.map(form => ({
        document_id: form.documentId,
        label: form.label,
        requires_signature: form.requiresSignature,
        due_at: dueDateToIso(form.due, timeZone),
      })),
      // The client intake form is one of the three common pieces and is not a
      // signature document: the client fills it in, so it travels unsigned.
      ...(draft.intakeFormDocumentId ? [{
        document_id: draft.intakeFormDocumentId,
        label: draft.intakeFormLabel || 'Client intake form',
        requires_signature: false,
        due_at: null,
      }] : []),
    ],
    upload_requirements: lines(draft.uploads).map((label, index) => ({
      key: `upload_${index + 1}`,
      label,
      required: true,
      due_at: dueDateToIso(draft.uploadsDue, timeZone),
    })),
    confirm_send: true,
  }
}

export const emptyDraft = {
  agreementDocumentId: '',
  agreementDue: '',
  forms: [],
  includeQuestionnaire: true,
  questionnaireDue: '',
  questions: 'Please describe your legal matter.\nWho are the other people or organizations involved?\nWhat important dates should your legal team know about? Enter none if unknown.',
  uploads: '',
  uploadsDue: '',
  intakeFormDocumentId: '',
  intakeFormLabel: '',
  email: '',
  channels: ['email'],
  smsPermissionVerified: false,
  smsCaseUpdatesVerified: false,
  ownerId: '',
  portalAfterSigning: true,
}
