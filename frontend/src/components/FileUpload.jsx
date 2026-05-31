import React, { useState, useCallback } from 'react'
import { useDropzone } from 'react-dropzone'
import { uploadDocument } from '../api'

const ACCEPTED_TYPES = {
  'application/pdf': ['.pdf'],
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
  'text/plain': ['.txt'],
}

function FileStatusIcon({ status }) {
  if (status === 'uploading') {
    return (
      <div className="w-4 h-4 border-2 border-[#1e3a5f] border-t-transparent rounded-full animate-spin" />
    )
  }
  if (status === 'processing') {
    return (
      <div className="w-4 h-4 border-2 border-yellow-500 border-t-transparent rounded-full animate-spin" />
    )
  }
  if (status === 'done' || status === 'indexed') {
    return (
      <svg className="w-4 h-4 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
      </svg>
    )
  }
  if (status === 'error') {
    return (
      <svg className="w-4 h-4 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
      </svg>
    )
  }
  return null
}

export default function FileUpload({ onUploadComplete }) {
  const [uploads, setUploads] = useState([])

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

          // Poll for completion
          let attempts = 0
          const poll = setInterval(async () => {
            attempts++
            if (attempts > 30) {
              clearInterval(poll)
              return
            }
            try {
              const { getDocuments } = await import('../api')
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

  return (
    <div>
      <div
        {...getRootProps()}
        className={`border-2 border-dashed rounded-lg p-3 text-center cursor-pointer transition-colors duration-150 ${
          isDragActive
            ? 'border-[#1e3a5f] bg-blue-50'
            : 'border-gray-300 hover:border-[#1e3a5f] hover:bg-gray-50'
        }`}
      >
        <input {...getInputProps()} />
        <svg
          className="w-5 h-5 text-gray-400 mx-auto mb-1"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.5}
            d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"
          />
        </svg>
        <p className="text-xs text-gray-500">
          {isDragActive ? 'Drop files here' : 'Upload PDF, DOCX, or TXT'}
        </p>
      </div>

      {uploads.length > 0 && (
        <div className="mt-2 space-y-1">
          {uploads.map((u) => (
            <div
              key={u.id}
              className="flex items-center gap-2 text-xs bg-gray-50 rounded px-2 py-1.5"
            >
              <FileStatusIcon status={u.status} />
              <span className="flex-1 truncate text-gray-700">{u.name}</span>
              <span
                className={`text-xs capitalize ${
                  u.status === 'error'
                    ? 'text-red-500'
                    : u.status === 'indexed' || u.status === 'done'
                    ? 'text-green-500'
                    : 'text-yellow-600'
                }`}
              >
                {u.status === 'error' ? u.error || 'Error' : u.status}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
