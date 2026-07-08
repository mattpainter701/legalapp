import React, { createContext, useContext, useState, useEffect, useCallback } from 'react'
import { Routes, Route, Navigate, useParams, useSearchParams } from 'react-router-dom'
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
import DomesticPortfolioPage from './pages/DomesticPortfolioPage'
import DomesticDetailPage from './pages/DomesticDetailPage'
import MediationPortfolioPage from './pages/MediationPortfolioPage'
import MediationDetailPage from './pages/MediationDetailPage'
import MCPPage from './pages/MCPPage'
import PlatformPage from './pages/PlatformPage'
import ContactsPage from './pages/ContactsPage'
import ContactDetailPage from './pages/ContactDetailPage'
import TasksPage from './pages/TasksPage'
import IntakePage from './pages/IntakePage'
import IntakeDashboardPage from './pages/IntakeDashboardPage'
import ReportsPage from './pages/ReportsPage'
import TrustAccountingPage from './pages/TrustAccountingPage'
import TrustAccountDetail from './components/TrustAccountDetail'
import CalendarPage from './pages/CalendarPage'
import TeamsTabPage from './pages/TeamsTabPage'
import TeamsTabConfigPage from './pages/TeamsTabConfigPage'
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
import { ToastProvider } from './components/toast/ToastProvider'
import { getMe } from './api'

// ---------------------------------------------------------------------------
// Auth Context
// ---------------------------------------------------------------------------

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)

  // Auth state lives entirely in the backend's httpOnly cookies — the access
  // token is never read from or written to browser-accessible storage, so an
  // XSS payload cannot exfiltrate a live session token. Session presence is
  // derived by asking the API who the current cookie belongs to.
  const fetchUser = useCallback(async () => {
    try {
      const me = await getMe({ _suppressAuthRedirect: true })
      setUser(me)
      return me
    } catch {
      setUser(null)
      return null
    }
  }, [])

  useEffect(() => {
    fetchUser().finally(() => setLoading(false))
  }, [fetchUser])

  const login = useCallback(async () => {
    // The login/register/oauth-exchange call already set the httpOnly
    // cookies server-side; just resolve who we are now.
    const me = await fetchUser()
    return me
  }, [fetchUser])

  const logoutUser = useCallback(() => {
    setUser(null)
  }, [])

  return (
    <AuthContext.Provider value={{ user, login, logout: logoutUser, refreshUser: fetchUser, loading }}>
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

function canAccessModule(user, module) {
  if (!module) return true
  const enabled = user?.enabled_modules
  if (!Array.isArray(enabled) || enabled.length === 0) return true
  return enabled.includes(module)
}

function hasFinanceAccess(user) {
  return user?.role === 'admin' || user?.role === 'accountant'
}

function ProtectedRoute({ children, adminOnly = false, financeOnly = false, module = null }) {
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
    return <Navigate to={user.default_route || '/matters'} replace />
  }

  if (financeOnly && !hasFinanceAccess(user)) {
    return <Navigate to={user.default_route || '/matters'} replace />
  }

  if (!canAccessModule(user, module)) {
    return <Navigate to={user.default_route || '/intake/dashboard'} replace />
  }

  return children
}

