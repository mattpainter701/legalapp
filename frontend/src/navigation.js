import {
  Blocks, BarChart2, CalendarDays, MessageSquare, FileSignature,
  Briefcase, Clock, Receipt, Landmark, CheckSquare, Users, ClipboardList,
  Mail, Shield, ShieldCheck, Rocket, PhoneCall, Search,
} from 'lucide-react'
import { canAccessModuleList } from './moduleAccess'

export const NAV_GROUPS = [
  {
    items: [
      { path: '/matters', label: 'My Matters', icon: Briefcase, primary: true, module: 'matters' },
      { path: '/chat',    label: 'Assistant',  icon: MessageSquare, module: 'chat' },
      { path: '/firm-memory', label: 'Firm Memory', icon: Search, module: 'matters' },
    ],
  },
  {
    label: 'Workspace',
    items: [
      { path: '/calendar',       label: 'Calendar',       icon: CalendarDays, module: 'calendar' },
      { path: '/time-tracking',  label: 'Time Tracking',  icon: Clock, module: 'time-tracking' },
      { path: '/tasks',          label: 'Tasks',          icon: CheckSquare, module: 'tasks' },
      { path: '/communications', label: 'Communications', icon: Mail, module: 'communications' },
      { path: '/clients',        label: 'Clients & CRM',  icon: Users, module: 'contacts' },
      { path: '/conflicts',      label: 'Conflict Search', icon: ShieldCheck, module: 'contacts' },
      { path: '/intake/dashboard', label: 'Call Intake',   icon: PhoneCall, module: 'intake-dashboard' },
      { path: '/intake',         label: 'Intake',         icon: ClipboardList, module: 'intake' },
      { path: '/templates',      label: 'Template Studio', icon: FileSignature, module: 'templates' },
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

export const NAV_ITEMS = NAV_GROUPS.flatMap((group) => group.items)
export const VIEW_ITEMS = NAV_ITEMS.filter((item) => !['/admin', '/onboarding'].includes(item.path))
const receptionist = ['/intake', '/intake/dashboard', '/conflicts', '/clients', '/tasks', '/calendar']
const secretary = [...receptionist, '/matters', '/communications', '/templates']
const paralegal = [...secretary, '/firm-memory', '/chat', '/time-tracking']
const attorney = ['/matters', '/chat', '/firm-memory', '/calendar', '/tasks', '/clients', '/conflicts', '/intake', '/intake/dashboard', '/communications', '/templates', '/time-tracking']
const finance = ['/invoices', '/trust', '/reports', '/time-tracking', '/clients', '/tasks', '/calendar']
export const VIEW_PRESETS = { Receptionist: receptionist, Finance: finance, Secretary: secretary, Paralegal: paralegal, Attorney: attorney, Partner: [...attorney, '/invoices', '/trust', '/reports'] }

export function availableNavigation(user) {
  return NAV_ITEMS.filter((item) => {
    if (!canAccessModuleList(user?.enabled_modules, item.module)) return false
    if (item.path === '/admin') return ['admin', 'accountant'].includes(user?.role)
    if (item.adminOnly) return user?.role === 'admin'
    return !Array.isArray(user?.navigation_paths) || user.navigation_paths.includes(item.path)
  })
}

export function orderedNavigation(items, preferences = {}) {
  const order = preferences?.order || []
  return [...items].sort((a, b) => {
    const rank = (path) => order.includes(path) ? order.indexOf(path) : order.length + items.findIndex((item) => item.path === path)
    return rank(a.path) - rank(b.path)
  })
}

export function visibleNavigation(user) {
  return orderedNavigation(availableNavigation(user), user?.navigation_preferences).filter((item) => (
    ['/admin', '/onboarding'].includes(item.path) || !user?.navigation_preferences?.hidden?.includes(item.path)
  ))
}

export function mobileNavigation(user) {
  const visible = visibleNavigation(user)
  if (user?.navigation_paths == null && !user?.navigation_preferences?.order?.length) {
    return ['/intake/dashboard', '/matters', '/chat', '/calendar', '/tasks']
      .map((path) => visible.find((item) => item.path === path)).filter(Boolean)
      .map((item) => item.path === '/matters' ? { ...item, label: 'Matters' } : item)
  }
  return visible.filter((item) => !['/admin', '/onboarding'].includes(item.path)).slice(0, 5)
}
