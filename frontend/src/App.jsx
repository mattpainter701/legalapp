import { Suspense, createContext, lazy, useContext, useState, useEffect, useCallback } from 'react'
import { Routes, Route, Navigate, useParams, useSearchParams } from 'react-router-dom'
import AppShell from './components/AppShell'
import { ToastProvider } from './components/toast/ToastProvider'
import { ConfirmProvider } from './components/dialog/ConfirmProvider'
import SeoHead from './components/SeoHead'
import VersionBadge from './components/VersionBadge'
import ReleaseAnnouncement from './components/ReleaseAnnouncement'
import AppErrorBoundary from './components/AppErrorBoundary'
import { getMe } from './api'
import { canAccessModuleList } from './moduleAccess'

const LoginPage = lazy(() => import('./pages/LoginPage'))
const DemoLoginPage = lazy(() => import('./pages/DemoLoginPage'))
const HomePage = lazy(() => import('./pages/HomePage'))
const SignupPage = lazy(() => import('./pages/SignupPage'))
const ForgotPasswordPage = lazy(() => import('./pages/ForgotPasswordPage'))
const ResetPasswordPage = lazy(() => import('./pages/ResetPasswordPage'))
const LegalNoticePage = lazy(() => import('./pages/LegalNoticePage'))
const ProductChatPage = lazy(() => import('./pages/ProductChatPage'))
const McpProductPage = lazy(() => import('./pages/McpProductPage'))
const PricingPage = lazy(() => import('./pages/PricingPage'))
const ProductPage = lazy(() => import('./pages/ProductPage'))
const NotFoundPage = lazy(() => import('./pages/NotFoundPage'))
const ChatPage = lazy(() => import('./pages/ChatPage'))
const AdminPage = lazy(() => import('./pages/AdminPage'))
const AuthCallback = lazy(() => import('./pages/AuthCallback'))
const PluginsPage = lazy(() => import('./pages/PluginsPage'))
const PluginPage = lazy(() => import('./pages/PluginPage'))
const MatterPortfolioPage = lazy(() => import('./pages/MatterPortfolioPage'))
const MatterDetailPage = lazy(() => import('./pages/MatterDetailPage'))
const DocumentRevisionPage = lazy(() => import('./pages/DocumentRevisionPage'))
const RenewalTrackerPage = lazy(() => import('./pages/RenewalTrackerPage'))
const EstatePortfolioPage = lazy(() => import('./pages/EstatePortfolioPage'))
const EstateDetailPage = lazy(() => import('./pages/EstateDetailPage'))
const DomesticPortfolioPage = lazy(() => import('./pages/DomesticPortfolioPage'))
const DomesticDetailPage = lazy(() => import('./pages/DomesticDetailPage'))
const MediationPortfolioPage = lazy(() => import('./pages/MediationPortfolioPage'))
const MediationDetailPage = lazy(() => import('./pages/MediationDetailPage'))
const PlatformPage = lazy(() => import('./pages/PlatformPage'))
const ContactsPage = lazy(() => import('./pages/ContactsPage'))
const ContactDetailPage = lazy(() => import('./pages/ContactDetailPage'))
const TasksPage = lazy(() => import('./pages/TasksPage'))
const IntakePage = lazy(() => import('./pages/IntakePage'))
const IntakeDashboardPage = lazy(() => import('./pages/IntakeDashboardPage'))
const ReportsPage = lazy(() => import('./pages/ReportsPage'))
const TrustAccountingPage = lazy(() => import('./pages/TrustAccountingPage'))
const TrustAccountDetail = lazy(() => import('./components/TrustAccountDetail'))
const CalendarPage = lazy(() => import('./pages/CalendarPage'))
const TeamsTabPage = lazy(() => import('./pages/TeamsTabPage'))
const TeamsTabConfigPage = lazy(() => import('./pages/TeamsTabConfigPage'))
const CommunicationsPage = lazy(() => import('./pages/CommunicationsPage'))
const TemplatesPage = lazy(() => import('./pages/TemplatesPage'))
const TimeTrackingPage = lazy(() => import('./pages/TimeTrackingPage'))
const InvoicesPage = lazy(() => import('./pages/InvoicesPage'))
const InvoiceDetailPage = lazy(() => import('./pages/InvoiceDetailPage'))
const ProfilePage = lazy(() => import('./pages/ProfilePage'))
const OnboardingWizard = lazy(() => import('./pages/OnboardingWizard'))
const PortalAcceptPage = lazy(() => import('./pages/PortalAcceptPage'))
const PortalCasePage = lazy(() => import('./pages/PortalCasePage'))
const ClientPortalAcceptPage = lazy(() => import('./pages/ClientPortalAcceptPage'))
const ClientPortalMatterPage = lazy(() => import('./pages/ClientPortalMatterPage'))

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
  return canAccessModuleList(user?.enabled_modules, module)
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

  if (loading) return <HomePage />

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
    <AppErrorBoundary>
    <AuthProvider>
      <SeoHead />
      <ToastProvider>
        <ConfirmProvider>
        <VersionBadge />
        <ReleaseAnnouncement />
        <Suspense fallback={<div role="status" className="flex min-h-screen items-center justify-center bg-brand-bg text-brand-ink">Loading workspace…</div>}>
        <Routes>
        <Route path="/" element={<RootRedirect />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/demo" element={<DemoLoginPage />} />
        <Route path="/signup" element={<SignupPage />} />
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="/terms" element={<LegalNoticePage type="terms" />} />
        <Route path="/privacy" element={<LegalNoticePage type="privacy" />} />
        <Route path="/product" element={<ProductPage />} />
        <Route path="/product/chat" element={<ProductChatPage />} />
        <Route path="/product/mcp" element={<McpProductPage />} />
        <Route path="/pricing" element={<PricingPage />} />
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
          path="/matters/:matterId/documents/:documentId/revise"
          element={<ShellRoute title="Revise Document" module="matters"><DocumentRevisionPage /></ShellRoute>}
        />
        <Route
          path="/matters/:matterId/documents/:documentId/revisions/:revisionId"
          element={<ShellRoute title="Review Document Revision" module="matters"><DocumentRevisionPage /></ShellRoute>}
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
          element={<ProtectedRoute adminOnly module="mcp"><Navigate to="/admin?tab=mcp" replace /></ProtectedRoute>}
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
        <Route path="*" element={<NotFoundPage />} />
        </Routes>
        </Suspense>
        </ConfirmProvider>
      </ToastProvider>
    </AuthProvider>
    </AppErrorBoundary>
  )
}
