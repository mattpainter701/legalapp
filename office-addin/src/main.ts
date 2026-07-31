import './styles.css'
import { OfficeApi } from './api/officeApi'
import { OfficeSession } from './auth/officeSession'
import type {
  ActionExecutionResult,
  ActionPreview,
  HostAdapter,
  OfficeActionPlan,
  OfficeContextEnvelope,
} from './contracts/office'
import { createAdapter } from './hosts/createAdapter'

const API_BASE = (import.meta.env.VITE_API_BASE || '/api').replace(/\/$/, '')
const api = new OfficeApi(API_BASE)
const session = new OfficeSession({
  apiBase: API_BASE,
  clientId: import.meta.env.VITE_OFFICE_ENTRA_CLIENT_ID,
  authority: import.meta.env.VITE_OFFICE_ENTRA_AUTHORITY || 'https://login.microsoftonline.com/common',
  apiScope: import.meta.env.VITE_OFFICE_API_SCOPE,
})

let adapter: HostAdapter | null = null
let capturedContext: OfficeContextEnvelope | null = null
let currentPlan: OfficeActionPlan | null = null
let authenticated = false

function element<T extends HTMLElement>(id: string): T {
  const value = document.getElementById(id)
  if (!value) throw new Error(`Missing UI element #${id}`)
  return value as T
}

function setStatus(message: string, error = false): void {
  const status = element<HTMLParagraphElement>('status')
  status.textContent = message
  status.classList.toggle('error', error)
}

function setHidden(id: string, hidden: boolean): void {
  element(id).classList.toggle('hidden', hidden)
}

function refreshProposeState(): void {
  const instruction = element<HTMLTextAreaElement>('instruction').value.trim()
  element<HTMLButtonElement>('propose').disabled = !authenticated || !capturedContext || !instruction
}

function contextSummary(context: OfficeContextEnvelope): { title: string; summary: string } {
  if (context.surface === 'word') {
    return {
      title: `${context.selection.charCount.toLocaleString()} selected characters`,
      summary: context.selection.charCount
        ? 'Only the selected Word text will be sent with your instruction.'
        : 'Select Word text before requesting a document change.',
    }
  }
  if (context.surface === 'excel') {
    return {
      title: context.selection.address,
      summary: `${context.selection.rowCount.toLocaleString()} rows × ${context.selection.columnCount.toLocaleString()} columns, including displayed values and formulas.`,
    }
  }
  return {
    title: context.selection.mode === 'compose' ? 'Current Outlook draft' : 'Current Outlook message',
    summary: `${context.selection.bodyText.length.toLocaleString()} body characters. Outlook may return the full thread for some reply views.`,
  }
}

function renderPreview(preview: ActionPreview, plan: OfficeActionPlan): void {
  element('plan-summary').textContent = plan.summary
  const warnings = element<HTMLUListElement>('plan-warnings')
  warnings.replaceChildren(...plan.warnings.map((warning) => {
    const item = document.createElement('li')
    item.textContent = warning
    return item
  }))
  const changes = element<HTMLDivElement>('changes')
  changes.replaceChildren(...preview.entries.flatMap((entry) => {
    const before = document.createElement('div')
    before.className = 'change before'
    const beforeLabel = document.createElement('strong')
    beforeLabel.textContent = `${entry.label} — before`
    before.append(beforeLabel, document.createTextNode(entry.before || '(no content)'))

    const after = document.createElement('div')
    after.className = 'change after'
    const afterLabel = document.createElement('strong')
    afterLabel.textContent = `${entry.label} — after`
    after.append(afterLabel, document.createTextNode(entry.after))
    return [before, after]
  }))
  setHidden('preview', false)
}

async function report(result: ActionExecutionResult): Promise<void> {
  try {
    await api.reportResult(result)
  } catch {
    // The local result remains authoritative for the active Office item. Audit
    // delivery failure is surfaced without retrying the document mutation.
    setStatus('The Office change completed, but WellPled could not record its audit result.', true)
  }
}

