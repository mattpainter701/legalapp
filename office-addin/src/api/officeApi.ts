import type {
  ActionExecutionResult,
  OfficeActionPlan,
  OfficeContextEnvelope,
} from '../contracts/office'
import { validatePlan } from '../contracts/validation'

export class OfficeApi {
  constructor(private readonly apiBase: string) {}

  private async request(path: string, init: RequestInit): Promise<Response> {
    const response = await fetch(`${this.apiBase}${path}`, {
      ...init,
      credentials: 'include',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        ...init.headers,
      },
    })
    if (!response.ok) {
      const detail = await response.json().catch(() => null) as { detail?: string } | null
      throw new Error(detail?.detail || `Office assistant request failed (${response.status})`)
    }
    return response
  }

  async createPlan(context: OfficeContextEnvelope, instruction: string): Promise<OfficeActionPlan> {
    const response = await this.request('/office/plans', {
      method: 'POST',
      body: JSON.stringify({ context, instruction }),
    })
    const raw = await response.json() as unknown
    return validatePlan(raw, context.surface, context.documentFingerprint)
  }

  async reportResult(result: ActionExecutionResult): Promise<void> {
    await this.request(`/office/plans/${encodeURIComponent(result.planId)}/result`, {
      method: 'POST',
      body: JSON.stringify(result),
    })
  }
}
