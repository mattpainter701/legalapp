/**
 * Public marketing catalog for the LawHand practice-area library.
 *
 * This mirrors the shipped plugin manifest in
 * ``backend/app/services/plugins/manifest.py`` and its prompt catalog in
 * ``backend/app/services/plugins/prompts.py``. Marketing may describe fewer
 * details than the manifest carries, but it must never list a practice area
 * that is not in the manifest, and it must not omit one that is — the home
 * page and the platform page both render from this single list so the two
 * cannot drift apart.
 *
 * Practice areas with a dedicated workspace surface (their own records, roles,
 * and routes) are listed in WORKSPACE_MODULES; the rest are skill libraries
 * that layer onto the shared matter workspace.
 */

/** Practice areas delivered as skill libraries over the shared matter record. */
export const PRACTICE_SKILLS = [
  {
    id: 'commercial', plugin: 'commercial-legal', icon: 'Files', name: 'Commercial Legal',
    description: 'Contract review, NDA triage, SaaS analysis, renewal tracking',
    example: 'Apex Cloud · SaaS agreement', status: '2 items need review', signal: '14 clauses checked',
    artifacts: [['Limitation of liability', 'Attorney review'], ['Data processing addendum', 'Playbook match'], ['Renewal terms', '45-day notice']],
    features: ['Compare clauses to the firm playbook', 'Flag missing terms and material deviations', 'Capture obligations, owners, and renewal dates'],
    language: 'Clause library · fallback language · approval thresholds',
  },
  {
    id: 'privacy', plugin: 'privacy-legal', icon: 'Lock', name: 'Privacy Legal',
    description: 'DPA review, DSAR responses, Privacy Impact Assessments',
    example: 'Atlas Analytics · privacy review', status: 'Response due in 9 days', signal: '6 systems mapped',
    artifacts: [['DPA transfer terms', 'Gap found'], ['DSAR identity check', 'Complete'], ['Processing inventory', '6 systems']],
    features: ['Run DPA checks by jurisdiction and data type', 'Coordinate DSAR identity, search, and response steps', 'Keep PIA evidence and mitigation owners together'],
    language: 'Data subjects · subprocessors · retention · transfer basis',
  },
  {
    id: 'litigation', plugin: 'litigation-legal', icon: 'Landmark', name: 'Litigation Legal',
    description: 'Matter intake, portfolio management, demand letters, claim charts',
    example: 'Rivera v. Northwind · portfolio', status: 'Deadline in 4 days', signal: '8 claims mapped',
    artifacts: [['Demand response', 'Draft ready'], ['Claim chart', '8 of 11 mapped'], ['Discovery cutoff', 'Oct 14']],
    features: ['Move intake facts into a structured matter', 'Link allegations, evidence, and authority', 'Track portfolio posture, dates, and next actions'],
    language: 'Claims · defenses · elements · evidence · deadlines',
  },
  {
    id: 'corporate', plugin: 'corporate-legal', icon: 'Building2', name: 'Corporate Legal',
    description: 'M&A diligence, closing checklists, entity compliance',
    example: 'Project Juniper · acquisition', status: '78% closing ready', signal: '42 documents reviewed',
    artifacts: [['Material contracts', '3 exceptions'], ['Closing checklist', '31 of 40'], ['Entity records', 'Current']],
    features: ['Organize diligence findings by workstream', 'Turn issues into owners and closing conditions', 'Maintain entity records and recurring compliance'],
    language: 'Diligence · disclosures · conditions · consents · filings',
  },
  {
    id: 'employment', plugin: 'employment-legal', icon: 'UserCircle', name: 'Employment Legal',
    description: 'Hire/termination review, worker classification, leave tracking',
    example: 'Workforce request · California', status: 'Classification review', signal: '3 decision checks',
    artifacts: [['Role classification', 'Needs facts'], ['Termination packet', 'Review ready'], ['Leave timeline', '12 weeks']],
    features: ['Route hire and separation facts through consistent checks', 'Document classification factors and decisions', 'Track leave events, notices, and return dates'],
    language: 'Worker status · protected leave · notice · final pay',
  },
  {
    id: 'product', plugin: 'product-legal', icon: 'Rocket', name: 'Product Legal',
    description: 'Launch reviews, marketing claims check, regulatory triage',
    example: 'Pulse AI · launch review', status: '2 launch blockers', signal: '5 teams aligned',
    artifacts: [['Marketing claims', '2 need support'], ['Terms update', 'Approved'], ['Launch gate', 'Conditional']],
    features: ['Collect one launch brief across product teams', 'Connect claims to substantiation and approvals', 'Route regulatory questions before release'],
    language: 'Claims · audience · data use · disclosures · launch gate',
  },
  {
    id: 'ip', plugin: 'ip-legal', icon: 'Lightbulb', name: 'IP Legal',
    description: 'Trademark clearance, freedom-to-operate, C&D letters',
    example: 'Northstar · clearance search', status: 'Moderate conflict risk', signal: '27 records screened',
    artifacts: [['Exact mark search', 'No match'], ['Similar marks', '4 for review'], ['Class coverage', '3 classes']],
    features: ['Keep search strategy and results reviewable', 'Compare marks, classes, owners, and status', 'Move findings into advice or enforcement drafts'],
    language: 'Similarity · classes · use evidence · claim scope',
  },
  {
    id: 'ai-governance', plugin: 'ai-governance-legal', icon: 'Bot', name: 'AI Governance',
    description: 'AI use-case triage, impact assessments, vendor AI review',
    example: 'Support copilot · use-case review', status: 'Human oversight required', signal: 'Risk tier · medium',
    artifacts: [['Data inputs', 'Restricted data'], ['Vendor controls', '1 gap'], ['Impact review', 'In progress']],
    features: ['Triage use cases by people, data, and decision impact', 'Standardize impact assessments and control owners', 'Review vendor AI terms alongside technical claims'],
    language: 'Use case · model role · oversight · testing · monitoring',
  },
  {
    id: 'regulatory', plugin: 'regulatory-legal', icon: 'ClipboardList', name: 'Regulatory Legal',
    description: 'Regulatory monitoring, policy gap analysis, NPRM comments',
    example: 'Consumer rules · monitoring file', status: 'Comment window open', signal: '12 obligations tagged',
    artifacts: [['Rule change', 'Material'], ['Policy mapping', '3 gaps'], ['Comment draft', 'Due Sep 8']],
    features: ['Turn regulatory developments into scoped impact reviews', 'Map requirements to policies, controls, and owners', 'Build comment records from evidence and stakeholder input'],
    language: 'Authority · obligation · applicability · policy · comment',
  },
  {
    id: 'real-estate', plugin: 'real-estate', icon: 'Home', name: 'Real Estate',
    description: 'Lease review, purchase agreements, title review, closing workflows',
    example: 'Cedar Point · commercial lease', status: 'Title exception open', signal: '9 provisions checked',
    artifacts: [['Lease review', '3 landlord-favorable'], ['Title exceptions', '1 unresolved'], ['Closing checklist', '12 of 18']],
    features: ['Review lease and purchase terms against firm positions', 'Surface title exceptions and required cures', 'Track closing conditions, dates, and responsible parties'],
    language: 'Premises · term · title · encumbrances · closing conditions',
  },
  {
    id: 'criminal-defense', plugin: 'criminal-defense', icon: 'Scale', name: 'Criminal Defense',
    description: 'Case assessment, discovery review, motion drafting',
    example: 'State v. Whitfield · assessment', status: 'Suppression issue flagged', signal: '340 discovery pages',
    artifacts: [['Charge assessment', 'Elements mapped'], ['Discovery review', '2 gaps noted'], ['Motion to suppress', 'Draft ready']],
    features: ['Map charges to elements and available defenses', 'Work through discovery productions and note what is missing', 'Prepare motion drafts for attorney revision and filing'],
    language: 'Charges · elements · discovery · suppression · plea posture',
  },
]

