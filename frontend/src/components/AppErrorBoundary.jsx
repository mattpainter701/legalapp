import React from 'react'

export default class AppErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('Uncaught workspace render failure', error, info)
  }

  render() {
    if (!this.state.error) return this.props.children

    return (
      <main className="flex min-h-screen items-center justify-center bg-brand-bg p-6">
        <div role="alert" className="w-full max-w-lg rounded-2xl border border-brand-line bg-white p-8 text-center shadow-xl">
          <p className="text-xs font-bold uppercase tracking-[0.18em] text-brand-accent">Workspace recovery</p>
          <h1 className="mt-3 font-serif text-2xl font-bold text-brand-ink">LawHand could not finish rendering this page</h1>
          <p className="mt-3 text-sm leading-6 text-brand-muted">The page stopped before it could finish rendering. Reload the workspace, or return to sign in if the problem continues.</p>
          <div className="mt-6 flex flex-wrap justify-center gap-3">
            <button type="button" onClick={() => window.location.reload()} className="rounded-lg bg-brand-ink px-4 py-2 text-sm font-semibold text-white">Reload workspace</button>
            <a href="/login" className="rounded-lg border border-brand-line px-4 py-2 text-sm font-semibold text-brand-ink">Return to sign in</a>
          </div>
        </div>
      </main>
    )
  }
}
