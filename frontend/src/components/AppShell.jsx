import React, { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../App'
import Sidebar from './Sidebar'
import { getConversations, createConversation, deleteConversation, getDocuments, uploadDocument, deleteDocument, logout } from '../api'
import { canAccessModuleList } from '../moduleAccess'
import { useConfirm } from './dialog/ConfirmProvider'
import { Briefcase, CalendarDays, CheckSquare, GripVertical, Menu, MessageSquare, PhoneCall, Shield } from 'lucide-react'

const AppShellContext = createContext(null)

const SIDEBAR_SNAP_POINTS = [240, 288, 344]
const DEFAULT_SIDEBAR_WIDTH = SIDEBAR_SNAP_POINTS[1]
const SIDEBAR_WIDTH_KEY = 'clarity.workspace.sidebar-width'
const SIDEBAR_COLLAPSED_KEY = 'clarity.workspace.sidebar-collapsed'

function nearestSidebarSnap(width) {
  return SIDEBAR_SNAP_POINTS.reduce((closest, point) => (
    Math.abs(point - width) < Math.abs(closest - width) ? point : closest
  ), DEFAULT_SIDEBAR_WIDTH)
}

function readStoredSidebarWidth() {
  if (typeof window === 'undefined') return DEFAULT_SIDEBAR_WIDTH
  try {
    const storedValue = window.localStorage.getItem(SIDEBAR_WIDTH_KEY)
    if (storedValue == null || storedValue === '') return DEFAULT_SIDEBAR_WIDTH
    const stored = Number(storedValue)
    return Number.isFinite(stored) ? nearestSidebarSnap(stored) : DEFAULT_SIDEBAR_WIDTH
  } catch {
    return DEFAULT_SIDEBAR_WIDTH
  }
}

function readStoredSidebarCollapsed() {
  if (typeof window === 'undefined') return false
  try {
    return window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === 'true'
  } catch {
    return false
  }
}

const MOBILE_NAV_ITEMS = [
  { path: '/intake/dashboard', label: 'Call Intake', icon: PhoneCall, module: 'intake-dashboard' },
  { path: '/matters', label: 'Matters', icon: Briefcase },
  { path: '/chat', label: 'Assistant', icon: MessageSquare },
  { path: '/calendar', label: 'Calendar', icon: CalendarDays },
  { path: '/tasks', label: 'Tasks', icon: CheckSquare },
]

export function useAppShell() {
  const ctx = useContext(AppShellContext)
  if (!ctx) throw new Error('useAppShell must be used within AppShellProvider')
  return ctx
}

export default function AppShell({ children, title }) {
  const { user, logout: authLogout } = useAuth()
  const confirmAction = useConfirm()
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [desktopSidebarWidth, setDesktopSidebarWidth] = useState(readStoredSidebarWidth)
  const [desktopSidebarCollapsed, setDesktopSidebarCollapsed] = useState(readStoredSidebarCollapsed)
  const [isResizingSidebar, setIsResizingSidebar] = useState(false)
  const [conversations, setConversations] = useState([])
  const [documents, setDocuments] = useState([])
  const [activeConvId, setActiveConvId] = useState(null)
  const resizeStateRef = useRef(null)
  const sidebarWidthRef = useRef(desktopSidebarWidth)
  const enabledModules = Array.isArray(user?.enabled_modules) ? user.enabled_modules : []
  const canSeeModule = useCallback((module) => (
    canAccessModuleList(enabledModules, module)
  ), [enabledModules])
  const hasFinanceAccess = user?.role === 'admin' || user?.role === 'accountant'

  const isActiveRoute = useCallback((path) => (
    pathname === path || pathname.startsWith(path + '/')
  ), [pathname])

  const handleShellNavigate = useCallback((path) => {
    navigate(path)
    setSidebarOpen(false)
  }, [navigate])

  useEffect(() => {
    setSidebarOpen(false)
  }, [pathname])

  useEffect(() => {
    sidebarWidthRef.current = desktopSidebarWidth
    try {
      window.localStorage.setItem(SIDEBAR_WIDTH_KEY, String(desktopSidebarWidth))
    } catch {
      // Device-local layout preferences are optional.
    }
  }, [desktopSidebarWidth])

  useEffect(() => {
    try {
      window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(desktopSidebarCollapsed))
    } catch {
      // Device-local layout preferences are optional.
    }
  }, [desktopSidebarCollapsed])

  useEffect(() => {
    if (!isResizingSidebar) return undefined

    const handlePointerMove = (event) => {
      const resizeState = resizeStateRef.current
      if (!resizeState) return
      const nextWidth = Math.min(
        SIDEBAR_SNAP_POINTS[SIDEBAR_SNAP_POINTS.length - 1],
        Math.max(SIDEBAR_SNAP_POINTS[0], resizeState.startWidth + event.clientX - resizeState.startX),
      )
      sidebarWidthRef.current = nextWidth
      setDesktopSidebarWidth(nextWidth)
    }

    const handlePointerUp = () => {
      const snappedWidth = nearestSidebarSnap(sidebarWidthRef.current)
      sidebarWidthRef.current = snappedWidth
      setDesktopSidebarWidth(snappedWidth)
      setIsResizingSidebar(false)
      resizeStateRef.current = null
    }

    window.addEventListener('pointermove', handlePointerMove)
    window.addEventListener('pointerup', handlePointerUp)
    window.addEventListener('pointercancel', handlePointerUp)
    return () => {
      window.removeEventListener('pointermove', handlePointerMove)
      window.removeEventListener('pointerup', handlePointerUp)
      window.removeEventListener('pointercancel', handlePointerUp)
    }
  }, [isResizingSidebar])

  const handleResizeStart = useCallback((event) => {
    if (desktopSidebarCollapsed) return
    event.preventDefault()
    resizeStateRef.current = {
      startX: event.clientX,
      startWidth: desktopSidebarWidth,
    }
    setIsResizingSidebar(true)
  }, [desktopSidebarCollapsed, desktopSidebarWidth])

  const handleResizeKeyDown = useCallback((event) => {
    const currentIndex = SIDEBAR_SNAP_POINTS.indexOf(nearestSidebarSnap(desktopSidebarWidth))
    let nextIndex = currentIndex
    if (event.key === 'ArrowLeft') nextIndex = Math.max(0, currentIndex - 1)
    else if (event.key === 'ArrowRight') nextIndex = Math.min(SIDEBAR_SNAP_POINTS.length - 1, currentIndex + 1)
    else if (event.key === 'Home') nextIndex = 0
    else if (event.key === 'End') nextIndex = SIDEBAR_SNAP_POINTS.length - 1
    else return

    event.preventDefault()
    const nextWidth = SIDEBAR_SNAP_POINTS[nextIndex]
    sidebarWidthRef.current = nextWidth
    setDesktopSidebarWidth(nextWidth)
  }, [desktopSidebarWidth])

  const loadSidebarData = useCallback(async () => {
    if (!canSeeModule('chat')) {
      setConversations([])
      setDocuments([])
      return
    }
    try {
      const [convs, docs] = await Promise.all([getConversations(), getDocuments()])
      setConversations(convs || [])
      setDocuments(Array.isArray(docs) ? docs : docs?.documents || [])
    } catch {
      // silent — sidebar data is non-critical
    }
  }, [canSeeModule])

  useEffect(() => {
    loadSidebarData()
  }, [loadSidebarData])

  const handleSelectConversation = useCallback((id) => {
    setActiveConvId(id)
    navigate(`/chat?conv=${id}`)
    setSidebarOpen(false)
  }, [navigate])

  const handleNewConversation = useCallback(async () => {
    if (!canSeeModule('chat')) return
    try {
      const conv = await createConversation()
      setConversations((prev) => [conv, ...prev])
      setActiveConvId(conv.id)
      navigate(`/chat?conv=${conv.id}`)
      setSidebarOpen(false)
    } catch (err) {
      console.error('Failed to create conversation', err)
    }
  }, [canSeeModule, navigate])

  const handleConversationDeleted = useCallback(async (id) => {
    const confirmed = await confirmAction({
      title: 'Delete conversation?',
      message: 'This permanently deletes the conversation and its messages.',
      confirmLabel: 'Delete conversation',
      destructive: true,
    })
    if (!confirmed) return false
    try {
      await deleteConversation(id)
      setConversations((prev) => prev.filter((c) => c.id !== id))
      if (activeConvId === id) {
        setActiveConvId(null)
      }
      return true
    } catch (err) {
      console.error('Failed to delete conversation', err)
      throw err
    }
  }, [activeConvId, confirmAction])

  const handleDocumentUploaded = useCallback((doc) => {
    setDocuments((prev) => {
      if (prev.find((d) => d.id === doc.id)) return prev
      return [doc, ...prev]
    })
  }, [])

  const handleDocumentDeleted = useCallback(async (id) => {
    try {
      await deleteDocument(id)
      setDocuments((prev) => prev.filter((d) => d.id !== id))
    } catch (err) {
      console.error('Failed to delete document', err)
    }
  }, [])

  const handleLogout = async () => {
    try { await logout() } catch { /* ignore */ }
    authLogout()
    navigate('/login')
  }

  useEffect(() => {
    if (!canSeeModule('chat')) return undefined
    const handler = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'n') {
        e.preventDefault()
        handleNewConversation()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [canSeeModule, handleNewConversation])

  const visibleMobileNavItems = MOBILE_NAV_ITEMS.filter(({ path, module }) => {
    if (module) return canSeeModule(module)
    if (path === '/matters') return canSeeModule('matters')
    if (path === '/tasks') return canSeeModule('tasks')
    if (path === '/chat') return canSeeModule('chat')
    if (path === '/calendar') return canSeeModule('calendar')
    return true
  })

  const ctxValue = {
    conversations,
    setConversations,
    documents,
    setDocuments,
    activeConvId,
    setActiveConvId,
    loadSidebarData,
    onSelectConversation: handleSelectConversation,
    onConversationDeleted: handleConversationDeleted,
    onDocumentUploaded: handleDocumentUploaded,
    onDocumentDeleted: handleDocumentDeleted,
  }

  return (
    <AppShellContext.Provider value={ctxValue}>
      <div className={`flex h-screen [height:100dvh] bg-brand-bg overflow-hidden ${isResizingSidebar ? 'select-none cursor-col-resize' : ''}`}>
        <Sidebar
          user={user}
          onLogout={handleLogout}
          isOpen={sidebarOpen}
          onClose={() => setSidebarOpen(false)}
          desktopWidth={desktopSidebarWidth}
          desktopCollapsed={desktopSidebarCollapsed}
          isResizing={isResizingSidebar}
          onToggleDesktopCollapsed={() => setDesktopSidebarCollapsed((collapsed) => !collapsed)}
        />
        {!desktopSidebarCollapsed && (
          <div
            role="separator"
            aria-label="Resize workspace navigation"
            aria-orientation="vertical"
            aria-valuemin={SIDEBAR_SNAP_POINTS[0]}
            aria-valuemax={SIDEBAR_SNAP_POINTS[SIDEBAR_SNAP_POINTS.length - 1]}
            aria-valuenow={Math.round(desktopSidebarWidth)}
            aria-valuetext={`${Math.round(desktopSidebarWidth)} pixels`}
            tabIndex={0}
            onPointerDown={handleResizeStart}
            onDoubleClick={() => setDesktopSidebarWidth(DEFAULT_SIDEBAR_WIDTH)}
            onKeyDown={handleResizeKeyDown}
            className={`group hidden lg:flex w-2 flex-shrink-0 cursor-col-resize items-center justify-center bg-brand-surface-2 outline-none ${
              isResizingSidebar ? 'text-brand-accent' : 'text-transparent hover:text-brand-line-2 focus-visible:text-brand-accent'
            }`}
            title="Drag to resize. Use arrow keys for compact, standard, or wide."
          >
            <GripVertical size={14} aria-hidden="true" />
          </div>
        )}

        <div className="flex-1 flex flex-col min-w-0">
          {/* Top header bar */}
          <header className="h-16 bg-brand-surface border-b border-brand-line px-4 md:px-6 flex items-center justify-between flex-shrink-0 z-20">
            <div className="flex items-center gap-3 min-w-0">
              <button
                className="lg:hidden tap-target text-brand-muted hover:text-brand-ink transition-colors -ml-1 flex-shrink-0"
                onClick={() => setSidebarOpen(true)}
                aria-label="Open sidebar"
              >
                <Menu size={20} />
              </button>
              {title && (
                <h1 className="font-serif font-bold text-lg text-brand-ink tracking-tight truncate">
                  {title}
                </h1>
              )}
            </div>

            <div className="flex items-center gap-2 sm:gap-3 flex-shrink-0">
              {canSeeModule('matters') && (
                <button
                  onClick={() => handleShellNavigate('/matters')}
                  title="My Matters"
                  aria-current={isActiveRoute('/matters') ? 'page' : undefined}
                  className={`hidden sm:inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-sans font-semibold uppercase tracking-wider rounded-lg border transition-colors ${
                    isActiveRoute('/matters')
                      ? 'bg-brand-ink text-white border-brand-ink'
                      : 'bg-brand-surface text-brand-ink border-brand-line hover:bg-brand-bg-soft hover:border-brand-line-2'
                  }`}
                >
                  <Briefcase size={13} /> My Matters
                </button>
              )}
              {hasFinanceAccess && canSeeModule('admin') && (
                <button
                  onClick={() => navigate('/admin')}
                  title="Administration"
                  className="p-0 bg-transparent border-0"
                >
                  <span className="hidden sm:inline-flex items-center gap-1.5 px-3 py-1.5 bg-brand-ink/10 text-brand-ink text-xs font-sans font-semibold uppercase tracking-wider rounded-lg border border-brand-ink/20 hover:bg-brand-ink/20 transition-colors">
                    <Shield size={13} /> Admin
                  </span>
                  <span className="sm:hidden inline-flex items-center p-2 text-brand-ink/70 hover:text-brand-ink hover:bg-brand-line/40 rounded-lg transition-colors">
                    <Shield size={18} />
                  </span>
                </button>
              )}
            </div>
          </header>

          {/* Main content */}
          <main className="flex-1 overflow-auto [scrollbar-gutter:stable]">
            {children}
          </main>

          <nav
            aria-label="Primary workspace navigation"
            className="lg:hidden min-h-[4.5rem] pb-[env(safe-area-inset-bottom)] bg-brand-surface/95 backdrop-blur border-t border-brand-line grid flex-shrink-0"
            style={{ gridTemplateColumns: `repeat(${Math.max(visibleMobileNavItems.length, 1)}, minmax(0, 1fr))` }}
          >
            {visibleMobileNavItems.map(({ path, label, icon: Icon }) => {
              const active = isActiveRoute(path)
              return (
                <button
                  key={path}
                  onClick={() => handleShellNavigate(path)}
                  aria-current={active ? 'page' : undefined}
                  aria-label={label}
                  className={`group flex min-w-0 flex-col items-center justify-center gap-1 px-1 text-[10px] min-[360px]:text-[11px] font-sans font-semibold ${
                    active ? 'text-brand-ink' : 'text-brand-muted hover:text-brand-ink'
                  }`}
                >
                  <span className={`flex h-8 min-w-10 items-center justify-center rounded-xl px-3 transition-colors ${
                    active ? 'bg-brand-bg-soft text-brand-accent-2' : 'group-hover:bg-brand-bg-soft/60'
                  }`}>
                    <Icon size={18} strokeWidth={active ? 2.2 : 1.8} />
                  </span>
                  <span className="max-w-full truncate">{label}</span>
                </button>
              )
            })}
          </nav>
        </div>
      </div>
    </AppShellContext.Provider>
  )
}
