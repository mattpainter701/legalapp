import React, { createContext, useContext, useState, useEffect, useCallback } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../App'
import Sidebar from './Sidebar'
import { getConversations, createConversation, deleteConversation, getDocuments, uploadDocument, deleteDocument, logout } from '../api'
import { Briefcase, CalendarDays, CheckSquare, Menu, MessageSquare, Shield } from 'lucide-react'

const AppShellContext = createContext(null)

const MOBILE_NAV_ITEMS = [
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
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [conversations, setConversations] = useState([])
  const [documents, setDocuments] = useState([])
  const [activeConvId, setActiveConvId] = useState(null)
  const enabledModules = Array.isArray(user?.enabled_modules) ? user.enabled_modules : []
  const canSeeModule = useCallback((module) => (
    enabledModules.length === 0 || enabledModules.includes(module)
  ), [enabledModules])
  const hasFinanceAccess = user?.role === 'admin' || user?.role === 'accountant'

  const isActiveRoute = useCallback((path) => (
    pathname === path || pathname.startsWith(path + '/')
  ), [pathname])

  const handleShellNavigate = useCallback((path) => {
    navigate(path)
    setSidebarOpen(false)
  }, [navigate])

  const loadSidebarData = useCallback(async () => {
    try {
      const [convs, docs] = await Promise.all([getConversations(), getDocuments()])
      setConversations(convs || [])
      setDocuments(Array.isArray(docs) ? docs : docs?.documents || [])
    } catch {
      // silent — sidebar data is non-critical
    }
  }, [])

  useEffect(() => {
    loadSidebarData()
  }, [loadSidebarData])

  const handleSelectConversation = useCallback((id) => {
    setActiveConvId(id)
    navigate(`/chat?conv=${id}`)
    setSidebarOpen(false)
  }, [navigate])

  const handleNewConversation = useCallback(async () => {
    try {
      const conv = await createConversation()
      setConversations((prev) => [conv, ...prev])
      setActiveConvId(conv.id)
      navigate(`/chat?conv=${conv.id}`)
      setSidebarOpen(false)
    } catch (err) {
      console.error('Failed to create conversation', err)
    }
  }, [navigate])

  const handleConversationDeleted = useCallback(async (id) => {
    try {
      await deleteConversation(id)
      setConversations((prev) => prev.filter((c) => c.id !== id))
      if (activeConvId === id) {
        setActiveConvId(null)
      }
    } catch (err) {
      console.error('Failed to delete conversation', err)
      throw err
    }
  }, [activeConvId])

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
    const handler = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'n') {
        e.preventDefault()
        handleNewConversation()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [handleNewConversation])

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
      <div className="flex h-screen [height:100dvh] bg-brand-bg overflow-hidden">
        <Sidebar
          user={user}
          onLogout={handleLogout}
          isOpen={sidebarOpen}
          onClose={() => setSidebarOpen(false)}
        />

        <div className="flex-1 flex flex-col min-w-0">
          {/* Top header bar */}
          <header className="h-16 bg-brand-surface border-b border-brand-line px-4 md:px-6 flex items-center justify-between flex-shrink-0 z-20">
            <div className="flex items-center gap-3 min-w-0">
              <button
                className="md:hidden tap-target text-brand-muted hover:text-brand-ink transition-colors -ml-1 flex-shrink-0"
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
          <main className="flex-1 overflow-auto">
            {children}
          </main>

          <nav className="md:hidden min-h-[4rem] pb-[env(safe-area-inset-bottom)] bg-brand-surface border-t border-brand-line grid grid-cols-4 flex-shrink-0">
            {MOBILE_NAV_ITEMS.filter(({ path }) => {
              if (path === '/matters' || path === '/tasks') return canSeeModule('matters')
              if (path === '/chat') return canSeeModule('chat')
              if (path === '/calendar') return canSeeModule('calendar')
              return true
            }).map(({ path, label, icon: Icon }) => {
              const active = isActiveRoute(path)
              return (
                <button
                  key={path}
                  onClick={() => handleShellNavigate(path)}
                  aria-current={active ? 'page' : undefined}
                  className={`flex flex-col items-center justify-center gap-1 text-[11px] font-sans font-semibold ${
                    active ? 'text-brand-ink' : 'text-brand-muted hover:text-brand-ink'
                  }`}
                >
                  <Icon size={18} strokeWidth={active ? 2.2 : 1.8} />
                  <span>{label}</span>
                </button>
              )
            })}
          </nav>
        </div>
      </div>
    </AppShellContext.Provider>
  )
}
