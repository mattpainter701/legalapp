import { fingerprint, stableSerialize } from '../contracts/fingerprint'
import type {
  ActionExecutionResult,
  ActionPreview,
  ContextRequest,
  ExcelContextEnvelope,
  HostAdapter,
  HostCapabilities,
  OfficeActionPlan,
} from '../contracts/office'
import { PlanValidationError, validatePlan } from '../contracts/validation'

const DEFAULT_MAX_CELLS = 2_500

function matrixShape(matrix: unknown[][]): string {
  return `${matrix.length} × ${matrix[0]?.length ?? 0}`
}

export class ExcelAdapter implements HostAdapter {
  async capabilities(): Promise<HostCapabilities> {
    return {
      surface: 'excel',
      requirementSets: {
        ExcelApi_1_1: Office.context.requirements.isSetSupported('ExcelApi', '1.1'),
        NestedAppAuth_1_1: Office.context.requirements.isSetSupported('NestedAppAuth', '1.1'),
      },
      readableScopes: ['selection'],
      supportedActions: ['set_selected_values', 'set_selected_formulas'],
      writeEnabled: Office.context.requirements.isSetSupported('ExcelApi', '1.1'),
    }
  }

  async captureContext(request: ContextRequest = {}): Promise<ExcelContextEnvelope> {
    const maxCells = request.maxCells ?? DEFAULT_MAX_CELLS
    const captured = await Excel.run(async (context) => {
      const range = context.workbook.getSelectedRange()
      range.load('address,rowCount,columnCount,values,formulas,numberFormat')
      await context.sync()
      if (range.rowCount * range.columnCount > maxCells) {
        throw new Error(`Selection exceeds the ${maxCells.toLocaleString()} cell limit`)
      }
      return {
        address: range.address,
        rowCount: range.rowCount,
        columnCount: range.columnCount,
        values: range.values as unknown[][],
        formulas: range.formulas as unknown[][],
        numberFormats: range.numberFormat as unknown[][],
      }
    })
    const selectionHash = await fingerprint(captured)
    return {
      surface: 'excel',
      scope: 'selection',
      capturedAt: new Date().toISOString(),
      documentFingerprint: selectionHash,
      hostCapabilities: await this.capabilities(),
      selection: { kind: 'range', ...captured, selectionHash },
    }
  }

  async preview(plan: OfficeActionPlan): Promise<ActionPreview> {
    const current = await this.captureContext()
    const checked = validatePlan(plan, 'excel', current.documentFingerprint)
    if (checked.actions.length !== 1) {
      throw new PlanValidationError('unsafe_action_count', 'Excel plans must contain exactly one range-bound action')
    }
    const action = checked.actions[0]
    if (!action || (action.type !== 'set_selected_values' && action.type !== 'set_selected_formulas')) {
      throw new PlanValidationError('unsupported_action', 'Unsupported Excel action')
    }
    if (action.anchor.address !== current.selection.address) {
      throw new PlanValidationError('stale_plan', 'The selected Excel range changed')
    }
    const proposed = action.type === 'set_selected_values' ? action.content.values : action.content.formulas
    if (proposed.length !== current.selection.rowCount || proposed.some((row) => row.length !== current.selection.columnCount)) {
      throw new PlanValidationError('range_shape_mismatch', 'Proposed cells do not match the selected range')
    }
    return {
      planId: checked.planId,
      surface: 'excel',
      entries: [{
        label: `${action.type === 'set_selected_values' ? 'Values' : 'Formulas'} in ${current.selection.address} (${matrixShape(proposed)})`,
        before: stableSerialize(action.type === 'set_selected_values' ? current.selection.values : current.selection.formulas),
        after: stableSerialize(proposed),
      }],
    }
  }

  async execute(plan: OfficeActionPlan): Promise<ActionExecutionResult> {
    try {
      await this.preview(plan)
      const current = await this.captureContext()
      const checked = validatePlan(plan, 'excel', current.documentFingerprint)
      const action = checked.actions[0]
      if (!action || action.anchor.selectionHash !== current.selection.selectionHash || action.anchor.address !== current.selection.address) {
        throw new PlanValidationError('stale_plan', 'The Excel selection changed before apply')
      }
      await Excel.run(async (context) => {
        const range = context.workbook.getSelectedRange()
        if (action.type === 'set_selected_values') {
          range.values = action.content.values
        } else if (action.type === 'set_selected_formulas') {
          range.formulas = action.content.formulas
        } else {
          throw new PlanValidationError('unsupported_action', 'Unsupported Excel action')
        }
        await context.sync()
      })
      const result = await this.captureContext()
      return {
        planId: checked.planId,
        status: 'applied',
        actionCount: 1,
        resultFingerprint: result.documentFingerprint,
      }
    } catch (error) {
      return {
        planId: plan.planId,
        status: error instanceof PlanValidationError && error.code === 'stale_plan' ? 'stale' : 'failed',
        actionCount: 0,
        errorCode: error instanceof PlanValidationError ? error.code : 'excel_write_failed',
      }
    }
  }
}
