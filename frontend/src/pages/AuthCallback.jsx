import React, { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams, Link } from 'react-router-dom'
import { useAuth } from '../App'
import { exchangeOAuthCode, getOnboardingStatus } from '../api'

export default function AuthCallback() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const { login } = useAuth()
  const handled = useRef(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (handled.current) return
    handled.current = true

    const code = searchParams.get('code')
    if (!code) {
      setError('No authentication code received. Please try signing in again.')
      return
    }

    exchangeOAuthCode(code)
      .then((result) => login(result.access_token))
      .then((userObj) => {
        // If admin and onboarding not complete, redirect to wizard
        if (userObj?.role === 'admin') {
          getOnboardingStatus()
            .then((s) => {
              if (!s.onboarding_completed) {
                navigate('/onboarding', { replace: true })
              } else {
                navigate('/chat', { replace: true })
              }
            })
            .catch(() => navigate('/chat', { replace: true }))
        } else {
          navigate('/chat', { replace: true })
        }
      })
      .catch(() => {
        setError('Sign-in failed. Please try again.')
      })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="flex items-center justify-center h-screen bg-brand-bg relative overflow-hidden">
      {/* Background grain/texture */}
      <div
        className="absolute inset-0 opacity-[0.03] pointer-events-none"
        style={{ backgroundImage: 'url("data:image/svg+xml,%3Csvg viewBox=%220 0 200 200%22 xmlns=%22http://www.w3.org/2000/svg%22%3E%3Cfilter id=%22noiseFilter%22%3E%3CfeTurbulence type=%22fractalNoise%22 baseFrequency=%220.65%22 numOctaves=%223%22 stitchTiles=%22stitch%22/%3E%3C/filter%3E%3Crect width=%22100%25%22 height=%22100%25%22 filter=%22url(%23noiseFilter)%22/%3E%3C/svg%3E")' }}
      ></div>

      <div className="text-center relative z-10 px-4">
        {error ? (
          <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-xl px-10 py-10 max-w-sm mx-auto">
            <div className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-red-50 border border-red-200 mb-4">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" stroke="#dc2626" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </div>
            <p className="text-brand-ink font-sans text-sm font-medium mb-1">Authentication error</p>
            <p className="text-brand-ink-2 font-sans text-xs mb-6 leading-relaxed">{error}</p>
            <Link
              to="/login"
              className="inline-flex items-center justify-center px-5 py-2.5 rounded-xl bg-brand-surface text-brand-ink font-sans text-sm font-medium border border-brand-line hover:border-brand-ink hover:bg-brand-bg-soft transition-all duration-200 shadow-sm"
            >
              Back to sign in
            </Link>
          </div>
        ) : (
          <>
            <div className="inline-block w-10 h-10 border-4 border-brand-ink border-t-transparent rounded-full animate-spin mb-4 shadow-sm" />
            <p className="text-brand-ink font-sans text-sm font-medium">Completing sign-in...</p>
          </>
        )}
      </div>
    </div>
  )
}
