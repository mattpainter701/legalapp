export const OPEN_STUDIO_EVENT = 'lawhand.open_studio'

export const STUDIO_FOCUS_KEYS = Object.freeze({
  draft: 'draft_id',
  proposal: 'proposal_id',
  snapshot: 'snapshot_id',
})

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

export const isStudioServerId = (value) => UUID_PATTERN.test(String(value || ''))

export function readStudioFocus(search = '') {
  const params = search instanceof URLSearchParams ? search : new URLSearchParams(search)
  const allowedKeys = new Set(['focus', ...Object.values(STUDIO_FOCUS_KEYS)])
  if ([...params.keys()].some((key) => !allowedKeys.has(key))) {
    return { focus: null, focusId: null, valid: false, message: 'The requested Studio focus contains unsupported data. Showing the template workspace.' }
  }
  const focus = params.get('focus')
  const suppliedFocusIds = Object.entries(STUDIO_FOCUS_KEYS)
    .filter(([, key]) => params.has(key))

  if (!focus && suppliedFocusIds.length === 0) return { focus: null, focusId: null, valid: true }
  if (!Object.hasOwn(STUDIO_FOCUS_KEYS, focus)) {
    return { focus: null, focusId: null, valid: false, message: 'The requested Studio focus is not supported. Showing the template workspace.' }
  }

  const expectedKey = STUDIO_FOCUS_KEYS[focus]
  const focusId = params.get(expectedKey)
  if (suppliedFocusIds.length !== 1 || suppliedFocusIds[0][1] !== expectedKey || !isStudioServerId(focusId)) {
    return { focus: null, focusId: null, valid: false, message: 'The requested Studio focus is invalid or unavailable. Showing the template workspace.' }
  }

  return { focus, focusId, valid: true }
}

export function buildOpenStudioTarget(detail = {}) {
  const templateId = detail.template_id
  if (!isStudioServerId(templateId)) {
    return { url: '/templates', valid: false, message: 'The Studio event did not include a valid template ID. Showing Template Studio home.' }
  }

  const baseUrl = `/templates/${encodeURIComponent(templateId)}/studio`
  const allowedDetailKeys = new Set(['template_id', 'focus', ...Object.values(STUDIO_FOCUS_KEYS)])
  if (Object.keys(detail).some((key) => !allowedDetailKeys.has(key))) {
    return { url: baseUrl, valid: false, message: 'The Studio event contained unsupported data. Showing the template workspace.' }
  }
  const focus = detail.focus
  const suppliedIds = Object.entries(STUDIO_FOCUS_KEYS).filter(([, key]) => detail[key] != null)
  if (focus == null && suppliedIds.length === 0) return { url: baseUrl, valid: true }

  const focusKey = STUDIO_FOCUS_KEYS[focus]
  if (!focusKey || suppliedIds.length !== 1 || suppliedIds[0][1] !== focusKey || !isStudioServerId(detail[focusKey])) {
    return { url: baseUrl, valid: false, message: 'The Studio event focus was invalid or unavailable. Showing the template workspace.' }
  }

  const params = new URLSearchParams({ focus, [focusKey]: detail[focusKey] })
  return { url: `${baseUrl}?${params.toString()}`, valid: true }
}
