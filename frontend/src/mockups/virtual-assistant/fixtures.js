export const DEMO_DATE = '2026-08-04'

export const DEMO_PROMPTS = [
  {
    id: 'intake',
    label: 'New client + task',
    shortLabel: 'Client + task',
    prompt: 'new client jane doe, 701-123-2255 create task to schedule consultation',
    description: 'Prepare a prospective-client record and a scheduling task.',
  },
  {
    id: 'time',
    label: 'Log matter time',
    shortLabel: 'Log 3 hours',
    prompt: "add 3hrs to joel's case, for reviewing documents and planning case ruling",
    description: 'Resolve the exact matter, billing details, and time entry.',
  },
  {
    id: 'documents',
    label: 'Prepare document packet',
    shortLabel: 'Prep documents',
    prompt: 'prep documents for pleadings, and xyz standard fee, contracts',
    description: 'Scope three private drafts without filing or sending anything.',
  },
]

export const MATTER_OPTIONS = [
  {
    id: 'ramirez',
    name: 'State v. Joel Ramirez',
    caseNumber: '2026-CR-0187',
    client: 'Joel Ramirez',
    rate: 275,
  },
  {
    id: 'peterson',
    name: 'Peterson Estate Administration',
    caseNumber: 'PR-2026-0042',
    client: 'Joel Peterson',
    rate: 240,
  },
  {
    id: 'acme',
    name: 'Acme Purchase Agreement',
    caseNumber: 'MAT-2026-0118',
    client: 'Acme North LLC',
    rate: 325,
  },
]

export const PLEADING_TYPES = ['Complaint', 'Answer', 'Motion', 'Proposed order']

export const CONTRACT_TYPES = [
  'Services agreement',
  'Settlement agreement',
  'NDA',
  'Purchase agreement',
]

export function detectScenario(prompt) {
  const normalized = String(prompt || '').toLowerCase()
  if (/new client|jane doe|schedule consultation/.test(normalized)) return 'intake'
  if (/\b3\s*(hr|hrs|hour|hours)\b|joel'?s case|reviewing documents/.test(normalized)) return 'time'
  if (/pleading|standard fee|contract|prep documents/.test(normalized)) return 'documents'
  return null
}

export function createWorkflow(kind, id, sourcePrompt) {
  if (kind === 'intake') {
    return {
      id,
      kind,
      sourcePrompt,
      runtimeStatus: 'draft',
      expanded: true,
      contactType: 'prospect',
      assignee: 'me',
      due: 'none',
      priority: 'medium',
    }
  }

  if (kind === 'time') {
    return {
      id,
      kind,
      sourcePrompt,
      runtimeStatus: 'draft',
      expanded: true,
      matterId: '',
      date: DEMO_DATE,
      billable: null,
      hours: 3,
      description: 'Reviewed documents and planned case strategy regarding the ruling',
    }
  }

  return {
    id,
    kind: 'documents',
    sourcePrompt,
    runtimeStatus: 'draft',
    expanded: true,
    matterId: '',
    pleadingType: '',
    contractType: '',
    feeTemplate: 'XYZ Standard Hourly Fee Agreement v4',
  }
}

export function getWorkflowReadiness(workflow) {
  if (workflow.runtimeStatus === 'completed') {
    return { status: 'completed', label: 'Completed', missing: [] }
  }
  if (workflow.runtimeStatus === 'running') {
    return { status: 'running', label: 'Working', missing: [] }
  }

  const missing = []
  if (workflow.kind === 'time') {
    if (!workflow.matterId) missing.push('exact matter')
    if (workflow.billable == null) missing.push('billing status')
  }
  if (workflow.kind === 'documents') {
    if (!workflow.matterId) missing.push('matter')
    if (!workflow.pleadingType) missing.push('pleading type')
    if (!workflow.contractType) missing.push('contract type')
  }

  return missing.length
    ? { status: 'needs_input', label: `Needs ${missing.length} answer${missing.length === 1 ? '' : 's'}`, missing }
    : { status: 'ready', label: 'Ready to review', missing: [] }
}

export function getMatter(matterId) {
  return MATTER_OPTIONS.find((matter) => matter.id === matterId) || null
}

export function getWorkflowCopy(workflow) {
  if (workflow.kind === 'intake') {
    return {
      title: 'New client intake',
      collapsedTarget: 'Jane Doe · •••-•••-2255',
      reviewTitle: 'Review 2 changes',
      reviewIntro: 'Nothing is created until you use the button below.',
      actionLabel: 'Create contact & task',
      runningLabel: 'Creating contact & task…',
      receiptTitle: 'Client and task created',
      receiptDetail: 'Jane Doe is in Contacts. The consultation-scheduling task is assigned to you.',
    }
  }

  if (workflow.kind === 'time') {
    const matter = getMatter(workflow.matterId)
    const amount = matter ? workflow.hours * matter.rate : null
    return {
      title: 'Log matter time',
      collapsedTarget: matter ? `${matter.name} · ${workflow.hours.toFixed(2)}h` : `Joel's case · ${workflow.hours.toFixed(2)}h`,
      reviewTitle: 'Review time entry',
      reviewIntro: 'This saves unbilled time. It does not create or send an invoice.',
      actionLabel: `Log ${workflow.hours.toFixed(2)} hours`,
      runningLabel: 'Saving time entry…',
      receiptTitle: `${workflow.hours.toFixed(2)} hours logged`,
      receiptDetail: matter
        ? `${matter.name} · ${workflow.billable ? 'Billable' : 'Non-billable'}${amount != null ? ` · $${amount.toLocaleString('en-US', { minimumFractionDigits: 2 })}` : ''}`
        : 'Time entry saved',
    }
  }

  const matter = getMatter(workflow.matterId)
  return {
    title: 'Prepare document packet',
    collapsedTarget: matter ? `${matter.name} · 3 drafts` : 'Matter required · 3 drafts',
    reviewTitle: 'Review 3 private drafts',
    reviewIntro: 'This prepares drafts only. Nothing will be filed, sent, published, or signed.',
    actionLabel: 'Create 3 drafts',
    runningLabel: 'Preparing 3 drafts…',
    receiptTitle: '3 private drafts prepared',
    receiptDetail: matter ? `${matter.name} · Pleading, fee agreement, and contract` : 'Document packet prepared',
  }
}
