import { useEffect, useMemo, useState } from 'react'
import { getAppVersion } from '../api'

const FALLBACK_VERSION = import.meta.env.VITE_APP_VERSION || 'dev'

export default function VersionBadge() {
  const [version, setVersion] = useState(null)

  useEffect(() => {
    let mounted = true
    getAppVersion()
      .then((data) => {
        if (mounted) setVersion(data)
      })
      .catch(() => {
        if (mounted) setVersion({ version: FALLBACK_VERSION })
      })
    return () => {
      mounted = false
    }
  }, [])

  const label = useMemo(() => {
    const short = version?.short_commit || version?.version || FALLBACK_VERSION
    return `v ${short}`
  }, [version])

  const title = useMemo(() => {
    if (!version) return `Version ${FALLBACK_VERSION}`
    const parts = [
      `Version: ${version.version || FALLBACK_VERSION}`,
      version.commit ? `Commit: ${version.commit}` : '',
      version.build_time ? `Built: ${version.build_time}` : '',
    ].filter(Boolean)
    return parts.join('\n')
  }, [version])

  return (
    <div
      className="fixed bottom-2 right-2 z-[60] rounded border border-brand-line bg-brand-surface/90 px-2 py-1 font-mono text-[10px] leading-none text-brand-muted shadow-sm backdrop-blur print:hidden"
      title={title}
      aria-label={title}
    >
      {label}
    </div>
  )
}
