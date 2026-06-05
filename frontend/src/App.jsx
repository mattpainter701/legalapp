import React, { createContext, useContext, useState, useEffect, useCallback } from 'react'
import { Routes, Route, Navigate, useNavigate, useParams } from 'react-router-dom'
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
    // DEPRECATED: No longer storing token in localStorage (now using httpOnly cookie)
    // Keep this line for backward compatibility during transition, but new tokens come from cookies
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
        {/* Portal routes — auth via httpOnly cookie from invite acceptance */}
        <Route path="/portal/accept" element={<PortalAcceptPage />} />
        <Route path="/portal/case" element={<PortalCasePage />} />
        <Route
          path="/onboarding"
          element={
            <ProtectedRoute adminOnly>
              <OnboardingWizard />
            </ProtectedRoute>
          }
        />
        <Route
          path="/chat"
          element={
            <ProtectedRoute>
              <ChatPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/admin"
          element={
            <ProtectedRoute adminOnly>
              <AdminPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/plugins"
          element={
            <ProtectedRoute>
              <PluginsPage />
            </ProtectedRoute>
          }
        />
        {/* Primary matter routes */}
        <Route
          path="/matters"
          element={
            <ProtectedRoute>
              <MatterPortfolioPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/matters/:id"
          element={
            <ProtectedRoute>
              <MatterDetailPage />
            </ProtectedRoute>
          }
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
        <Route
          path="/plugins/commercial/renewals"
          element={
            <ProtectedRoute>
              <RenewalTrackerPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/plugins/trust-estate/estates"
          element={
            <ProtectedRoute>
              <EstatePortfolioPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/plugins/trust-estate/estates/:id"
          element={
            <ProtectedRoute>
              <EstateDetailPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/plugins/mediation/cases"
          element={
            <ProtectedRoute>
              <MediationPortfolioPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/plugins/mediation/cases/:id"
          element={
            <ProtectedRoute>
              <MediationDetailPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/plugins/:pluginName"
          element={
            <ProtectedRoute>
              <PluginPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/billing"
          element={
            <ProtectedRoute>
              <BillingPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/contacts"
          element={
            <ProtectedRoute>
              <ContactsPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/contacts/:id"
          element={
            <ProtectedRoute>
              <ContactDetailPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/tasks"
          element={
            <ProtectedRoute>
              <TasksPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/intake"
          element={
            <ProtectedRoute>
              <IntakePage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/reports"
          element={
            <ProtectedRoute>
              <ReportsPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/calendar"
          element={
            <ProtectedRoute>
              <CalendarPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/communications"
          element={
            <ProtectedRoute>
              <CommunicationsPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/templates"
          element={
            <ProtectedRoute>
              <TemplatesPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/mcp"
          element={
            <ProtectedRoute adminOnly>
              <MCPPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/time-tracking"
          element={
            <ProtectedRoute>
              <TimeTrackingPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/invoices"
          element={
            <ProtectedRoute>
              <InvoicesPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/invoices/:id"
          element={
            <ProtectedRoute>
              <InvoiceDetailPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/profile"
          element={
            <ProtectedRoute>
              <ProfilePage />
            </ProtectedRoute>
          }
        />
        {/* Platform admin — has its own auth (platform key), not protected by JWT */}
        <Route path="/platform" element={<PlatformPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  )
}
