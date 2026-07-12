import React, { useEffect, useRef, useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import {
  Blocks, Scale, X, BarChart2, CalendarDays, MessageSquare, FileSignature,
  Briefcase, Clock, Receipt, User, Landmark, CheckSquare, Users, ClipboardList,
  Mail, Shield, Rocket, PhoneCall, Lock,
} from 'lucide-react'
import UpgradeModal from './UpgradeModal'
import { canAccessModuleList } from '../moduleAccess'

const NAV_GROUPS = [
  {
    items: [
      { path: '/matters', label: 'My Matters', icon: Briefcase, primary: true, module: 'matters' },
      { path: '/chat',    label: 'Assistant',  icon: MessageSquare, module: 'chat' },
    ],
  },
  {
    label: 'Workspace',
    items: [
      { path: '/calendar',       label: 'Calendar',       icon: CalendarDays, module: 'calendar' },
      { path: '/time-tracking',  label: 'Time Tracking',  icon: Clock, module: 'time-tracking' },
      { path: '/tasks',          label: 'Tasks',          icon: CheckSquare, module: 'tasks' },
      { path: '/communications', label: 'Communications', icon: Mail, module: 'communications' },
      { path: '/contacts',       label: 'Contacts',       icon: Users, module: 'contacts' },
      { path: '/intake/dashboard', label: 'Call Intake',   icon: PhoneCall, module: 'intake-dashboard' },
      { path: '/intake',         label: 'Intake',         icon: ClipboardList, module: 'intake' },
      { path: '/templates',      label: 'Document Automation', icon: FileSignature, module: 'templates' },
    ],
  },
  {
    label: 'Accounting',
    items: [
      { path: '/invoices',      label: 'Invoices',         icon: Receipt, module: 'invoices' },
      { path: '/trust',         label: 'Trust Accounting', icon: Landmark, module: 'trust' },
      { path: '/reports',       label: 'Reports',          icon: BarChart2, module: 'reports' },
    ],
  },
  {
    label: 'Firm',
    items: [
      { path: '/plugins', label: 'Add-on Modules', icon: Blocks, module: 'plugins' },
    ],
  },
  {
    label: 'Administration',
    financeOnly: true,
    items: [
      { path: '/admin',      label: 'Administration', icon: Shield, module: 'admin' },
      { path: '/onboarding', label: 'Onboarding',     icon: Rocket, module: 'onboarding', adminOnly: true },
    ],
  },
]

export default function Sidebar({
  user,
  onLogout,
  isOpen = true,
  onClose,
}) {
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const [upgradeOpen, setUpgradeOpen] = useState(false)
  const isLimited = Boolean(user?.upsell_target)
  const panelRef = useRef(null)
  const closeRef = useRef(null)
  const previousFocusRef = useRef(null)
  const onCloseRef = useRef(onClose)
  const upgradeOpenRef = useRef(upgradeOpen)
  const [isMobile, setIsMobile] = useState(() => window.matchMedia?.('(max-width: 767px)').matches || false)
  upgradeOpenRef.current = upgradeOpen

  useEffect(() => {
    onCloseRef.current = onClose
  }, [onClose])

  useEffect(() => {
    const media = window.matchMedia?.('(max-width: 767px)')
    const update = () => setIsMobile(Boolean(media?.matches))
    update()
    media?.addEventListener?.('change', update)
    return () => media?.removeEventListener?.('change', update)
  }, [])

  useEffect(() => {
    if (!isMobile || !isOpen) return undefined
    previousFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    queueMicrotask(() => closeRef.current?.focus())

    const handleKeyDown = (event) => {
      // UpgradeModal owns focus and Escape while it is layered over the drawer.
      if (upgradeOpenRef.current) return
      if (event.key === 'Escape') {
        event.preventDefault()
        onCloseRef.current?.()
        return
      }
      if (event.key !== 'Tab') return
      const focusable = Array.from(panelRef.current?.querySelectorAll(
        'button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'
      ) || [])
      if (!focusable.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      queueMicrotask(() => previousFocusRef.current?.focus())
    }
  }, [isMobile, isOpen])


  const isActive = (path) => {
    if (path === '/intake') return pathname === '/intake'
    return pathname === path || pathname.startsWith(path + '/')
  }

  const handleNavAndClose = (path) => {
    navigate(path)
    onClose?.()
  }

  const hasFinanceAccess = user?.role === 'admin' || user?.role === 'accountant'
  const visibleGroups = NAV_GROUPS.filter(
    (g) => (!g.adminOnly || user?.role === 'admin') && (!g.financeOnly || hasFinanceAccess)
  ).map((group) => {
    const enabled = user?.enabled_modules
    const items = group.items.map((item) => {
      if (item.adminOnly && user?.role !== 'admin') return null
      const moduleOk = canAccessModuleList(enabled, item.module)
      if (moduleOk) return item
      return null
    }).filter(Boolean)
    return { ...group, items }
  }).filter((group) => group.items.length > 0)

  return (
    <>
      {/* Mobile backdrop */}
      <div
        className={`fixed inset-0 bg-black/40 z-30 md:hidden transition-opacity duration-300 ${isOpen ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none'}`}
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Sidebar panel */}
      <div
        ref={panelRef}
        role={isMobile && isOpen && !upgradeOpen ? 'dialog' : undefined}
        aria-modal={isMobile && isOpen && !upgradeOpen ? 'true' : undefined}
        aria-label={isMobile && isOpen && !upgradeOpen ? 'Workspace navigation' : undefined}
        {...(isMobile && (!isOpen || upgradeOpen) ? { inert: '', 'aria-hidden': true } : {})}
        className={`
        fixed md:relative inset-y-0 left-0 z-40
        w-[300px] flex-shrink-0 border-r border-brand-line flex flex-col h-full bg-brand-surface-2
        transition-transform duration-300 ease-in-out
        ${isOpen ? 'sidebar-visible' : 'sidebar-hidden md:translate-x-0'}
      `}>
        {/* Header */}
        <div className="h-16 flex items-center px-4 border-b border-brand-line shrink-0">
          <Scale className="w-5 h-5 mr-2 text-brand-accent" strokeWidth={1.5} />
          <span className="font-serif font-semibold text-lg tracking-tight text-brand-ink flex-1">Clarity Legal</span>
          <button
            className="md:hidden p-1.5 text-brand-muted hover:text-brand-ink transition-colors tap-target"
            ref={closeRef}
            onClick={onClose}
            aria-label="Close sidebar"
          >
            <X size={18} />
          </button>
        </div>

        {/* Navigation */}
        <nav className="flex-1 overflow-y-auto p-3">
          {visibleGroups.map((group, gi) => (
            <div key={group.label || `group-${gi}`} className={gi > 0 ? 'mt-1' : ''}>
              {group.label && (
                <div className="px-3 pt-4 pb-1 text-[10px] font-semibold uppercase tracking-wider text-brand-muted">
                  {group.label}
                </div>
              )}
              <div className="flex flex-col gap-0.5">
                {group.items.map(({ path, label, icon: Icon, primary }) => {
                  const active = isActive(path)
                  if (primary) {
                    return (
                      <button
                        key={path}
                        onClick={() => handleNavAndClose(path)}
                        className={`sidebar-item w-full rounded text-white ${active ? 'bg-brand-ink-2' : 'bg-brand-ink hover:bg-brand-ink-2'}`}
                      >
                        <Icon className="w-4 h-4" /> {label}
                      </button>
                    )
                  }
                  return (
                    <button
                      key={path}
                      onClick={() => handleNavAndClose(path)}
                      className={`sidebar-item w-full rounded ${active ? 'sidebar-item-active' : 'sidebar-item-inactive'}`}
                    >
                      <Icon className="w-4 h-4" /> {label}
                    </button>
                  )
                })}
              </div>
            </div>
          ))}
          {isLimited && (
            <button
              type="button"
              onClick={() => setUpgradeOpen(true)}
              className="mt-5 flex w-full items-center gap-2 rounded-lg border border-brand-line bg-brand-bg-soft px-3 py-2.5 text-left text-xs font-semibold text-brand-ink hover:border-brand-accent"
            >
              <Lock className="h-3.5 w-3.5 text-brand-accent" />
              <span>Explore the full platform</span>
            </button>
          )}
        </nav>

        {/* Footer / User info */}
        <div className="p-4 border-t border-brand-line flex items-center gap-3 bg-brand-surface-2 shrink-0">
          <div className="w-8 h-8 bg-brand-ink text-brand-bg flex items-center justify-center font-serif text-sm font-semibold shrink-0">
            {user?.full_name?.[0]?.toUpperCase() || user?.email?.[0]?.toUpperCase() || 'U'}
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-brand-ink truncate">
              {user?.full_name || user?.email}
            </p>
            <p className="text-xs text-brand-muted font-mono uppercase tracking-wider truncate">
              {user?.billing_tier || 'Free Tier'}
            </p>
          </div>
          <button
            onClick={() => handleNavAndClose('/profile')}
            className="text-brand-muted hover:text-brand-ink transition-colors shrink-0"
            title="Profile"
          >
            <User className="w-4 h-4" />
          </button>
          <button
            onClick={onLogout}
            className="text-brand-muted hover:text-brand-rose transition-colors shrink-0 ml-2"
            title="Sign out"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
              <polyline points="16 17 21 12 16 7" />
              <line x1="21" y1="12" x2="9" y2="12" />
            </svg>
          </button>
        </div>
      </div>{/* end sidebar panel */}

      <UpgradeModal open={upgradeOpen} onClose={() => setUpgradeOpen(false)} />
    </>
  )
}
