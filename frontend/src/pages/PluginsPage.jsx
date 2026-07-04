import React, { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { getPlugins, updatePluginEntitlement } from '../api'
import { useAuth } from '../App'
import {
  Scale, Lock, Landmark, Building2, UserCircle, Rocket, Lightbulb, Bot, ClipboardList, Vault, Handshake,
  ShoppingCart, Sparkles, CircleAlert, Ban, Settings2, BriefcaseBusiness, GitBranch, Workflow, Home, Shield
} from 'lucide-react'


const ADDON_WORKFLOWS = {
  'commercial-legal': {
    generalMatterBridge: 'Uses general matters for ownership, client context, files, dates, and billing while adding contract-specific lifecycle tracking.',
    workflowDepth: [
      'Vendor, NDA, SaaS/MSA, amendment, and stakeholder review workstreams',
      'Renewal, obligation, escalation, and playbook-position tracking',
      'Template-aligned drafting and negotiation checkpoints tied to matter files',
    ],
    review: {
      buyer: 'In-house and firm teams that manage repeat contract volume or need consistent commercial playbook enforcement.',
      matterTieBack: 'Each contract review stays visible as a general matter with the same client, responsible attorney, deadlines, files, communications, time, and invoices.',
      value: 'Reduces missed renewals and inconsistent fallback positions while giving leadership portfolio visibility across active commercial work.',
    },
  },
  'privacy-legal': {
    generalMatterBridge: 'Turns privacy requests into ordinary matters for ownership, client communications, documents, due dates, and reporting while adding privacy-specific registers.',
    workflowDepth: [
      'DPA review, DSAR response, privacy impact assessment, and policy-monitor workflows',
      'Jurisdiction, data category, vendor, processor/controller, and response-deadline tracking',
      'Matter-linked evidence packs for privacy advice, approvals, and remediation follow-up',
    ],
    review: {
      buyer: 'Teams supporting data protection programs, vendor privacy reviews, consumer requests, or recurring privacy assessments.',
      matterTieBack: 'Privacy tasks inherit the same matter activity trail so DSARs, DPAs, and PIAs can be tracked beside non-addon legal work.',
      value: 'Improves response discipline, creates defensible records, and helps teams spot repeat privacy risk across clients or business units.',
    },
  },
  'litigation-legal': {
    generalMatterBridge: 'Keeps disputes in the general matter portfolio while adding litigation-specific phases, parties, claims, deadlines, holds, and evidentiary work product.',
    workflowDepth: [
      'Matter intake, demand response, claim charts, subpoenas, legal holds, and matter updates',
      'Chronologies, deposition preparation, privilege logs, and OC status tracking',
      'Portfolio status and closeout summaries that roll up across active disputes',
    ],
    review: {
      buyer: 'Litigation teams that need dispute operations depth beyond a generic matter record.',
      matterTieBack: 'The dispute remains a normal matter for assignments, documents, calendar, billing, and client updates while the addon captures litigation-specific artifacts.',
      value: 'Creates a stronger command center for deadlines, evidence, risk, and communication across each case lifecycle.',
    },
  },
  'corporate-legal': {
    generalMatterBridge: 'Uses general matters for client and deal ownership while adding corporate governance, diligence, entity, consent, and closing structures.',
    workflowDepth: [
      'M&A diligence, tabular document review, deal-team summaries, and closing checklists',
      'Entity compliance, board minutes, written consents, and governance calendars',
      'Matter-linked signature, approval, deliverable, and post-closing task tracking',
    ],
    review: {
      buyer: 'Corporate, startup, fund, and M&A teams managing entity maintenance or transaction execution.',
      matterTieBack: 'Entity and deal workflows attach to the same matter system used for files, responsible lawyers, client contacts, time, and reporting.',
      value: 'Improves closing discipline, governance hygiene, and reusable transaction knowledge without forcing every user into corporate-only screens.',
    },
  },
  'employment-legal': {
    generalMatterBridge: 'Keeps employment work in My Matters while adding employee, policy, jurisdiction, investigation, and wage-hour context.',
    workflowDepth: [
      'Termination, classification, hiring, investigation, handbook, wage-hour, and expansion reviews',
      'Employee/role facts, jurisdictional posture, policy versions, and HR stakeholder tracking',
      'Matter-linked recommendations, risk levels, and follow-up actions for HR and legal teams',
    ],
    review: {
      buyer: 'Teams supporting HR advisory, workplace investigations, worker classification, handbooks, or multi-state employment questions.',
      matterTieBack: 'The addon enriches a standard matter rather than replacing it, preserving common deadlines, documents, communications, and billing.',
      value: 'Helps teams standardize sensitive people decisions and maintain cleaner records for repeat HR legal work.',
    },
  },
  'product-legal': {
    generalMatterBridge: 'Links product launches and feature reviews to the standard matter portfolio while adding product-risk and go-to-market decision records.',
    workflowDepth: [
      'Launch review, marketing claims checks, feature-risk assessments, and problem triage',
      'Risk category, approval owner, claim support, market, and release-timeline tracking',
      'Actionable launch gates and mitigations that remain connected to matter documents and communications',
    ],
    review: {
      buyer: 'Legal teams embedded with product, marketing, growth, or regulated feature-launch processes.',
      matterTieBack: 'Product questions become trackable matters with the same client/business owner, due dates, files, notes, and reporting as other legal requests.',
      value: 'Speeds launch counseling while preserving an audit trail for claims, approvals, and risk-based release decisions.',
    },
  },
  'ip-legal': {
    generalMatterBridge: 'Keeps IP counseling in general matters while adding IP asset, clearance, enforcement, open-source, and portfolio-specific structure.',
    workflowDepth: [
      'Trademark clearance, FTO, cease-and-desist, takedown, OSS, and infringement triage workflows',
      'Invention intake, clause review, portfolio summaries, asset ownership, and chain-of-title context',
      'Matter-linked enforcement history, clearance findings, and portfolio reporting',
    ],
    review: {
      buyer: 'Teams managing trademarks, patent/FTO questions, copyright enforcement, open-source reviews, or invention intake.',
      matterTieBack: 'Each asset or review can still be assigned, calendared, documented, billed, and reported as a normal matter.',
      value: 'Creates repeatable IP diligence and enforcement records while keeping portfolio decisions connected to client relationships.',
    },
  },
  'ai-governance-legal': {
    generalMatterBridge: 'Routes AI use cases and vendor reviews through general matters while adding governance inventory, impact, and policy controls.',
    workflowDepth: [
      'AI use-case triage, vendor AI review, inventory, impact assessment, and policy starter workflows',
      'Model/use-case purpose, risk tier, data inputs, vendor terms, safeguards, and approval checkpoints',
      'Matter-linked governance decisions, mitigations, policy monitoring, and board-ready summaries',
    ],
    review: {
      buyer: 'Organizations building AI governance programs or reviewing internal AI use cases and vendor AI tools.',
      matterTieBack: 'AI reviews remain visible as legal matters with familiar ownership, due dates, documents, communications, and cross-practice reporting.',
      value: 'Helps legal teams turn fast-moving AI questions into consistent, traceable governance decisions.',
    },
  },
  'regulatory-legal': {
    generalMatterBridge: 'Keeps regulatory work in the same matter list while adding agency, rulemaking, obligation, comment, and monitoring structures.',
    workflowDepth: [
      'Regulatory gap analysis, policy diffing, redlines, comments, NPRM responses, and feed watching',
      'Agency, jurisdiction, obligation, policy owner, deadline, and remediation tracking',
      'Matter-linked watchlists, executive summaries, and implementation follow-up',
    ],
    review: {
      buyer: 'Compliance and regulatory teams watching agencies, rule changes, policies, and remediation obligations.',
      matterTieBack: 'Every regulatory request is still assignable and reportable through general matters, calendar, documents, and client updates.',
      value: 'Improves visibility from regulatory change detection through gap analysis, response drafting, and operational follow-through.',
    },
  },
  'family-law': {
    generalMatterBridge: 'Connects family-law cases back to the firmwide matter list for assignments, deadlines, documents, billing, and client communication.',
    workflowDepth: [
      'Parties, children, custody schedules, support orders, protective orders, and hearing history',
      'Jurisdiction-aware child-support calculations with reproducible worksheets where configured',
      'Payment ledgers and domestic-relations case events that remain tied to matter activity',
    ],
    review: {
      buyer: 'Consumer practices handling divorce, custody, support, and related domestic-relations workflows.',
      matterTieBack: 'The case remains a standard matter for client care and firm reporting while the addon tracks domestic-relations facts and calculations.',
      value: 'Gives family-law teams deeper case structure without taking away shared My Matters visibility for all users.',
    },
  },
  'criminal-defense': {
    generalMatterBridge: 'Uses the common matter record for client intake, assignments, court dates, files, billing, and communications while adding defense-specific case assessment.',
    workflowDepth: [
      'Charge, court, discovery, plea, motion, and hearing tracking for criminal-defense matters',
      'Discovery review and issue spotting connected to case documents and deadlines',
      'Motion drafting and case-assessment notes that stay linked to the client matter',
    ],
    review: {
      buyer: 'Criminal-defense practices that need case-specific discovery and motion workflows on top of general matter management.',
      matterTieBack: 'Defense work shares the same matter timeline, document store, calendar, responsible user, and client communication history as core matters.',
      value: 'Helps teams organize high-volume court and discovery activity while preserving a unified client service record.',
    },
  },
  'real-estate': {
    generalMatterBridge: 'Keeps property matters in the core portfolio while adding property, lease, title, purchase, and closing workflow detail.',
    workflowDepth: [
      'Lease review, purchase agreement review, title review, and closing checklist workflows',
      'Property, counterparty, title exception, contingency, deliverable, and closing-date tracking',
      'Matter-linked deal documents, approvals, issues lists, and closing follow-up',
    ],
    review: {
      buyer: 'Teams handling commercial or residential property transactions, leasing, title review, and closings.',
      matterTieBack: 'Each property transaction remains a normal matter for client contacts, documents, deadlines, billing, and reporting.',
      value: 'Improves closing readiness and issue visibility without fragmenting property work from the broader matter portfolio.',
    },
  },
  'trust-estate-legal': {
    generalMatterBridge: 'Starts from the same client, contacts, documents, calendar, billing, and My Matters foundation every firm already uses.',
    workflowDepth: [
      'Estate portfolio, fiduciary roles, beneficiaries, assets, liabilities, and probate milestones',
      'Accounting summaries, distributions, estate-tax preparation, and court-ready estate reports',
      'Beneficiary communications and probate checklists connected to matter deadlines and files',
    ],
    review: {
      buyer: 'Trust, estate, and probate teams that need fiduciary accounting and beneficiary workflows beyond a generic matter.',
      matterTieBack: 'The estate links back to the general matter system for client relationship management, documents, calendar, billing, and portfolio reporting.',
      value: 'Adds estate administration depth while keeping every estate visible in the firmwide matter-management foundation.',
    },
  },
  'mediation-legal': {
    generalMatterBridge: 'Keeps the neutral case visible beside ordinary matters while preserving separate party workspaces and balanced access.',
    workflowDepth: [
      'Two-sided party intake, mediator case tracking, caucus notes, and session summaries',
      'Asset exchange, document exchange, approvals, and settlement proposal workflows',
      'Portal collaboration and settlement-agreement drafting for participants outside the firm',
    ],
    review: {
      buyer: 'Mediators and dispute-resolution teams that need neutral workflows and participant collaboration beyond standard disputes.',
      matterTieBack: 'The mediation matter remains in the shared portfolio for scheduling, documents, contacts, reporting, and billing while party-specific data stays structured.',
      value: 'Supports balanced mediation administration and settlement momentum without hiding the case from general matter oversight.',
    },
  },
}

function workflowFor(pluginId) {
  return ADDON_WORKFLOWS[pluginId] || {
    generalMatterBridge: 'Extends the core My Matters workspace with niche workflow data while keeping shared contacts, documents, deadlines, and reports in one firmwide system.',
    workflowDepth: [
      'Practice-specific intake and checklists',
      'Specialized drafting, review, or analysis outputs',
      'Matter-linked reporting for teams that buy this add-on',
    ],
    review: {
      buyer: 'Teams with specialized case work that needs more structure than a general matter alone.',
      matterTieBack: 'The addon should enrich—not replace—the shared matter record users already rely on.',
      value: 'Creates practice-specific depth while preserving a single source of truth for clients and matters.',
    },
  }
}

const PLUGIN_ICONS = {
  'commercial-legal': Scale,
  'privacy-legal': Lock,
  'litigation-legal': Landmark,
  'corporate-legal': Building2,
  'employment-legal': UserCircle,
  'product-legal': Rocket,
  'ip-legal': Lightbulb,
  'ai-governance-legal': Bot,
  'regulatory-legal': ClipboardList,
  'trust-estate-legal': Vault,
  'mediation-legal': Handshake,
  'family-law': UserCircle,
  'criminal-defense': Shield,
  'real-estate': Home,
}


// ── State tab definitions ────────────────────────────────────────────────────
const STATE_TABS = [
  { key: 'purchased', label: 'Purchased', icon: ShoppingCart, filter: (p) => p.is_purchased && !p.is_locked && p.entitlement_status !== 'trial' },
  { key: 'trials', label: 'Trials', icon: Sparkles, filter: (p) => p.entitlement_status === 'trial' },
  { key: 'setup-required', label: 'Setup Required', icon: CircleAlert, filter: (p) => (p.is_purchased || p.entitlement_status === 'purchased') && p.setup_status !== 'complete' && !p.profile_is_complete },
  { key: 'available', label: 'Available', icon: ShoppingCart, filter: (p) => !p.is_purchased && !p.is_locked && p.entitlement_status !== 'trial' && !p.is_trial },
  { key: 'locked', label: 'Locked', icon: Ban, filter: (p) => p.is_locked || p.entitlement_status === 'locked' || p.entitlement_status === 'disabled' },
]

function stateFor(plugin) {
  if (plugin.is_locked || plugin.entitlement_status === 'locked' || plugin.entitlement_status === 'disabled') return 'locked'
  if (plugin.entitlement_status === 'trial') return 'trials'
  if ((plugin.is_purchased || plugin.entitlement_status === 'purchased') && plugin.setup_status !== 'complete' && !plugin.profile_is_complete) return 'setup-required'
  if (plugin.is_purchased || plugin.entitlement_status === 'purchased' || plugin.setup_status === 'complete' || plugin.profile_is_complete) return 'purchased'
  return 'available'
}

const STATE_META = {
  purchased:  { badge: 'Active',   badgeCls: 'bg-green-100 text-green-700 border-green-200',   dotCls: 'bg-green-500',  emptyTitle: 'No Purchased Add-ons',    emptyDesc: 'Purchase an add-on from the Available tab to get started.' },
  trials:     { badge: 'Trial',    badgeCls: 'bg-purple-100 text-purple-700 border-purple-200', dotCls: 'bg-purple-500', emptyTitle: 'No Active Trials',        emptyDesc: 'Start a trial from the Available tab to evaluate an add-on.' },
  'setup-required': { badge: 'Setup Required', badgeCls: 'bg-blue-100 text-blue-700 border-blue-200', dotCls: 'bg-blue-500', emptyTitle: 'All Set Up', emptyDesc: 'All purchased add-ons have been configured.' },
  available: { badge: 'Available', badgeCls: 'bg-amber-100 text-amber-700 border-amber-200', dotCls: 'bg-amber-500', emptyTitle: 'All Add-ons Purchased',    emptyDesc: 'Every available add-on is already active or on trial.' },
  locked:    { badge: 'Locked',    badgeCls: 'bg-gray-100 text-gray-600 border-gray-200',     dotCls: 'bg-gray-500',  emptyTitle: 'No Locked Add-ons',        emptyDesc: 'No add-ons have been locked or disabled.' },
}

// ── Plugin card ──────────────────────────────────────────────────────────────
function PluginCard({ plugin, isAdmin, saving, onEntitlement, onNavigate }) {
  const pluginId = plugin.plugin_name || plugin.plugin_id || plugin.id
  const Icon = PLUGIN_ICONS[pluginId] || Settings2
  const state = stateFor(plugin)
  const meta = STATE_META[state]
  const workflow = workflowFor(pluginId)

  return (
    <div
      className="bg-brand-surface border border-brand-line rounded-2xl p-6 flex flex-col hover:shadow-md hover:border-brand-accent hover:-translate-y-1 transition-all duration-200 group cursor-pointer"
      onClick={() => onNavigate(plugin.primary_route || `/plugins/${pluginId}`)}
    >
      {/* Icon + name */}
      <div className="flex items-start gap-4 mb-4">
        <div className="w-12 h-12 rounded-xl bg-brand-bg border border-brand-line flex items-center justify-center text-brand-ink group-hover:bg-brand-ink group-hover:text-brand-surface transition-colors duration-200 shrink-0">
          <Icon size={24} strokeWidth={1.5} />
        </div>
        <div className="flex-1 min-w-0 pt-1">
          <h3 className="font-serif font-bold text-brand-ink text-lg leading-tight mb-2">
            {plugin.display_name || plugin.name || pluginId}
          </h3>
          <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold font-sans uppercase tracking-wider border ${meta.badgeCls}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${meta.dotCls}`} />
            {meta.badge}
          </span>
        </div>
      </div>

      {/* Description */}
      <p className="text-brand-muted text-sm font-sans leading-relaxed flex-1 mb-6">
        {plugin.description}
      </p>

      <div className="mb-5 space-y-3 text-[12px] font-sans text-brand-muted">
        <div>Category: <span className="text-brand-ink font-medium">{plugin.category}</span></div>
        <div>Best for: <span className="text-brand-ink font-medium">{workflow.review.buyer}</span></div>
        <div>Skills reviewed: <span className="text-brand-ink font-medium">{(plugin.skills || []).slice(0, 4).join(', ') || 'practice workflow setup'}</span>{(plugin.skills || []).length > 4 ? ` +${plugin.skills.length - 4} more` : ''}</div>
        <div>Matter types: <span className="text-brand-ink font-medium">{(plugin.matter_types || []).slice(0, 5).join(', ') || 'configured during setup'}</span></div>
        <div>Integrations: <span className="text-brand-ink font-medium">{(plugin.available_integrations || []).join(', ') || 'none connected'}</span></div>
        <div className="rounded-xl border border-brand-line bg-brand-bg-soft p-3">
          <div className="flex items-center gap-1.5 text-brand-ink font-semibold mb-1">
            <GitBranch size={13} /> General matter bridge
          </div>
          <p className="leading-relaxed mb-2">{workflow.generalMatterBridge}</p>
          <p className="leading-relaxed text-brand-ink/80"><span className="font-semibold">Review:</span> {workflow.review.matterTieBack}</p>
        </div>
        <div className="rounded-xl border border-brand-line bg-white p-3">
          <div className="flex items-center gap-1.5 text-brand-ink font-semibold mb-2">
            <Workflow size={13} /> Add-on workflow depth
          </div>
          <ul className="space-y-1 mb-2">
            {workflow.workflowDepth.map(item => <li key={item}>• {item}</li>)}
          </ul>
          <p className="leading-relaxed text-brand-ink/80"><span className="font-semibold">Customer value:</span> {workflow.review.value}</p>
        </div>
      </div>

      {/* Actions */}
      <div className="space-y-2">
        <button className="w-full py-2.5 bg-brand-surface text-brand-ink border border-brand-line text-sm font-sans font-medium rounded-xl group-hover:bg-brand-ink group-hover:text-white transition-colors">
          {plugin.is_purchased || state === 'purchased' ? 'Open Workspace' : 'View Add-on'}
        </button>
        {isAdmin && (
          <div className="grid grid-cols-2 gap-2">
            {!plugin.is_purchased && plugin.entitlement_status !== 'trial' ? (
              <>
                <button
                  onClick={(e) => onEntitlement(e, pluginId, 'trial')}
                  disabled={saving === `${pluginId}:trial`}
                  className="px-3 py-2 text-[12px] font-sans font-semibold rounded-lg border border-brand-line text-brand-ink hover:bg-brand-bg disabled:opacity-50"
                >Trial</button>
                <button
                  onClick={(e) => onEntitlement(e, pluginId, 'purchased')}
                  disabled={saving === `${pluginId}:purchased`}
                  className="px-3 py-2 text-[12px] font-sans font-semibold rounded-lg bg-brand-ink text-white hover:bg-brand-ink-2 disabled:opacity-50"
                >Purchase</button>
              </>
            ) : (
              <>
                <button
                  onClick={(e) => { e.stopPropagation(); onNavigate(`/plugins/${pluginId}`) }}
                  className="px-3 py-2 text-[12px] font-sans font-semibold rounded-lg border border-brand-line text-brand-ink hover:bg-brand-bg"
                >Configure</button>
                <button
                  onClick={(e) => onEntitlement(e, pluginId, 'disabled')}
                  disabled={saving === `${pluginId}:disabled`}
                  className="px-3 py-2 text-[12px] font-sans font-semibold rounded-lg border border-brand-line text-brand-muted hover:text-brand-ink hover:bg-brand-bg disabled:opacity-50"
                >Disable</button>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Page component ───────────────────────────────────────────────────────────
export default function PluginsPage() {
  const navigate = useNavigate()
  const { user } = useAuth()
  const [plugins, setPlugins] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [savingPlugin, setSavingPlugin] = useState(null)
  const [activeTab, setActiveTab] = useState('purchased')

  const loadPlugins = () => {
    setLoading(true)
    return getPlugins()
      .then((data) => {
        const list = Array.isArray(data) ? data : data?.plugins || []
        setPlugins(list)
      })
      .catch((err) => {
        setError('Failed to load plugins.')
        console.error(err)
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => { loadPlugins() }, [])

  const handleEntitlement = async (event, pluginId, status) => {
    event.stopPropagation()
    setSavingPlugin(`${pluginId}:${status}`)
    setError(null)
    try {
      await updatePluginEntitlement(pluginId, { status, source: 'admin' })
      await loadPlugins()
    } catch (err) {
      setError(err?.response?.data?.detail || 'Failed to update plugin entitlement.')
    } finally {
      setSavingPlugin(null)
    }
  }

  // Compute per-tab counts and auto-select first non-empty tab
  const tabCounts = useMemo(() => {
    const counts = {}
    for (const tab of STATE_TABS) {
      counts[tab.key] = plugins.filter(tab.filter).length
    }
    return counts
  }, [plugins])

  // Auto-select first non-empty tab on load if current tab is empty
  useEffect(() => {
    if (!loading && tabCounts[activeTab] === 0) {
      const first = STATE_TABS.find((t) => tabCounts[t.key] > 0)
      if (first) setActiveTab(first.key)
    }
  }, [loading, tabCounts, activeTab])

  const filteredPlugins = useMemo(
    () => plugins.filter(STATE_TABS.find((t) => t.key === activeTab)?.filter || (() => true)),
    [plugins, activeTab]
  )

  const isAdmin = user?.role === 'admin'

  return (
    <div className="min-h-screen bg-brand-bg">
      {/* Top nav */}
      <div className="bg-brand-surface border-b border-brand-line px-6 py-4 flex items-center justify-between sticky top-0 z-30">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate('/chat')}
            className="flex items-center gap-2 text-brand-muted hover:text-brand-ink transition-colors text-sm font-sans font-medium"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
            </svg>
            Back to Chat
          </button>
          <div className="h-4 w-px bg-brand-line"></div>
          <span className="font-serif font-semibold text-lg text-brand-ink">Clarity Legal</span>
        </div>
      </div>

      {/* Page header */}
      <div className="max-w-6xl mx-auto px-6 py-12">
        <div className="mb-8 text-center">
          <h1 className="font-serif text-4xl font-bold text-brand-ink mb-4">
            Add-on Modules
          </h1>
          <p className="text-brand-muted font-sans text-lg max-w-3xl mx-auto">
            Every user gets My Matters, contacts, documents, deadlines, communications, billing, and reporting. Add-ons are optional paid modules for niche case work that need deeper data models, portals, calculations, or practice-specific workflows.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-10">
          <div className="bg-brand-ink text-white rounded-2xl p-5 shadow-sm">
            <BriefcaseBusiness size={20} className="text-brand-amber mb-3" />
            <h2 className="font-serif font-bold mb-2">Core for everyone</h2>
            <p className="text-sm text-white/75 leading-relaxed">General matters remain the source of truth for clients, assignments, dates, files, time, invoices, and CRM activity.</p>
          </div>
          <div className="bg-brand-surface border border-brand-line rounded-2xl p-5 shadow-sm">
            <GitBranch size={20} className="text-brand-accent mb-3" />
            <h2 className="font-serif font-bold text-brand-ink mb-2">Add-ons attach to matters</h2>
            <p className="text-sm text-brand-muted leading-relaxed">Purchased modules add specialized records while still rolling activity back into firmwide matter management.</p>
          </div>
          <div className="bg-brand-surface border border-brand-line rounded-2xl p-5 shadow-sm">
            <Workflow size={20} className="text-brand-accent mb-3" />
            <h2 className="font-serif font-bold text-brand-ink mb-2">Niche workflow depth</h2>
            <p className="text-sm text-brand-muted leading-relaxed">Teams can buy only the estate, mediation, domestic, commercial, or other workflows their practice actually needs.</p>
          </div>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3 mb-8 text-red-700 text-sm font-sans text-center">
            {error}
          </div>
        )}

        {loading ? (
          <div className="flex justify-center py-20">
            <div className="w-8 h-8 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" />
          </div>
        ) : (
          <>
            {/* State tabs */}
            <div className="flex flex-wrap gap-2 mb-10 justify-center">
              {STATE_TABS.map((tab) => {
                const TabIcon = tab.icon
                const count = tabCounts[tab.key]
                const isActive = activeTab === tab.key
                return (
                  <button
                    key={tab.key}
                    onClick={() => setActiveTab(tab.key)}
                    className={`inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-sans font-semibold border transition-all ${
                      isActive
                        ? 'bg-brand-ink text-white border-brand-ink shadow-sm'
                        : 'bg-brand-surface text-brand-muted border-brand-line hover:text-brand-ink hover:border-brand-ink'
                    }`}
                  >
                    <TabIcon size={16} strokeWidth={1.5} />
                    {tab.label}
                    <span className={`inline-flex items-center justify-center min-w-[22px] h-[22px] px-1.5 rounded-full text-[11px] font-bold ${
                      isActive ? 'bg-white/20 text-white' : 'bg-brand-bg text-brand-muted'
                    }`}>
                      {count}
                    </span>
                  </button>
                )
              })}
            </div>

            {/* Plugin grid */}
            {filteredPlugins.length === 0 ? (
              <div className="text-center py-16">
                <div className="w-16 h-16 mx-auto mb-4 rounded-2xl bg-brand-surface border border-brand-line flex items-center justify-center text-brand-muted">
                  {(() => {
                    const tabDef = STATE_TABS.find((t) => t.key === activeTab)
                    const TabIcon = tabDef?.icon || Settings2
                    return <TabIcon size={32} strokeWidth={1.5} />
                  })()}
                </div>
                <h3 className="font-serif text-xl font-semibold text-brand-ink mb-2">
                  {STATE_META[activeTab]?.emptyTitle || 'Nothing here'}
                </h3>
                <p className="text-brand-muted text-sm font-sans max-w-sm mx-auto">
                  {STATE_META[activeTab]?.emptyDesc || ''}
                </p>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                {filteredPlugins.map((plugin) => (
                  <PluginCard
                    key={plugin.plugin_name || plugin.plugin_id || plugin.id}
                    plugin={plugin}
                    isAdmin={isAdmin}
                    saving={savingPlugin}
                    onEntitlement={handleEntitlement}
                    onNavigate={navigate}
                  />
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