/**
 * Practice areas that ship a dedicated workspace: their own records, roles,
 * and route inside the product, not only a skill library.
 */
export const WORKSPACE_MODULES = [
  {
    id: 'estate',
    plugin: 'trust-estate-legal',
    icon: 'Vault',
    name: 'Trust & Estate management',
    description: 'Estate portfolios with role-aware access for trustees, grantors, and beneficiaries — asset tracking, tax analysis, and probate records organized for review.',
    example: 'Hamilton Family Estate',
    status: 'Attorney review',
    roles: ['Attorney · full review', 'Trustee · update assets', 'Beneficiary · view approved'],
    metrics: [['24', 'Assets'], ['$4.8m', 'Gross estate'], ['3', 'Review items']],
    activity: [['Residence valuation', 'Reviewed'], ['Family trust allocation', 'Open'], ['Probate inventory', 'Draft']],
    features: ['Asset and liability inventory', 'Tax and probate checkpoints', 'Beneficiary-ready reporting'],
  },
  {
    id: 'domestic',
    plugin: 'family-law',
    icon: 'Users',
    name: 'Family & domestic relations',
    description: 'A domestic relations workspace for parties, children, custody arrangements, support orders, and payment history — with reproducible child support worksheets.',
    example: 'In re Marriage of Whitaker',
    status: 'Worksheet review',
    roles: ['Attorney · full edit', 'Paralegal · update records', 'Client · share inputs'],
    metrics: [['2', 'Children'], ['4', 'Upcoming dates'], ['1', 'Support order']],
    activity: [['Support worksheet', 'Recalculated'], ['Parenting schedule', 'Draft'], ['Response deadline', 'Sep 12']],
    features: [
      'Parties, children, and custody arrangements on one case record',
      'Child support worksheets with saved inputs and reproducible results (North Dakota and Texas guidelines)',
      'Support orders, payment ledger, deadlines, and case events',
    ],
  },
  {
    id: 'mediation',
    plugin: 'mediation-legal',
    icon: 'Handshake',
    name: 'Mediation management',
    description: 'A neutral two-party workspace — intake, briefs, settlement drafting, and case tracking with balanced access for each side.',
    example: 'Rivera v. Northwind',
    status: 'Proposal pending',
    roles: ['Mediator · neutral view', 'Party A · private workspace', 'Party B · private workspace'],
    metrics: [['6', 'Open issues'], ['2', 'Proposals'], ['Sep 18', 'Next session']],
    activity: [['Party A brief', 'Private'], ['Damages range', 'Shared'], ['Draft settlement terms', 'Waiting']],
    features: ['Separate private submissions', 'Shared issue and proposal tracking', 'Settlement drafting and approvals'],
  },
]
