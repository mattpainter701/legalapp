import { useState, useEffect } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { acceptClientPortalInvite } from '../api'
import { ShieldCheck, AlertTriangle, Check } from 'lucide-react'

export default function ClientPortalAcceptPage() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const token = searchParams.get('token')
  const [status, setStatus] = useState('loading')
  const [errorMsg, setErrorMsg] = useState('')

  useEffect(() => {
    if (!token) {
      setStatus('error')
      setErrorMsg('No invitation token provided. Please use the link from your invitation email.')
      return
    }
    let cancelled = false
    acceptClientPortalInvite(token)
      .then(() => {
        if (cancelled) return
        setStatus('success')
        setTimeout(() => navigate('/portal/client/matter', { replace: true }), 1200)
      })
      .catch((err) => {
        if (cancelled) return
        const detail = err?.response?.data?.detail
        if (err?.response?.status === 410) {
          setErrorMsg('This invitation has expired. Please contact your legal team for a new link.')
        } else if (err?.response?.status === 404) {
          setErrorMsg('Invitation not found. It may have been revoked or already used.')
        } else {
          setErrorMsg(detail || 'Failed to accept invitation. Please try again or contact your legal team.')
        }
        setStatus('error')
      })
    return () => { cancelled = true }
  }, [token, navigate])

  return (
    <div className="min-h-screen bg-brand-bg flex items-center justify-center px-4">
      <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm max-w-md w-full p-10 text-center">
        <ShieldCheck size={48} className="mx-auto text-brand-accent mb-6" strokeWidth={1.5} />
        {status === 'loading' && (
          <>
            <div className="w-8 h-8 border-2 border-brand-ink border-t-transparent rounded-full animate-spin mx-auto mb-4" />
            <h1 className="font-serif font-bold text-2xl text-brand-ink mb-2">Accepting Invitation</h1>
            <p className="text-brand-ink-2 font-sans text-sm">Verifying your invitation token…</p>
          </>
        )}
        {status === 'success' && (
          <>
            <div className="w-12 h-12 bg-brand-green/10 rounded-full flex items-center justify-center mx-auto mb-4">
              <Check size={24} className="text-brand-green" />
            </div>
            <h1 className="font-serif font-bold text-2xl text-brand-ink mb-2">Welcome!</h1>
            <p className="text-brand-ink-2 font-sans text-sm">Opening your client portal…</p>
          </>
        )}
        {status === 'error' && (
          <>
            <div className="w-12 h-12 bg-brand-rose/10 rounded-full flex items-center justify-center mx-auto mb-4">
              <AlertTriangle size={24} className="text-brand-rose" />
            </div>
            <h1 className="font-serif font-bold text-2xl text-brand-ink mb-2">Unable to Open Portal</h1>
            <p className="text-brand-ink-2 font-sans text-sm leading-relaxed mb-6">{errorMsg}</p>
          </>
        )}
      </div>
    </div>
  )
}
