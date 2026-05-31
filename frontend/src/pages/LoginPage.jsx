import React from 'react'
import { loginMicrosoft, loginGoogle } from '../api'

function MicrosoftIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 21 21" xmlns="http://www.w3.org/2000/svg">
      <rect x="1" y="1" width="9" height="9" fill="#f25022" />
      <rect x="11" y="1" width="9" height="9" fill="#7fba00" />
      <rect x="1" y="11" width="9" height="9" fill="#00a4ef" />
      <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
    </svg>
  )
}

function GoogleIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
      <path
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
        fill="#4285F4"
      />
      <path
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
        fill="#34A853"
      />
      <path
        d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
        fill="#FBBC05"
      />
      <path
        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
        fill="#EA4335"
      />
    </svg>
  )
}

export default function LoginPage() {
  return (
    <div className="min-h-screen bg-[#1e3a5f] flex flex-col items-center justify-center px-4">
      {/* Background pattern */}
      <div className="absolute inset-0 opacity-5 pointer-events-none">
        <div
          className="w-full h-full"
          style={{
            backgroundImage: `repeating-linear-gradient(
              45deg,
              #fff,
              #fff 1px,
              transparent 1px,
              transparent 60px
            )`,
          }}
        />
      </div>

      {/* Card */}
      <div className="relative z-10 bg-white rounded-xl shadow-2xl w-full max-w-md px-8 py-10">
        {/* Logo / Branding */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 bg-[#1e3a5f] rounded-full mb-4">
            <svg
              width="32"
              height="32"
              viewBox="0 0 32 32"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
            >
              <path
                d="M16 4L6 8v8c0 5.55 4.27 10.74 10 12 5.73-1.26 10-6.45 10-12V8L16 4z"
                fill="white"
                fillOpacity="0.9"
              />
              <path
                d="M13 15l2 2 4-4"
                stroke="#1e3a5f"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </div>
          <h1 className="text-2xl font-bold text-[#1e3a5f] font-serif tracking-tight">
            LegalScribe AI
          </h1>
          <p className="mt-2 text-gray-500 text-sm leading-relaxed">
            AI-powered legal research and drafting assistant
          </p>
        </div>

        {/* Divider */}
        <div className="border-t border-gray-100 mb-6" />

        {/* Sign-in buttons */}
        <div className="space-y-3">
          <button
            onClick={loginMicrosoft}
            className="w-full flex items-center justify-center gap-3 px-4 py-3 rounded-lg text-white font-sans text-sm font-medium transition-all duration-150 hover:opacity-90 active:opacity-80 shadow-sm"
            style={{ backgroundColor: '#0078d4' }}
          >
            <MicrosoftIcon />
            Sign in with Microsoft
          </button>

          <button
            onClick={loginGoogle}
            className="w-full flex items-center justify-center gap-3 px-4 py-3 rounded-lg bg-white text-gray-700 font-sans text-sm font-medium border border-gray-300 transition-all duration-150 hover:bg-gray-50 active:bg-gray-100 shadow-sm"
          >
            <GoogleIcon />
            Sign in with Google
          </button>
        </div>

        {/* Info text */}
        <p className="mt-6 text-xs text-gray-400 text-center leading-relaxed">
          By signing in, you agree to our Terms of Service and Privacy Policy. Your firm's
          data is isolated and never shared.
        </p>
      </div>

      {/* Footer */}
      <div className="relative z-10 mt-8 text-center">
        <p className="text-[#9eb8d5] text-sm font-sans tracking-wide">
          Secure. Private. Accurate.
        </p>
      </div>
    </div>
  )
}
