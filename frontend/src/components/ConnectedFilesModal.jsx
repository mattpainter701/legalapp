import React, { useState, useEffect } from 'react'
import { X, Search, File, Download, AlertCircle, Loader2 } from 'lucide-react'

export default function ConnectedFilesModal({
  isOpen,
  onClose,
  onImportFile,
  files = [],
  isLoading = false,
  error = null,
}) {
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedDrive, setSelectedDrive] = useState('all')
  const [importingFileId, setImportingFileId] = useState(null)

  const drives = ['all', 'google_drive', 'onedrive', 'sharepoint']
  const driveNames = {
    all: 'All Drives',
    google_drive: 'Google Drive',
    onedrive: 'OneDrive',
    sharepoint: 'SharePoint',
  }

  const filteredFiles = files.filter((file) => {
    const matchesSearch = file.name.toLowerCase().includes(searchQuery.toLowerCase())
    const matchesDrive = selectedDrive === 'all' || file.drive === selectedDrive
    return matchesSearch && matchesDrive
  })

  const handleImportFile = async (file) => {
    setImportingFileId(file.id)
    try {
      await onImportFile?.(file)
    } finally {
      setImportingFileId(null)
    }
  }

  const formatFileSize = (bytes) => {
    if (!bytes) return '—'
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-brand-bg border border-brand-line rounded-lg max-w-2xl w-full max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="px-6 py-4 border-b border-brand-line flex items-center justify-between bg-brand-surface">
          <h2 className="font-serif text-xl font-semibold text-brand-ink">
            Import from Cloud Drives
          </h2>
          <button
            onClick={onClose}
            className="text-brand-muted hover:text-brand-ink transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Filters */}
        <div className="px-6 py-4 border-b border-brand-line space-y-4 bg-brand-surface-2">
          {/* Search */}
          <div className="relative">
            <Search className="absolute left-3 top-3 w-4 h-4 text-brand-muted pointer-events-none" />
            <input
              type="text"
              placeholder="Search documents..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-10 pr-4 py-2 bg-brand-bg border border-brand-line rounded text-sm text-brand-ink placeholder-brand-muted focus:outline-none focus:ring-1 focus:ring-brand-ink"
            />
          </div>

          {/* Drive filter */}
          <div className="flex gap-2 flex-wrap">
            {drives.map((drive) => (
              <button
                key={drive}
                onClick={() => setSelectedDrive(drive)}
                className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                  selectedDrive === drive
                    ? 'bg-brand-accent text-white'
                    : 'bg-brand-surface border border-brand-line text-brand-ink hover:border-brand-accent'
                }`}
              >
                {driveNames[drive]}
              </button>
            ))}
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {error && (
            <div className="mb-4 p-3 bg-brand-rose/10 border border-brand-rose/20 rounded flex items-start gap-2">
              <AlertCircle className="w-4 h-4 text-brand-rose flex-shrink-0 mt-0.5" />
              <p className="text-sm text-brand-rose">{error}</p>
            </div>
          )}

          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-6 h-6 animate-spin text-brand-accent" />
            </div>
          ) : filteredFiles.length === 0 ? (
            <div className="text-center py-12">
              <File className="w-12 h-12 text-brand-muted mx-auto mb-3 opacity-30" />
              <p className="text-brand-ink-2 text-sm">
                {files.length === 0
                  ? 'No connected drives or no documents found'
                  : 'No matching documents'}
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              {filteredFiles.map((file) => (
                <div
                  key={file.id}
                  className="p-3 bg-brand-surface border border-brand-line rounded hover:border-brand-accent transition-colors flex items-center justify-between group"
                >
                  <div className="flex items-center gap-3 flex-1 min-w-0">
                    <File className="w-4 h-4 text-brand-muted flex-shrink-0" />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-brand-ink truncate">
                        {file.name}
                      </p>
                      <p className="text-xs text-brand-muted">
                        {formatFileSize(file.size)} • {file.drive}
                        {file.modified && ` • ${new Date(file.modified).toLocaleDateString()}`}
                      </p>
                    </div>
                  </div>
                  <button
                    onClick={() => handleImportFile(file)}
                    disabled={importingFileId === file.id}
                    className="ml-3 flex items-center gap-1 px-3 py-1 text-xs font-medium bg-brand-accent text-white hover:bg-brand-accent-2 disabled:opacity-50 transition-colors flex-shrink-0 rounded"
                  >
                    {importingFileId === file.id ? (
                      <>
                        <Loader2 className="w-3 h-3 animate-spin" />
                        Importing...
                      </>
                    ) : (
                      <>
                        <Download className="w-3 h-3" />
                        Import
                      </>
                    )}
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-3 border-t border-brand-line bg-brand-surface-2 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-brand-ink bg-brand-surface border border-brand-line hover:bg-brand-line/40 rounded transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
