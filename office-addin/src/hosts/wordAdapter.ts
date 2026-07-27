import { fingerprint } from '../contracts/fingerprint'
import type {
  ActionExecutionResult,
  ActionPreview,
  ContextRequest,
  HostAdapter,
  HostCapabilities,
  OfficeActionPlan,
  WordContextEnvelope,
} from '../contracts/office'
import { PlanValidationError, validatePlan } from '../contracts/validation'

const DEFAULT_MAX_CHARACTERS = 50_000

export class WordAdapter implements HostAdapter {
  async capabilities(): Promise<HostCapabilities> {
    return {
      surface: 'word',
      requirementSets: {
        WordApi_1_1: Office.context.requirements.isSetSupported('WordApi', '1.1'),
        WordApi_1_4: Office.context.requirements.isSetSupported('WordApi', '1.4'),
        NestedAppAuth_1_1: Office.context.requirements.isSetSupported('NestedAppAuth', '1.1'),
      },
      readableScopes: ['selection'],
      supportedActions: ['replace_selection'],
      writeEnabled: Office.context.requirements.isSetSupported('WordApi', '1.1'),
    }
  }

  async captureContext(request: ContextRequest = {}): Promise<WordContextEnvelope> {
    const maxCharacters = request.maxCharacters ?? DEFAULT_MAX_CHARACTERS
    const captured = await Word.run(async (context) => {
      const selection = context.document.getSelection()
      selection.load('text')
      await context.sync()
      if (selection.text.length > maxCharacters) {
        throw new Error(`Selection exceeds the ${maxCharacters.toLocaleString()} character limit`)
      }
      return { text: selection.text }
    })
    const selectionHash = await fingerprint(captured)
    return {
      surface: 'word',
      scope: 'selection',
      capturedAt: new Date().toISOString(),
      documentFingerprint: selectionHash,
      hostCapabilities: await this.capabilities(),
      selection: {
        kind: 'text',
        text: captured.text,
        charCount: captured.text.length,
        selectionHash,
      },
    }
  }

  async preview(plan: OfficeActionPlan): Promise<ActionPreview> {
    const current = await this.captureContext()
    const checked = validatePlan(plan, 'word', current.documentFingerprint)
    if (current.selection.charCount === 0) {
      throw new PlanValidationError('selection_required', 'Select Word text before requesting a change')
    }
    if (checked.actions.length !== 1) {
      throw new PlanValidationError('unsafe_action_count', 'Word plans must contain exactly one selection-bound action')
    }
    const action = checked.actions[0]
    if (!action || action.type !== 'replace_selection') {
      throw new PlanValidationError('unsupported_action', 'Unsupported Word action')
    }
    return {
      planId: checked.planId,
      surface: 'word',
      entries: [{
        label: 'Replace selection',
        before: current.selection.text,
        after: action.content.text,
      }],
    }
  }

  async execute(plan: OfficeActionPlan): Promise<ActionExecutionResult> {
    try {
      const current = await this.captureContext()
      const checked = validatePlan(plan, 'word', current.documentFingerprint)
      if (current.selection.charCount === 0) {
        throw new PlanValidationError('selection_required', 'Select Word text before applying a change')
      }
      if (checked.actions.length !== 1) {
        throw new PlanValidationError('unsafe_action_count', 'Word plans must contain exactly one action')
      }
      const action = checked.actions[0]
      if (!action || action.anchor.selectionHash !== current.selection.selectionHash) {
        throw new PlanValidationError('stale_plan', 'The Word selection changed before apply')
      }
      if (action.type !== 'replace_selection') {
        throw new PlanValidationError('unsupported_action', 'Unsupported Word action')
      }

      await Word.run(async (context) => {
        const selection = context.document.getSelection()
        selection.insertText(action.content.text, Word.InsertLocation.replace)
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
        errorCode: error instanceof PlanValidationError ? error.code : 'word_write_failed',
      }
    }
  }
}
