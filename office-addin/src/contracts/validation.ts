import type {
  ActionAnchor,
  OfficeAction,
  OfficeActionPlan,
  OfficeActionType,
  OfficeSurface,
} from './office'

const MAX_ACTIONS = 20
const MAX_TEXT_LENGTH = 100_000
const MAX_SUBJECT_LENGTH = 998
const MAX_WARNINGS = 10

const ACTIONS_BY_SURFACE: Record<OfficeSurface, ReadonlySet<OfficeActionType>> = {
  word: new Set(['replace_selection']),
  excel: new Set(['set_selected_values', 'set_selected_formulas']),
  outlook: new Set(['set_subject']),
}

export class PlanValidationError extends Error {
  readonly code: string

  constructor(code: string, message: string) {
    super(message)
    this.name = 'PlanValidationError'
    this.code = code
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function assertRecord(value: unknown, name: string): Record<string, unknown> {
  if (!isRecord(value)) throw new PlanValidationError('invalid_shape', `${name} must be an object`)
  return value
}

function assertKeys(
  value: Record<string, unknown>,
  allowed: readonly string[],
  required: readonly string[],
  name: string,
): void {
  const allowedSet = new Set(allowed)
  const unknown = Object.keys(value).find((key) => !allowedSet.has(key))
  if (unknown) throw new PlanValidationError('unknown_field', `${name}.${unknown} is not allowed`)
  const missing = required.find((key) => !(key in value))
  if (missing) throw new PlanValidationError('missing_field', `${name}.${missing} is required`)
}

function assertString(value: unknown, name: string, max = MAX_TEXT_LENGTH): string {
  if (typeof value !== 'string' || value.length === 0 || value.length > max) {
    throw new PlanValidationError('invalid_string', `${name} must be a non-empty string of at most ${max} characters`)
  }
  return value
}

function validateAnchor(raw: unknown, name: string): ActionAnchor {
  const anchor = assertRecord(raw, name)
  assertKeys(anchor, ['selectionHash', 'address'], ['selectionHash'], name)
  const result: ActionAnchor = { selectionHash: assertString(anchor.selectionHash, `${name}.selectionHash`, 200) }
  if (anchor.address !== undefined) result.address = assertString(anchor.address, `${name}.address`, 300)
  return result
}

function validateMatrix(raw: unknown, name: string): unknown[][] {
  if (!Array.isArray(raw) || raw.length === 0 || raw.length > 10_000) {
    throw new PlanValidationError('invalid_matrix', `${name} must be a non-empty bounded matrix`)
  }
  let width: number | null = null
  return raw.map((row, rowIndex) => {
    if (!Array.isArray(row) || row.length === 0) {
      throw new PlanValidationError('invalid_matrix', `${name}[${rowIndex}] must be a non-empty row`)
    }
    if (width === null) width = row.length
    if (row.length !== width) throw new PlanValidationError('invalid_matrix', `${name} must be rectangular`)
    return row
  })
}

function validateAction(raw: unknown, surface: OfficeSurface, index: number): OfficeAction {
  const name = `actions[${index}]`
  const action = assertRecord(raw, name)
  assertKeys(action, ['type', 'anchor', 'content'], ['type', 'anchor', 'content'], name)
  const type = assertString(action.type, `${name}.type`, 80) as OfficeActionType
  if (!ACTIONS_BY_SURFACE[surface].has(type)) {
    throw new PlanValidationError('unsupported_action', `${type} is not allowed for ${surface}`)
  }
  const anchor = validateAnchor(action.anchor, `${name}.anchor`)
  const content = assertRecord(action.content, `${name}.content`)

  if (type === 'replace_selection') {
    assertKeys(content, ['text', 'format'], ['text', 'format'], `${name}.content`)
    if (content.format !== 'text') throw new PlanValidationError('invalid_format', `${type} supports text only`)
    return { type, anchor, content: { text: assertString(content.text, `${name}.content.text`), format: 'text' } }
  }

  if (type === 'set_selected_values') {
    assertKeys(content, ['values'], ['values'], `${name}.content`)
    if (!anchor.address) throw new PlanValidationError('missing_address', `${name}.anchor.address is required`)
    return { type, anchor: { ...anchor, address: anchor.address }, content: { values: validateMatrix(content.values, `${name}.content.values`) } }
  }

  if (type === 'set_selected_formulas') {
    assertKeys(content, ['formulas'], ['formulas'], `${name}.content`)
    if (!anchor.address) throw new PlanValidationError('missing_address', `${name}.anchor.address is required`)
    const formulas = validateMatrix(content.formulas, `${name}.content.formulas`).map((row, rowIndex) =>
      row.map((cell, columnIndex) => {
        if (typeof cell !== 'string' || !cell.startsWith('=')) {
          throw new PlanValidationError('invalid_formula', `${name}.content.formulas[${rowIndex}][${columnIndex}] must start with =`)
        }
        return cell
      }),
    )
    return { type, anchor: { ...anchor, address: anchor.address }, content: { formulas } }
  }

  assertKeys(content, ['subject'], ['subject'], `${name}.content`)
  return { type: 'set_subject', anchor, content: { subject: assertString(content.subject, `${name}.content.subject`, MAX_SUBJECT_LENGTH) } }
}

export function validatePlan(
  raw: unknown,
  expectedSurface: OfficeSurface,
  expectedFingerprint: string,
  now = new Date(),
): OfficeActionPlan {
  const plan = assertRecord(raw, 'plan')
  assertKeys(
    plan,
    ['planId', 'surface', 'expiresAt', 'baseFingerprint', 'summary', 'warnings', 'actions'],
    ['planId', 'surface', 'expiresAt', 'baseFingerprint', 'summary', 'warnings', 'actions'],
    'plan',
  )

  if (plan.surface !== expectedSurface) {
    throw new PlanValidationError('surface_mismatch', `Plan is for ${String(plan.surface)}, not ${expectedSurface}`)
  }
  const baseFingerprint = assertString(plan.baseFingerprint, 'plan.baseFingerprint', 200)
  if (baseFingerprint !== expectedFingerprint) {
    throw new PlanValidationError('stale_plan', 'The Office context changed before this plan was reviewed')
  }
  const expiresAt = assertString(plan.expiresAt, 'plan.expiresAt', 80)
  const expiry = Date.parse(expiresAt)
  if (!Number.isFinite(expiry) || expiry <= now.getTime()) {
    throw new PlanValidationError('expired_plan', 'The proposed change has expired')
  }
  if (!Array.isArray(plan.warnings) || plan.warnings.length > MAX_WARNINGS) {
    throw new PlanValidationError('invalid_warnings', `plan.warnings must contain at most ${MAX_WARNINGS} entries`)
  }
  const warnings = plan.warnings.map((warning, index) => assertString(warning, `plan.warnings[${index}]`, 500))
  if (!Array.isArray(plan.actions) || plan.actions.length === 0 || plan.actions.length > MAX_ACTIONS) {
    throw new PlanValidationError('invalid_actions', `plan.actions must contain between 1 and ${MAX_ACTIONS} actions`)
  }

  const actions = plan.actions.map((action, index) => validateAction(action, expectedSurface, index))
  if (actions.some((action) => action.anchor.selectionHash !== baseFingerprint)) {
    throw new PlanValidationError('anchor_mismatch', 'Every action must be bound to the captured Office context')
  }

  return {
    planId: assertString(plan.planId, 'plan.planId', 100),
    surface: expectedSurface,
    expiresAt,
    baseFingerprint,
    summary: assertString(plan.summary, 'plan.summary', 1000),
    warnings,
    actions,
  }
}
