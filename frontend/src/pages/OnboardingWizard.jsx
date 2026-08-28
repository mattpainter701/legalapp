import React, { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../App'
import {
  getOnboardingStatus,
  completeOnboarding,
  skipOnboarding,
  updateOnboardingStep,
  API_BASE_URL,
} from '../api'
import { AgreementAcceptancePanel } from '../components/CompliancePanel'

const STEPS = [
  { id: 0, label: 'Welcome' },
  { id: 1, label: 'Connect' },
  { id: 2, label: 'Sync Users' },
  { id: 3, label: 'Review' },
  { id: 4, label: 'Complete' },
]

export default function OnboardingWizard() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [step, setStep] = useState(0)
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [syncing, setSyncing] = useState(false)
  const [completing, setCompleting] = useState(false)
  const [agreementStatus, setAgreementStatus] = useState(null)

  useEffect(() => {
    loadStatus()
  }, [])

  const loadStatus = async () => {
    try {
      const data = await getOnboardingStatus()
      setStatus(data)
      setStep(data.onboarding_step)
    } catch (err) {
      setError('Failed to load onboarding status.')
    } finally {
      setLoading(false)
    }
  }

  const advanceStep = async (newStep) => {
    setStep(newStep)
    try {
      await updateOnboardingStep(newStep)
    } catch {
      // Non-fatal — progress is persisted optimistically
    }
    await loadStatus()
  }

  const handleConnectMicrosoft = () => {
    window.location.href = `${API_BASE_URL}/integrations/microsoft/connect?intent=admin`
  }

  const handleConnectGoogle = () => {
    window.location.href = `${API_BASE_URL}/integrations/google/connect?intent=admin`
  }

  const handleSyncUsers = async () => {
    setSyncing(true)
    try {
      await advanceStep(2)
      await loadStatus()
      // After integration connect, the backend auto-syncs.
      // If it already happened, advance to review.
      const msConnected = status?.integrations?.microsoft?.connected
      const googleConnected = status?.integrations?.google?.connected
      if (msConnected || googleConnected) {
        await advanceStep(3)
      }
    } catch {
      setError('User sync failed. Try again from the Admin panel later.')
    } finally {
      setSyncing(false)
    }
  }

  const handleComplete = async () => {
    setCompleting(true)
    try {
      await completeOnboarding()
      navigate('/admin', { replace: true })
    } catch (err) {
      setError(
        err?.response?.data?.detail ||
          'Failed to complete onboarding. At least one integration must be connected.'
      )
    } finally {
      setCompleting(false)
    }
  }

  const handleSkip = async () => {
    setCompleting(true)
    try {
      await skipOnboarding()
      navigate('/matters', { replace: true })
    } catch {
      setError('Failed to skip.')
    } finally {
      setCompleting(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen bg-brand-bg">
        <div className="text-center">
          <div className="inline-block w-10 h-10 border-4 border-brand-ink border-t-transparent rounded-full animate-spin mb-4" />
          <p className="text-brand-ink font-sans text-sm font-medium">Loading setup...</p>
        </div>
      </div>
    )
  }

  const msConnected = status?.integrations?.microsoft?.connected
  const googleConnected = status?.integrations?.google?.connected
  const hasIntegration = msConnected || googleConnected
  const agreementReady = agreementStatus !== null && !agreementStatus.blocking
  const syncedUsers = status?.synced_users || {}
  const totalSynced = (syncedUsers.microsoft || 0) + (syncedUsers.google || 0)

  return (
    <div className="min-h-screen bg-brand-bg flex flex-col">
      {/* Header */}
      <div className="px-6 py-4 border-b border-brand-line bg-brand-surface">
        <div className="max-w-3xl mx-auto flex items-center justify-between">
          <h1 className="text-brand-ink font-sans text-lg font-bold tracking-tight">
            {user?.tenant_name || 'Firm'} Setup
          </h1>
          <button
            onClick={handleSkip}
            className="text-brand-ink-2 hover:text-brand-ink font-sans text-xs transition-colors"
          >
            Skip setup
          </button>
        </div>
      </div>

      {/* Step indicator */}
      <div className="px-6 py-4 border-b border-brand-line bg-white">
        <div className="max-w-3xl mx-auto flex items-center justify-between">
          {STEPS.map((s, i) => (
            <React.Fragment key={s.id}>
              <div className="flex items-center gap-2">
                <div
                  className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold transition-colors ${
                    step > s.id
                      ? 'bg-brand-ink text-white'
                      : step === s.id
                        ? 'bg-brand-ink text-white ring-2 ring-brand-ink/20'
                        : 'bg-brand-bg-soft text-brand-ink-2 border border-brand-line'
                  }`}
                >
                  {step > s.id ? (
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                      <path d="M5 13l4 4L19 7" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  ) : (
                    s.id + 1
                  )}
                </div>
                <span
                  className={`text-xs font-medium hidden sm:inline ${
                    step >= s.id ? 'text-brand-ink' : 'text-brand-ink-2'
                  }`}
                >
                  {s.label}
                </span>
              </div>
              {i < STEPS.length - 1 && (
                <div
                  className={`flex-1 h-0.5 mx-3 rounded transition-colors ${
                    step > s.id ? 'bg-brand-ink' : 'bg-brand-line'
                  }`}
                />
              )}
            </React.Fragment>
          ))}
        </div>
      </div>

      {/* Step content */}
      <div className="flex-1 flex items-start justify-center px-6 py-10">
        <div className="max-w-lg w-full">
          {error && (
            <div className="mb-6 px-4 py-3 bg-red-50 border border-red-200 rounded-xl text-red-700 text-xs font-medium">
              {error}
              <button
                onClick={() => setError(null)}
                className="ml-2 underline hover:no-underline"
              >
                Dismiss
              </button>
            </div>
          )}

          {/* Step 0: Welcome */}
          {step === 0 && (
            <div className="bg-brand-surface border border-brand-line rounded-2xl p-8 shadow-sm">
              <div className="text-center mb-8">
                <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-brand-ink/5 mb-4">
                  <svg width="28" height="28" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2V9z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                    <path d="M9 22V12h6v10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </div>
                <h2 className="text-brand-ink font-sans text-xl font-bold mb-2">
                  Welcome, {user?.full_name || user?.email?.split('@')[0]}
                </h2>
                <p className="text-brand-ink-2 font-sans text-sm leading-relaxed">
                  Let's set up your firm in 4 quick steps. Connect your Microsoft 365 or
                  Google Workspace to import users, sync email, and organize case files.
                </p>
              </div>

              <div className="space-y-3 mb-8">
                {[
                  { icon: '🔗', text: 'Connect your firm\'s Microsoft 365 or Google Workspace' },
                  { icon: '👥', text: 'Import your team from the directory' },
                  { icon: '📁', text: 'Create shared folders for case documents' },
                  { icon: '⚙️', text: 'Configure licenses and permissions' },
                ].map((item, i) => (
                  <div key={i} className="flex items-center gap-3 px-4 py-3 bg-brand-bg rounded-xl">
                    <span className="text-lg">{item.icon}</span>
                    <span className="text-brand-ink font-sans text-sm">{item.text}</span>
                  </div>
                ))}
              </div>

              <button
                onClick={() => advanceStep(1)}
                className="w-full py-3 px-6 bg-brand-ink text-white font-sans text-sm font-semibold rounded-xl hover:opacity-90 transition-opacity shadow-sm"
              >
                Get Started
              </button>
            </div>
          )}

          {/* Step 1: Connect Integrations */}
          {step === 1 && (
            <div className="bg-brand-surface border border-brand-line rounded-2xl p-8 shadow-sm">
              <h2 className="text-brand-ink font-sans text-lg font-bold mb-1">Connect Your Firm</h2>
              <p className="text-brand-ink-2 font-sans text-sm mb-8">
                Grant LawHand access to your firm's directory so we can import users
                and sync email. This requires admin consent.
              </p>

              <div className="mb-8 rounded-xl border border-brand-line bg-brand-bg-soft p-4">
                <AgreementAcceptancePanel compact onStatusChange={setAgreementStatus} />
              </div>

              <div className="space-y-4 mb-8">
                {/* Microsoft */}
                <div className={`p-5 rounded-xl border transition-colors ${msConnected ? 'border-green-300 bg-green-50' : 'border-brand-line bg-brand-bg'}`}>
                  <div className="flex items-center justify-between mb-3">
                    <span className="text-brand-ink font-sans text-sm font-semibold">Microsoft 365</span>
                    {msConnected ? (
                      <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full bg-green-100 text-green-700 text-xs font-bold">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none"><path d="M5 13l4 4L19 7" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" /></svg>
                        Connected
                      </span>
                    ) : (
                      <button
                        onClick={handleConnectMicrosoft}
                        disabled={!agreementReady}
                        className="px-4 py-2 bg-brand-ink text-white font-sans text-xs font-semibold rounded-lg hover:opacity-90 transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        Connect
                      </button>
                    )}
                  </div>
                  <p className="text-brand-ink-2 font-sans text-xs leading-relaxed">
                    Required: Read all users, read mail, read files (OneDrive + SharePoint), read/write calendars.
                  </p>
                </div>

                {/* Google */}
                <div className={`p-5 rounded-xl border transition-colors ${googleConnected ? 'border-green-300 bg-green-50' : 'border-brand-line bg-brand-bg'}`}>
                  <div className="flex items-center justify-between mb-3">
                    <span className="text-brand-ink font-sans text-sm font-semibold">Google Workspace</span>
                    {googleConnected ? (
                      <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full bg-green-100 text-green-700 text-xs font-bold">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none"><path d="M5 13l4 4L19 7" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" /></svg>
                        Connected
                      </span>
                    ) : (
                      <button
                        onClick={handleConnectGoogle}
                        disabled={!agreementReady}
                        className="px-4 py-2 bg-brand-ink text-white font-sans text-xs font-semibold rounded-lg hover:opacity-90 transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        Connect
                      </button>
                    )}
                  </div>
                  <p className="text-brand-ink-2 font-sans text-xs leading-relaxed">
                    Required: Read directory users, read Gmail, read Google Drive, read calendar.
                  </p>
                </div>
              </div>

              <div className="flex gap-3">
                <button
                  onClick={() => advanceStep(0)}
                  className="flex-1 py-2.5 px-4 border border-brand-line text-brand-ink font-sans text-sm font-medium rounded-xl hover:bg-brand-bg-soft transition-colors"
                >
                  Back
                </button>
                <button
                  onClick={() => handleSyncUsers()}
                  disabled={syncing || !hasIntegration || !agreementReady}
                  className="flex-1 py-2.5 px-4 bg-brand-ink text-white font-sans text-sm font-semibold rounded-xl hover:opacity-90 transition-opacity disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {syncing ? 'Syncing...' : 'Sync Users'}
                </button>
              </div>
            </div>
          )}

          {/* Step 2: Syncing */}
          {step === 2 && (
            <div className="bg-brand-surface border border-brand-line rounded-2xl p-8 shadow-sm text-center">
              <div className="inline-block w-12 h-12 border-4 border-brand-ink border-t-transparent rounded-full animate-spin mb-5" />
              <h2 className="text-brand-ink font-sans text-lg font-bold mb-2">Syncing Users</h2>
              <p className="text-brand-ink-2 font-sans text-sm leading-relaxed mb-6">
                Pulling users from your connected directory. This may take a moment.
              </p>
              <button
                onClick={() => advanceStep(3)}
                className="py-2.5 px-6 bg-brand-ink text-white font-sans text-sm font-semibold rounded-xl hover:opacity-90 transition-opacity"
              >
                Continue to Review
              </button>
            </div>
          )}

          {/* Step 3: Review */}
          {step === 3 && (
            <div className="bg-brand-surface border border-brand-line rounded-2xl p-8 shadow-sm">
              <h2 className="text-brand-ink font-sans text-lg font-bold mb-1">Review Imported Users</h2>
              <p className="text-brand-ink-2 font-sans text-sm mb-6">
                {totalSynced > 0
                  ? `${totalSynced} users were imported from your directory.`
                  : 'No users were imported yet. You can sync again from the Admin panel later.'}
              </p>

              <div className="space-y-2 mb-6">
                {msConnected && (
                  <div className="flex items-center justify-between px-4 py-3 bg-brand-bg rounded-xl">
                    <span className="text-brand-ink font-sans text-sm">Microsoft 365 users synced</span>
                    <span className="text-brand-ink font-sans text-sm font-bold">{syncedUsers.microsoft || 0}</span>
                  </div>
                )}
                {googleConnected && (
                  <div className="flex items-center justify-between px-4 py-3 bg-brand-bg rounded-xl">
                    <span className="text-brand-ink font-sans text-sm">Google Workspace users synced</span>
                    <span className="text-brand-ink font-sans text-sm font-bold">{syncedUsers.google || 0}</span>
                  </div>
                )}
              </div>

              <div className="flex gap-3">
                <button
                  onClick={() => advanceStep(1)}
                  className="flex-1 py-2.5 px-4 border border-brand-line text-brand-ink font-sans text-sm font-medium rounded-xl hover:bg-brand-bg-soft transition-colors"
                >
                  Back
                </button>
                <button
                  onClick={handleComplete}
                  disabled={completing}
                  className="flex-1 py-2.5 px-4 bg-brand-ink text-white font-sans text-sm font-semibold rounded-xl hover:opacity-90 transition-opacity disabled:opacity-40"
                >
                  {completing ? 'Completing...' : 'Complete Setup'}
                </button>
              </div>
            </div>
          )}

          {/* Step 4: Complete (shown briefly before redirect) */}
          {step === 4 && (
            <div className="bg-brand-surface border border-brand-line rounded-2xl p-8 shadow-sm text-center">
              <div className="inline-flex items-center justify-center w-14 h-14 rounded-full bg-green-100 mb-4">
                <svg width="28" height="28" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <path d="M5 13l4 4L19 7" stroke="#16a34a" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </div>
              <h2 className="text-brand-ink font-sans text-xl font-bold mb-2">Setup Complete!</h2>
              <p className="text-brand-ink-2 font-sans text-sm leading-relaxed mb-6">
                Your firm is ready. You can manage users, licenses, and integrations
                from the Admin panel.
              </p>
              <button
                onClick={() => navigate('/admin', { replace: true })}
                className="py-3 px-8 bg-brand-ink text-white font-sans text-sm font-semibold rounded-xl hover:opacity-90 transition-opacity"
              >
                Go to Admin Panel
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