function ShellRoute({ children, title, adminOnly = false, financeOnly = false, module = null }) {
  return (
    <ProtectedRoute adminOnly={adminOnly} financeOnly={financeOnly} module={module}>
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

  return user ? <Navigate to={user.default_route || '/matters'} replace /> : <HomePage />
}

function RedirectMatterId() {
  const { id } = useParams()
  return <Navigate to={`/matters/${id}`} replace />
}

function LegacyBillingRedirect() {
  const [searchParams] = useSearchParams()
  const success = searchParams.get('success')
  return <Navigate to={`/admin?tab=billing${success ? '&success=1' : ''}`} replace />
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

export default function App() {
  return (
    <AuthProvider>
      <ToastProvider>
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
          element={<ShellRoute title="Chat" module="chat"><ChatPage /></ShellRoute>}
        />
        <Route
          path="/matters"
          element={<ShellRoute title="My Matters" module="matters"><MatterPortfolioPage /></ShellRoute>}
        />
        <Route
          path="/matters/:id"
          element={<ShellRoute title="Matter Details" module="matters"><MatterDetailPage /></ShellRoute>}
        />
        <Route
          path="/calendar"
          element={<ShellRoute title="Calendar" module="calendar"><CalendarPage /></ShellRoute>}
        />
        <Route
          path="/teams"
          element={<ProtectedRoute><TeamsTabPage /></ProtectedRoute>}
        />
        <Route
          path="/teams/config"
          element={<ProtectedRoute adminOnly><TeamsTabConfigPage /></ProtectedRoute>}
        />
        <Route
          path="/communications"
          element={<ShellRoute title="Communications" module="communications"><CommunicationsPage /></ShellRoute>}
        />
        <Route
          path="/time-tracking"
          element={<ShellRoute title="Time Tracking" module="time-tracking"><TimeTrackingPage /></ShellRoute>}
        />
        <Route
          path="/invoices"
          element={<ShellRoute title="Invoices" module="invoices"><InvoicesPage /></ShellRoute>}
        />
        <Route
          path="/invoices/:id"
          element={<ShellRoute title="Invoice" module="invoices"><InvoiceDetailPage /></ShellRoute>}
        />
        <Route
          path="/reports"
          element={<ShellRoute title="Reports" module="reports"><ReportsPage /></ShellRoute>}
        />
        <Route
          path="/trust"
          element={<ShellRoute title="Trust Accounting" module="trust"><TrustAccountingPage /></ShellRoute>}
        />
        <Route
          path="/trust/:id"
          element={<ShellRoute title="Trust Account" module="trust"><TrustAccountDetail /></ShellRoute>}
        />
        <Route
          path="/templates"
          element={<ShellRoute title="Document Automation" module="templates"><TemplatesPage /></ShellRoute>}
        />
        <Route
          path="/billing"
          element={<ProtectedRoute financeOnly><LegacyBillingRedirect /></ProtectedRoute>}
        />
        <Route
          path="/contacts"
          element={<ShellRoute title="Contacts" module="contacts"><ContactsPage /></ShellRoute>}
        />
        <Route
          path="/contacts/:id"
          element={<ShellRoute title="Contact" module="contacts"><ContactDetailPage /></ShellRoute>}
        />
        <Route
          path="/tasks"
          element={<ShellRoute title="Tasks" module="tasks"><TasksPage /></ShellRoute>}
        />
        <Route
          path="/tasks/:taskId"
          element={<ShellRoute title="Tasks" module="tasks"><TasksPage /></ShellRoute>}
        />
        <Route
          path="/intake"
          element={<ShellRoute title="Intake" module="intake"><IntakePage /></ShellRoute>}
        />
        <Route
          path="/intake/dashboard"
          element={<ShellRoute title="Intake Dashboard" module="intake-dashboard"><IntakeDashboardPage /></ShellRoute>}
        />
        <Route
          path="/plugins"
          element={<ShellRoute title="Add-on Modules" module="plugins"><PluginsPage /></ShellRoute>}
        />
        <Route
          path="/plugins/:pluginName"
          element={<ShellRoute title="Plugin" module="plugins"><PluginPage /></ShellRoute>}
        />
        <Route
          path="/profile"
          element={<ShellRoute title="Profile"><ProfilePage /></ShellRoute>}
        />

        {/* Plugin sub-routes */}
        <Route
          path="/plugins/commercial/renewals"
          element={<ShellRoute title="Renewal Tracker" module="plugins"><RenewalTrackerPage /></ShellRoute>}
        />
        <Route
          path="/plugins/trust-estate/estates"
          element={<ShellRoute title="Trust & Estate" module="plugins"><EstatePortfolioPage /></ShellRoute>}
        />
        <Route
          path="/plugins/trust-estate/estates/:id"
          element={<ShellRoute title="Estate" module="plugins"><EstateDetailPage /></ShellRoute>}
        />
        <Route
          path="/plugins/domestic/cases"
          element={<ShellRoute title="Domestic Relations" module="plugins"><DomesticPortfolioPage /></ShellRoute>}
        />
        <Route
          path="/plugins/domestic/cases/:id"
          element={<ShellRoute title="Domestic Case" module="plugins"><DomesticDetailPage /></ShellRoute>}
        />
        <Route
          path="/plugins/mediation/cases"
          element={<ShellRoute title="Mediation Cases" module="plugins"><MediationPortfolioPage /></ShellRoute>}
        />
        <Route
          path="/plugins/mediation/cases/:id"
          element={<ShellRoute title="Mediation Case" module="plugins"><MediationDetailPage /></ShellRoute>}
        />

        {/* Admin routes */}
        <Route
          path="/admin"
          element={<ShellRoute title="Administration" financeOnly module="admin"><AdminPage /></ShellRoute>}
        />
        <Route
          path="/mcp"
          element={<ShellRoute title="MCP" adminOnly module="mcp"><MCPPage /></ShellRoute>}
        />
        <Route
          path="/onboarding"
          element={<ShellRoute title="Onboarding" adminOnly module="onboarding"><OnboardingWizard /></ShellRoute>}
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
      </ToastProvider>
    </AuthProvider>
  )
}