async function capture(): Promise<void> {
  if (!adapter) return
  setStatus('Reading the current Office context…')
  capturedContext = await adapter.captureContext()
  currentPlan = null
  setHidden('preview', true)
  const summary = contextSummary(capturedContext)
  element('context-title').textContent = summary.title
  element('context-summary').textContent = summary.summary
  refreshProposeState()
  setStatus('Context captured. Review the scope above before asking WellPled.')
}

async function propose(event: SubmitEvent): Promise<void> {
  event.preventDefault()
  if (!adapter || !capturedContext || !authenticated) return
  const instruction = element<HTMLTextAreaElement>('instruction').value.trim()
  if (!instruction) return
  setStatus('Preparing a bounded change plan…')
  element<HTMLButtonElement>('propose').disabled = true
  try {
    currentPlan = await api.createPlan(capturedContext, instruction)
    const preview = await adapter.preview(currentPlan)
    renderPreview(preview, currentPlan)
    setStatus('Review the before-and-after preview. The Office item is unchanged.')
  } finally {
    refreshProposeState()
  }
}

async function apply(): Promise<void> {
  if (!adapter || !currentPlan) return
  element<HTMLButtonElement>('apply').disabled = true
  setStatus('Checking the current selection and applying the approved change…')
  const result = await adapter.execute(currentPlan)
  await report(result)
  if (result.status === 'applied') {
    setStatus('Approved change applied.')
    setHidden('preview', true)
    currentPlan = null
    await capture()
  } else if (result.status === 'stale') {
    setStatus('The Office content changed. Capture it again before applying a new plan.', true)
  } else {
    setStatus(`The change was not applied (${result.errorCode || 'unknown error'}).`, true)
  }
  element<HTMLButtonElement>('apply').disabled = false
}

async function reject(): Promise<void> {
  if (!currentPlan) return
  const result: ActionExecutionResult = {
    planId: currentPlan.planId,
    status: 'rejected',
    actionCount: 0,
  }
  currentPlan = null
  setHidden('preview', true)
  setStatus('Proposed change rejected. The Office item was not modified.')
  await report(result)
}

function wireEvents(): void {
  element<HTMLButtonElement>('capture').addEventListener('click', () => void capture().catch((error: unknown) => setStatus(error instanceof Error ? error.message : 'Context capture failed', true)))
  element<HTMLTextAreaElement>('instruction').addEventListener('input', refreshProposeState)
  element<HTMLFormElement>('request-form').addEventListener('submit', (event) => void propose(event).catch((error: unknown) => setStatus(error instanceof Error ? error.message : 'Plan request failed', true)))
  element<HTMLButtonElement>('apply').addEventListener('click', () => void apply().catch((error: unknown) => setStatus(error instanceof Error ? error.message : 'Apply failed', true)))
  element<HTMLButtonElement>('reject').addEventListener('click', () => void reject())
}

Office.onReady(async (info) => {
  adapter = createAdapter(info.host)
  if (!adapter) {
    element('unsupported-detail').textContent = `WellPled cannot work in ${info.host || 'this Office application'}. Open it in Word, Excel, or Outlook.`
    setHidden('unsupported', false)
    return
  }

  const capabilities = await adapter.capabilities()
  element('host-label').textContent = `${capabilities.surface[0]?.toUpperCase()}${capabilities.surface.slice(1)} assistant`
  setHidden('workspace', false)
  wireEvents()

  try {
    setStatus('Checking your WellPled session…')
    const user = await session.ensure(capabilities.requirementSets.NestedAppAuth_1_1 === true)
    authenticated = true
    setStatus(user.full_name ? `Signed in as ${user.full_name}.` : 'Signed in to WellPled.')
  } catch (error) {
    authenticated = false
    setStatus(error instanceof Error ? error.message : 'Office sign-in failed', true)
  }
  refreshProposeState()
})
