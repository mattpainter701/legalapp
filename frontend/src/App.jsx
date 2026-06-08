import React, { createContext, useContext, useState, useEffect, useCallback } from 'react'
import { Routes, Route, Navigate, useParams } from 'react-router-dom'
import AppShell from './components/AppShell'
import LoginPage from './pages/LoginPage'
import HomePage from './pages/HomePage'
import SignupPage from './pages/SignupPage'
import ForgotPasswordPage from './pages/ForgotPasswordPage'
import ResetPasswordPage from './pages/ResetPasswordPage'
import ChatPage from './pages/ChatPage'
import AdminPage from './pages/AdminPage'
import AuthCallback from './pages/AuthCallback'
import PluginsPage from './pages/PluginsPage'
import PluginPage from './pages/PluginPage'
import MatterPortfolioPage from './pages/MatterPortfolioPage'
import MatterDetailPage from './pages/MatterDetailPage'
import RenewalTrackerPage from './pages/RenewalTrackerPage'
import EstatePortfolioPage from './pages/EstatePortfolioPage'
import EstateDetailPage from './pages/EstateDetailPage'
import MediationPortfolioPage from './pages/MediationPortfolioPage'
import MediationDetailPage from './pages/MediationDetailPage'
import BillingPage from './pages/BillingPage'
import MCPPage from './pages/MCPPage'
import PlatformPage from './pages/PlatformPage'
import ContactsPage from './pages/ContactsPage'
import ContactDetailPage from './pages/ContactDetailPage'
import TasksPage from './pages/TasksPage'
import IntakePage from './pages/IntakePage'
import ReportsPage from './pages/ReportsPage'
import CalendarPage from './pages/CalendarPage'
import CommunicationsPage from './pages/CommunicationsPage'
import TemplatesPage from './pages/TemplatesPage'
import TimeTrackingPage from './pages/TimeTrackingPage'
import InvoicesPage from './pages/InvoicesPage'
import InvoiceDetailPage from './pages/InvoiceDetailPage'
import ProfilePage from './pages/ProfilePage'
import OnboardingWizard from './pages/OnboardingWizard'
import PortalAcceptPage from './pages/PortalAcceptPage'
import PortalCasePage from './pages/PortalCasePage'
import ClientPortalAcceptPage from './pages/ClientPortalAcceptPage'
import ClientPortalMatterPage from './pages/ClientPortalMatterPage'
import { getMe } from './api'

// ---------------------------------------------------------------------------
// Auth Context
// ---------------------------------------------------------------------------

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [token, setToken] = useState(() => localStorage.getItem('token'))
  const [loading, setLoading] = useState(true)

  const fetchUser = useCallback(async (tok) => {
    try {
      const me = await getMe()
      setUser(me)
      return me
    } catch {
      localStorage.removeItem('token')
      localStorage.removeItem('user')
      setToken(null)
      setUser(null)
      return null
    }
  }, [])

  useEffect(() => {
    if (token) {
      fetchUser(token).finally(() => setLoading(false))
    } else {
      setLoading(false)
    }
  }, [token, fetchUser])

  const login = useCallback(async (newToken) => {
    localStorage.setItem('token', newToken)
    setToken(newToken)
    const me = await fetchUser(newToken)
    return me
  }, [fetchUser])

  const logoutUser = useCallback(() => {
    localStorage.removeItem('token')
    localStorage.removeItem('user')
    setToken(null)
    setUser(null)
  }, [])

  return (
    <AuthContext.Provider value={{ user, token, login, logout: logoutUser, loading }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}

// ---------------------------------------------------------------------------
// Route guards
// ---------------------------------------------------------------------------

function ProtectedRoute({ children, adminOnly = false }) {
  const { user, loading } = useAuth()

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen bg-brand-bg">
        <div className="text-brand-ink text-lg font-serif">Loading...</div>
      </div>
    )
  }

  if (!user) {
    return <Navigate to="/login" replace />
  }

  if (adminOnly && user.role !== 'admin') {
    return <Navigate to="/chat" replace />
  }

  return children
}

function ShellRoute({ children, title, adminOnly = false }) {
  return (
    <ProtectedRoute adminOnly={adminOnly}>
      <AppShell title={title}>
        {children}
      </AppShell>
    </ProtectedRoute>
  )
}

function RootRedirect() {
  const { user, loading } = useAuth()

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen bg-brand-bg">
        <div className="text-brand-ink text-lg font-serif">Loading...</div>
      </div>
    )
  }

  return user ? <Navigate to="/matters" replace /> : <HomePage />
}

