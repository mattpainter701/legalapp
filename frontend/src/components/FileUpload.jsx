import React, { useState, useCallback } from 'react'
import { useDropzone } from 'react-dropzone'
import { uploadDocument, getDocuments } from '../api'
import { FileUp, FileText, Check, AlertCircle, Loader2, Cloud } from 'lucide-react'
import ConnectedFilesModal from './ConnectedFilesModal'

const ACCEPTED_TYPES = {
  'application/pdf': ['.pdf'],
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
  'text/plain': ['.txt'],
}

function FileStatusIcon({ status }) {
  if (status === 'uploading') {
    return <Loader2 size={16} className="text-blue-500 animate-spin" />
  }
  if (status === 'processing') {
    return <Loader2 size={16} className="text-orange-500 animate-spin" />
  }
  if (status === 'done' || status === 'indexed') {
    return <Check size={16} className="text-brand-green" strokeWidth={3} />
  }
  if (status === 'error') {
    return <AlertCircle size={16} className="text-brand-rose" />
  }
  return <FileText size={16} className="text-brand-muted" />
}

export default function FileUpload({ onUploadComplete, showCloudIntegration = true }) {
  const [uploads, setUploads] = useState([])
  const [activeTab, setActiveTab] = useState('local')
  const [showCloudModal, setShowCloudModal] = useState(false)

  const updateUpload = (id, patch) => {
    setUploads((prev) => prev.map((u) => (u.id === id ? { ...u, ...patch } : u)))
  }

  const onDrop = useCallback(
    async (acceptedFiles) => {
      for (const file of acceptedFiles) {
        const localId = `${Date.now()}-${file.name}`
        setUploads((prev) => [
          ...prev,
          { id: localId, name: file.name, status: 'uploading' },
        ])

        try {
          const doc = await uploadDocument(file)
          updateUpload(localId, { status: doc.status || 'processing', docId: doc.id })

          if (onUploadComplete) {
            onUploadComplete(doc)
          }

          let attempts = 0
          const poll = setInterval(async () => {
            attempts++
            if (attempts > 30) {
              clearInterval(poll)
              return
            }
            try {
              const docs = await getDocuments()
              const found = docs.find((d) => d.id === doc.id)
              if (found) {
                updateUpload(localId, { status: found.status })
                if (found.status === 'indexed' || found.status === 'done') {
                  clearInterval(poll)
                }
              }
            } catch {
              clearInterval(poll)
            }
          }, 3000)
        } catch (err) {
          updateUpload(localId, {
            status: 'error',
            error: err?.response?.data?.detail || 'Upload failed',
          })
        }
      }
    },
    [onUploadComplete]
  )

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: ACCEPTED_TYPES,
    maxSize: 50 * 1024 * 1024,
  })

  const handleCloudImport = async (file) => {
    const localId = `cloud-${Date.now()}-${file.name}`
    setUploads((prev) => [
      ...prev,
      { id: localId, name: file.name, status: 'importing' },
    ])

    try {
      // TODO: Implement cloud file import API call
      // const doc = await importFromDrive(file)
      updateUpload(localId, { status: 'processing' })
      setTimeout(() => {
        updateUpload(localId, { status: 'indexed' })
      }, 2000)
    } catch (err) {
      updateUpload(localId, {
        status: 'error',
        error: err?.message || 'Import failed',
      })
    }
  }

  return (
    <div>
      {/* Tabs */}
      {showCloudIntegration && (
        <div className="flex gap-1 mb-3">
          <button
            onClick={() => setActiveTab('local')}
            className={`px-3 py-1.5 text-xs font-medium rounded-t border-b-2 transition-colors ${
              activeTab === 'local'
                ? 'border-brand-accent text-brand-accent bg-brand-surface'
                : 'border-brand-line text-brand-muted hover:text-brand-ink'
            }`}
          >
            Local Upload
          </button>
          <button
            onClick={() => setActiveTab('cloud')}
            className={`px-3 py-1.5 text-xs font-medium rounded-t border-b-2 transition-colors flex items-center gap-1 ${
              activeTab === 'cloud'
                ? 'border-brand-accent text-brand-accent bg-brand-surface'
                : 'border-brand-line text-brand-muted hover:text-brand-ink'
            }`}
          >
            <Cloud size={12} /> Cloud Drives
          </button>
        </div>
      )}

      {/* Local upload tab */}
      {activeTab === 'local' && (
        <div>
          <div
            {...getRootProps()}
            className={`border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-all duration-200 ${
              isDragActive
                ? 'border-brand-accent bg-brand-green/5 scale-[1.02]'
                : 'border-brand-line hover:border-brand-accent hover:bg-brand-surface'
            }`}
          >
            <input {...getInputProps()} />
            <div
              className={`w-10 h-10 mx-auto rounded-full flex items-center justify-center mb-3 transition-colors ${
                isDragActive ? 'bg-brand-accent text-white' : 'bg-brand-bg-soft text-brand-muted'
              }`}
            >
              <FileUp size={20} />
            </div>
            <p className="text-[13px] font-sans font-medium text-brand-ink-2">
              {isDragActive ? 'Drop files to upload' : 'Click or drag files to upload'}
            </p>
            <p className="text-[11px] font-sans text-brand-muted mt-1 uppercase tracking-wide">
              PDF, DOCX, TXT up to 50MB
            </p>
          </div>

          {uploads.length > 0 && (
            <div className="mt-3 space-y-2">
              {uploads.map((u) => (
                <div
                  key={u.id}
                  className="flex items-center gap-3 bg-brand-surface border border-brand-line rounded-lg px-3 py-2.5 shadow-sm"
                >
                  <div className="shrink-0">
                    <FileStatusIcon status={u.status} />
                  </div>
                  <span className="flex-1 truncate text-[13px] font-sans font-medium text-brand-ink">
                    {u.name}
                  </span>
                  <span
                    className={`text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full border shrink-0 ${
                      u.status === 'error'
                        ? 'text-brand-rose bg-brand-rose/10 border-brand-rose/20'
                        : u.status === 'indexed' || u.status === 'done'
                        ? 'text-brand-green bg-brand-green/10 border-brand-green/20'
                        : 'text-orange-600 bg-orange-100 border-orange-200'
                    }`}
                  >
                    {u.status === 'error' ? u.error || 'Error' : u.status}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Cloud drives tab */}
      {showCloudIntegration && activeTab === 'cloud' && (
        <div className="text-center p-6 bg-brand-surface border border-brand-line rounded-lg">
          <Cloud className="w-10 h-10 mx-auto text-brand-muted mb-3 opacity-50" />
          <p className="text-sm text-brand-ink-2 mb-3">
            Browse and import documents from your connected cloud drives
          </p>
          <button
            onClick={() => setShowCloudModal(true)}
            className="px-4 py-2 bg-brand-accent text-white font-medium text-sm hover:bg-brand-accent-2 rounded transition-colors"
          >
            Browse Cloud Files
          </button>
        </div>
      )}

      {/* Cloud import modal */}
      <ConnectedFilesModal
        isOpen={showCloudModal}
        onClose={() => setShowCloudModal(false)}
        onImportFile={handleCloudImport}
        files={[]} // Will be populated from API
        isLoading={false}
      />
    </div>
  )
}
