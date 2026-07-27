import { fingerprint } from '../contracts/fingerprint'
import type {
  ActionExecutionResult,
  ActionPreview,
  ContextRequest,
  HostAdapter,
  HostCapabilities,
  OfficeActionPlan,
  OutlookContextEnvelope,
} from '../contracts/office'
import { PlanValidationError, validatePlan } from '../contracts/validation'

const DEFAULT_MAX_CHARACTERS = 50_000

interface OutlookBodyLike {
  getAsync(coercionType: Office.CoercionType, callback: (result: Office.AsyncResult<string>) => void): void
  setSelectedDataAsync?: (
    data: string,
    options: { coercionType: Office.CoercionType },
    callback: (result: Office.AsyncResult<void>) => void,
  ) => void
}

interface OutlookSubjectLike {
  getAsync(callback: (result: Office.AsyncResult<string>) => void): void
  setAsync?: (value: string, callback: (result: Office.AsyncResult<void>) => void) => void
}

interface OutlookItemLike {
  body: OutlookBodyLike
  subject: string | OutlookSubjectLike
}

function asyncValue<T>(register: (callback: (result: Office.AsyncResult<T>) => void) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    register((result) => {
      if (result.status === Office.AsyncResultStatus.Succeeded) resolve(result.value)
      else reject(new Error(result.error?.message || 'Outlook operation failed'))
    })
  })
}

export class OutlookAdapter implements HostAdapter {
  private item(): OutlookItemLike {
    return Office.context.mailbox.item as unknown as OutlookItemLike
  }

  private isCompose(): boolean {
    const item = this.item()
    return typeof item.body.setSelectedDataAsync === 'function'
  }

  private async subject(): Promise<string> {
    const subject = this.item().subject
    if (typeof subject === 'string') return subject
    return asyncValue<string>((callback) => subject.getAsync(callback))
  }

  async capabilities(): Promise<HostCapabilities> {
    const compose = this.isCompose()
    return {
      surface: 'outlook',
      requirementSets: {
        Mailbox_1_1: Office.context.requirements.isSetSupported('Mailbox', '1.1'),
        Mailbox_1_3: Office.context.requirements.isSetSupported('Mailbox', '1.3'),
        NestedAppAuth_1_1: Office.context.requirements.isSetSupported('NestedAppAuth', '1.1'),
      },
      readableScopes: ['current-item'],
      supportedActions: compose ? ['set_subject'] : [],
      writeEnabled: compose,
    }
  }

  async captureContext(request: ContextRequest = {}): Promise<OutlookContextEnvelope> {
    const maxCharacters = request.maxCharacters ?? DEFAULT_MAX_CHARACTERS
    const item = this.item()
    const [bodyText, subject] = await Promise.all([
      asyncValue<string>((callback) => item.body.getAsync(Office.CoercionType.Text, callback)),
      this.subject(),
    ])
    if (bodyText.length > maxCharacters) {
      throw new Error(`Current Outlook item exceeds the ${maxCharacters.toLocaleString()} character limit`)
    }
    const mode: 'compose' | 'read' = this.isCompose() ? 'compose' : 'read'
    const captured = { mode, subject, bodyText, bodyFormat: 'text' as const }
    const selectionHash = await fingerprint(captured)
    return {
      surface: 'outlook',
      scope: 'current-item',
      capturedAt: new Date().toISOString(),
      documentFingerprint: selectionHash,
      hostCapabilities: await this.capabilities(),
      selection: { kind: 'mail', ...captured, selectionHash },
    }
  }

  async preview(plan: OfficeActionPlan): Promise<ActionPreview> {
    const current = await this.captureContext()
    const checked = validatePlan(plan, 'outlook', current.documentFingerprint)
    if (current.selection.mode !== 'compose') {
      throw new PlanValidationError('read_only_item', 'Outlook read items cannot be changed')
    }
    if (checked.actions.length !== 1) {
      throw new PlanValidationError('unsafe_action_count', 'Outlook plans must contain exactly one item-bound action')
    }
    const action = checked.actions[0]
    if (!action) throw new PlanValidationError('invalid_actions', 'Outlook action is missing')
    if (action.type === 'set_subject') {
      return { planId: checked.planId, surface: 'outlook', entries: [{ label: 'Subject', before: current.selection.subject, after: action.content.subject }] }
    }
    throw new PlanValidationError('unsupported_action', 'Unsupported Outlook action')
  }

  async execute(plan: OfficeActionPlan): Promise<ActionExecutionResult> {
    try {
      await this.preview(plan)
      const current = await this.captureContext()
      const checked = validatePlan(plan, 'outlook', current.documentFingerprint)
      const action = checked.actions[0]
      if (!action || action.anchor.selectionHash !== current.selection.selectionHash) {
        throw new PlanValidationError('stale_plan', 'The Outlook draft changed before apply')
      }
      const item = this.item()
      if (action.type === 'set_subject') {
        const subject = item.subject
        if (typeof subject === 'string' || !subject.setAsync) throw new PlanValidationError('read_only_item', 'Subject is read-only')
        await asyncValue<void>((callback) => subject.setAsync?.(action.content.subject, callback))
      } else {
        throw new PlanValidationError('unsupported_action', 'Unsupported Outlook action')
      }
      const result = await this.captureContext()
      return { planId: checked.planId, status: 'applied', actionCount: 1, resultFingerprint: result.documentFingerprint }
    } catch (error) {
      return {
        planId: plan.planId,
        status: error instanceof PlanValidationError && error.code === 'stale_plan' ? 'stale' : 'failed',
        actionCount: 0,
        errorCode: error instanceof PlanValidationError ? error.code : 'outlook_write_failed',
      }
    }
  }
}