function RedirectMatterId() {
  const { id } = useParams()
  return <Navigate to={`/matters/${id}`} replace />
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/" element={<RootRedirect />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/signup" element={<SignupPage />} />
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="/auth/callback" element={<AuthCallback />} />
        <Route path="/portal/accept" element={<PortalAcceptPage />} />
        <Route path="/portal/case" element={<PortalCasePage />} />
        <Route path="/portal/client/accept" element={<ClientPortalAcceptPage />} />
        <Route path="/portal/client/matter" element={<ClientPortalMatterPage />} />

        {/* Authenticated pages wrapped in AppShell */}
        <Route
          path="/chat"
          element={<ShellRoute title="Chat"><ChatPage /></ShellRoute>}
        />
        <Route
          path="/matters"
          element={<ShellRoute title="My Matters"><MatterPortfolioPage /></ShellRoute>}
        />
        <Route
          path="/matters/:id"
          element={<ShellRoute title="Matter Details"><MatterDetailPage /></ShellRoute>}
        />
        <Route
          path="/calendar"
          element={<ShellRoute title="Calendar"><CalendarPage /></ShellRoute>}
        />
        <Route
          path="/communications"
          element={<ShellRoute title="Communications"><CommunicationsPage /></ShellRoute>}
        />
        <Route
          path="/time-tracking"
          element={<ShellRoute title="Time Tracking"><TimeTrackingPage /></ShellRoute>}
        />
        <Route
          path="/invoices"
          element={<ShellRoute title="Invoices"><InvoicesPage /></ShellRoute>}
        />
        <Route
          path="/invoices/:id"
          element={<ShellRoute title="Invoice"><InvoiceDetailPage /></ShellRoute>}
        />
        <Route
          path="/reports"
          element={<ShellRoute title="Reports"><ReportsPage /></ShellRoute>}
        />
        <Route
          path="/templates"
          element={<ShellRoute title="Templates"><TemplatesPage /></ShellRoute>}
        />
        <Route
          path="/billing"
          element={<ShellRoute title="Billing"><BillingPage /></ShellRoute>}
        />
        <Route
          path="/contacts"
          element={<ShellRoute title="Contacts"><ContactsPage /></ShellRoute>}
        />
        <Route
          path="/contacts/:id"
          element={<ShellRoute title="Contact"><ContactDetailPage /></ShellRoute>}
        />
        <Route
          path="/tasks"
          element={<ShellRoute title="Tasks"><TasksPage /></ShellRoute>}
        />
        <Route
          path="/intake"
          element={<ShellRoute title="Intake"><IntakePage /></ShellRoute>}
        />
        <Route
          path="/plugins"
          element={<ShellRoute title="Add-on Modules"><PluginsPage /></ShellRoute>}
        />
        <Route
          path="/plugins/:pluginName"
          element={<ShellRoute title="Plugin"><PluginPage /></ShellRoute>}
        />
        <Route
          path="/profile"
          element={<ShellRoute title="Profile"><ProfilePage /></ShellRoute>}
        />

        {/* Plugin sub-routes */}
        <Route
          path="/plugins/commercial/renewals"
          element={<ShellRoute title="Renewal Tracker"><RenewalTrackerPage /></ShellRoute>}
        />
        <Route
          path="/plugins/trust-estate/estates"
          element={<ShellRoute title="Trust & Estate"><EstatePortfolioPage /></ShellRoute>}
        />
        <Route
          path="/plugins/trust-estate/estates/:id"
          element={<ShellRoute title="Estate"><EstateDetailPage /></ShellRoute>}
        />
        <Route
          path="/plugins/mediation/cases"
          element={<ShellRoute title="Mediation Cases"><MediationPortfolioPage /></ShellRoute>}
        />
        <Route
          path="/plugins/mediation/cases/:id"
          element={<ShellRoute title="Mediation Case"><MediationDetailPage /></ShellRoute>}
        />

        {/* Admin routes */}
        <Route
          path="/admin"
          element={<ShellRoute title="Administration" adminOnly><AdminPage /></ShellRoute>}
        />
        <Route
          path="/mcp"
          element={<ShellRoute title="MCP" adminOnly><MCPPage /></ShellRoute>}
        />
        <Route
          path="/onboarding"
          element={<ShellRoute title="Onboarding" adminOnly><OnboardingWizard /></ShellRoute>}
        />

        {/* Legacy redirects */}
        <Route path="/plugins/litigation/matters" element={<Navigate to="/matters" replace />} />
        <Route
          path="/plugins/litigation/matters/:id"
          element={
            <ProtectedRoute>
              <RedirectMatterId />
            </ProtectedRoute>
          }
        />

        {/* Platform admin — standalone auth */}
        <Route path="/platform" element={<PlatformPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  )
}
