export type OfficeSurface = 'word' | 'excel' | 'outlook'

export type ContextScope = 'selection' | 'current-item'

export interface HostCapabilities {
  surface: OfficeSurface
  requirementSets: Record<string, boolean>
  readableScopes: ContextScope[]
  supportedActions: OfficeActionType[]
  writeEnabled: boolean
}

interface ContextBase {
  surface: OfficeSurface
  scope: ContextScope
  capturedAt: string
  documentFingerprint: string
  hostCapabilities: HostCapabilities
}

export interface WordContextEnvelope extends ContextBase {
  surface: 'word'
  scope: 'selection'
  selection: {
    kind: 'text'
    text: string
    charCount: number
    selectionHash: string
  }
}

export interface ExcelContextEnvelope extends ContextBase {
  surface: 'excel'
  scope: 'selection'
  selection: {
    kind: 'range'
    address: string
    rowCount: number
    columnCount: number
    values: unknown[][]
    formulas: unknown[][]
    numberFormats: unknown[][]
    selectionHash: string
  }
}

export interface OutlookContextEnvelope extends ContextBase {
  surface: 'outlook'
  scope: 'current-item'
  selection: {
    kind: 'mail'
    mode: 'read' | 'compose'
    subject: string
    bodyText: string
    bodyFormat: 'text' | 'html'
    selectionHash: string
  }
}

export type OfficeContextEnvelope =
  | WordContextEnvelope
  | ExcelContextEnvelope
  | OutlookContextEnvelope

export interface ContextRequest {
  scope?: ContextScope
  maxCharacters?: number
  maxCells?: number
}

export interface ActionAnchor {
  selectionHash: string
  address?: string
}

export interface ReplaceSelectionAction {
  type: 'replace_selection'
  anchor: ActionAnchor
  content: { text: string; format: 'text' }
}

export interface SetSelectedValuesAction {
  type: 'set_selected_values'
  anchor: ActionAnchor & { address: string }
  content: { values: unknown[][] }
}

export interface SetSelectedFormulasAction {
  type: 'set_selected_formulas'
  anchor: ActionAnchor & { address: string }
  content: { formulas: string[][] }
}

export interface SetSubjectAction {
  type: 'set_subject'
  anchor: ActionAnchor
  content: { subject: string }
}

export type OfficeAction =
  | ReplaceSelectionAction
  | SetSelectedValuesAction
  | SetSelectedFormulasAction
  | SetSubjectAction

export type OfficeActionType = OfficeAction['type']

export interface OfficeActionPlan {
  planId: string
  surface: OfficeSurface
  expiresAt: string
  baseFingerprint: string
  summary: string
  warnings: string[]
  actions: OfficeAction[]
}

export interface ActionPreviewEntry {
  label: string
  before: string
  after: string
}

export interface ActionPreview {
  planId: string
  surface: OfficeSurface
  entries: ActionPreviewEntry[]
}

export type ActionExecutionStatus = 'applied' | 'rejected' | 'stale' | 'failed'

export interface ActionExecutionResult {
  planId: string
  status: ActionExecutionStatus
  actionCount: number
  resultFingerprint?: string
  errorCode?: string
}

export interface HostAdapter {
  capabilities(): Promise<HostCapabilities>
  captureContext(request?: ContextRequest): Promise<OfficeContextEnvelope>
  preview(plan: OfficeActionPlan): Promise<ActionPreview>
  execute(plan: OfficeActionPlan): Promise<ActionExecutionResult>
}
