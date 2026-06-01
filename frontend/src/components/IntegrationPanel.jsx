import React, { useState } from 'react'
import { Cloud, CheckCircle2, AlertCircle, Loader2, Plus, X } from 'lucide-react'

const INTEGRATIONS = [
  {
    id: 'google_drive',
    name: 'Google Drive',
    icon: '🔍',
    color: 'text-blue-600',
    description: 'Access files from your Google Drive',
  },
  {
    id: 'onedrive',
    name: 'OneDrive',
    icon: '☁️',
    color: 'text-blue-500',
    description: 'Access files from your Microsoft OneDrive',
  },
  {
    id: 'sharepoint',
    name: 'SharePoint',
    icon: '📁',
    color: 'text-emerald-600',
    description: 'Access files from SharePoint sites',
  },
]

export default function IntegrationPanel({
  integrationStatus = {},
  onConnect,
  onDisconnect,
  isLoading = false,
}) {
  const [expandedId, setExpandedId] = useState(null)

  const getStatus = (integrationId) => {
    const status = integrationStatus[integrationId]
    return {
      isConnected: status?.connected || false,
      fileCount: status?.fileCount || 0,
      lastSync: status?.lastSync,
      error: status?.error,
    }
  }

  return (
    <div className="bg-brand-surface border border-brand-line rounded-lg overflow-hidden">
      <div className="px-4 py-3 border-b border-brand-line bg-brand-surface-2 flex items-center gap-2">
        <Cloud className="w-4 h-4 text-brand-accent" />
        <h3 className="text-sm font-semibold text-brand-ink">Cloud Integrations</h3>
      </div>

      <div className="divide-y divide-brand-line">
        {INTEGRATIONS.map((integration) => {
          const status = getStatus(integration.id)
          const isExpanded = expandedId === integration.id

          return (
            <div key={integration.id} className="p-4">
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-3 flex-1">
                  <span className="text-xl">{integration.icon}</span>
                  <div className="flex-1">
                    <h4 className="font-medium text-sm text-brand-ink">{integration.name}</h4>
                    <p className="text-xs text-brand-muted">{integration.description}</p>
                  </div>
                </div>

                {status.isConnected ? (
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-brand-muted">
                      {status.fileCount} files
                    </span>
                    <CheckCircle2 className="w-4 h-4 text-brand-green" />
                  </div>
                ) : (
                  <AlertCircle className="w-4 h-4 text-brand-muted" />
                )}
              </div>

              {status.error && (
                <p className="text-xs text-brand-rose mb-2">{status.error}</p>
              )}

              {status.lastSync && (
                <p className="text-xs text-brand-muted mb-3">
                  Last synced: {new Date(status.lastSync).toLocaleDateString()}
                </p>
              )}

              <div className="flex gap-2">
                {!status.isConnected ? (
                  <button
                    onClick={() => onConnect?.(integration.id)}
                    disabled={isLoading}
                    className="flex items-center gap-1 text-xs font-medium px-3 py-1.5 bg-brand-accent text-white hover:bg-brand-accent-2 disabled:opacity-50 transition-colors"
                  >
                    {isLoading ? (
                      <Loader2 className="w-3 h-3 animate-spin" />
                    ) : (
                      <Plus className="w-3 h-3" />
                    )}
                    Connect
                  </button>
                ) : (
                  <>
                    <button
                      onClick={() => setExpandedId(isExpanded ? null : integration.id)}
                      className="flex items-center gap-1 text-xs font-medium px-3 py-1.5 bg-brand-surface-2 text-brand-ink hover:bg-brand-line/40 transition-colors"
                    >
                      {isExpanded ? 'Hide' : 'Show'} files
                    </button>
                    <button
                      onClick={() => onDisconnect?.(integration.id)}
                      className="flex items-center gap-1 text-xs font-medium px-3 py-1.5 text-brand-rose hover:bg-brand-rose/10 transition-colors"
                    >
                      <X className="w-3 h-3" />
                      Disconnect
                    </button>
                  </>
                )}
              </div>

              {/* Expanded file list */}
              {isExpanded && status.isConnected && (
                <div className="mt-3 pt-3 border-t border-brand-line">
                  <p className="text-xs text-brand-muted mb-2">
                    {status.fileCount === 0
                      ? 'No legal documents found'
                      : `${status.fileCount} legal documents available`}
                  </p>
                  {status.fileCount > 0 && (
                    <div className="bg-brand-bg rounded p-2 max-h-32 overflow-y-auto text-xs text-brand-ink-2">
                      <p className="text-brand-muted italic">
                        File list loading... (will show previews in next iteration)
                      </p>
                    </div>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>

      <div className="px-4 py-3 bg-brand-surface-2 text-xs text-brand-muted border-t border-brand-line">
        Documents from connected drives are available in the upload panel. Refresh to sync latest files.
      </div>
    </div>
  )
}
