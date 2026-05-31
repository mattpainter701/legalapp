import React, { useEffect, useRef } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useAuth } from '../App'

export default function AuthCallback() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const { login } = useAuth()
  const handled = useRef(false)

  useEffect(() => {
    if (handled.current) return
    handled.current = true

    const token = searchParams.get('token')
    if (!token) {
      navigate('/login', { replace: true })
      return
    }

    login(token)
      .then((user) => {
        if (user && user.role === 'admin') {
          navigate('/chat', { replace: true })
        } else {
          navigate('/chat', { replace: true })
        }
      })
      .catch(() => {
        navigate('/login', { replace: true })
      })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="flex items-center justify-center h-screen bg-[#1e3a5f]">
      <div className="text-center">
        <div className="inline-block w-10 h-10 border-4 border-white border-t-transparent rounded-full animate-spin mb-4" />
        <p className="text-white font-sans text-sm">Completing sign-in...</p>
      </div>
    </div>
  )
}
