import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { format } from 'date-fns'
import FileUpload from './FileUpload'
import { deleteDocument, deleteConversation } from '../api'

function ConversationItem({ conv, isActive, onClick, onDelete }) {
  const [hover, setHover] = useState(false)

  return (
    <div
      className={`sidebar-item ${isActive ? 'sidebar-item-active' : 'sidebar-item-inactive'}`}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <button
        onClick={onClick}
        className="flex-1 text-left truncate text-sm"
        title={conv.title || 'Untitled conversation'}
      >
        {conv.title || 'Untitled conversation'}
      </button>
      {hover && !isActive && (
        <button
          onClick={(e) => {
            e.stopPropagation()
            onDelete(conv.id)
          }}
          className="ml-1 p-0.5 rounded hover:bg-red-100 text-gray-400 hover:text-red-500 transition-colors"
          title="Delete conversation"
        >
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
            />
          </svg>
        </button>
      )}
      {isActive && hover && (
        <button
          onClick={(e) => {
            e.stopPropagation()
            onDelete(conv.id)
          }}
          className="ml-1 p-0.5 rounded hover:bg-[#2e4f7a] text-white/70 hover:text-white transition-colors"
          title="Delete conversation"
        >
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
            />
          </svg>
        </button>
      )}
    </div>
  )
}

function DocumentItem({ doc, onDelete }) {
  const statusColor = {
    indexed: 'text-green-600',
    processing: 'text-yellow-600',
    uploading: 'text-blue-600',
    error: 'text-red-600',
  }[doc.status] || 'text-gray-500'

  return (
    <div className="flex items-center gap-2 px-3 py-1.5 rounded hover:bg-gray-50 group">
      <svg
        className="w-3.5 h-3.5 text-gray-400 flex-shrink-0"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={1.5}
          d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
        />
      </svg>
      <span className="flex-1 text-xs text-gray-700 truncate" title={doc.filename}>
        {doc.filename}
      </span>
      <span className={`text-xs capitalize ${statusColor} hidden group-hover:block`}>
        {doc.status}
      </span>
      <button
        onClick={() => onDelete(doc.id)}
        className="hidden group-hover:block p-0.5 rounded hover:bg-red-100 text-gray-400 hover:text-red-500 transition-colors"
        title="Delete document"
      >
        <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M6 18L18 6M6 6l12 12"
          />
        </svg>
      </button>
    </div>
  )
}

export default function Sidebar({
  conversations,
  activeConvId,
  onSelectConversation,
  onNewConversation,
  onConversationDeleted,
  documents,
  onDocumentUploaded,
  onDocumentDeleted,
  user,
  onLogout,
}) {
  const navigate = useNavigate()

  const handleDeleteConv = async (id) => {
    try {
      await deleteConversation(id)
      onConversationDeleted(id)
    } catch (err) {
      console.error('Failed to delete conversation', err)
    }
  }

  const handleDeleteDoc = async (id) => {
    try {
      await deleteDocument(id)
      onDocumentDeleted(id)
    } catch (err) {
      console.error('Failed to delete document', err)
    }
  }

  return (
    <div className="w-64 flex-shrink-0 bg-[#f8f9fc] border-r border-gray-200 flex flex-col h-full">
      {/* Header */}
      <div className="px-4 py-4 border-b border-gray-200">
        <div className="flex items-center gap-2 mb-3">
          <div className="w-7 h-7 bg-[#1e3a5f] rounded-full flex items-center justify-center">
            <svg width="14" height="14" viewBox="0 0 32 32" fill="none">
              <path
                d="M16 4L6 8v8c0 5.55 4.27 10.74 10 12 5.73-1.26 10-6.45 10-12V8L16 4z"
                fill="white"
                fillOpacity="0.9"
              />
            </svg>
          </div>
          <span className="font-serif font-bold text-[#1e3a5f] text-sm">LegalScribe AI</span>
        </div>
        <button
          onClick={onNewConversation}
          className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-[#1e3a5f] text-white text-sm rounded-lg hover:bg-[#2e4f7a] transition-colors font-sans"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
          </svg>
          New Conversation
        </button>
        <button
          onClick={() => navigate('/plugins')}
          className="w-full flex items-center justify-center gap-2 px-3 py-2 mt-2 bg-white border border-[#1e3a5f] text-[#1e3a5f] text-sm rounded-lg hover:bg-blue-50 transition-colors font-sans"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
          </svg>
          Practice Plugins
        </button>
      </div>

      {/* Conversations */}
      <div className="flex-1 overflow-y-auto">
        <div className="px-2 py-2">
          <p className="px-2 text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1 font-sans">
            Conversations
          </p>
          {conversations.length === 0 ? (
            <p className="px-3 py-2 text-xs text-gray-400 italic">No conversations yet</p>
          ) : (
            conversations.map((conv) => (
              <ConversationItem
                key={conv.id}
                conv={conv}
                isActive={conv.id === activeConvId}
                onClick={() => onSelectConversation(conv.id)}
                onDelete={handleDeleteConv}
              />
            ))
          )}
        </div>

        {/* Documents */}
        <div className="px-2 py-2 border-t border-gray-200 mt-2">
          <p className="px-2 text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1 font-sans">
            Documents
          </p>
          {documents.length === 0 ? (
            <p className="px-3 py-1 text-xs text-gray-400 italic">No documents uploaded</p>
          ) : (
            documents.map((doc) => (
              <DocumentItem key={doc.id} doc={doc} onDelete={handleDeleteDoc} />
            ))
          )}
        </div>
      </div>

      {/* Upload area */}
      <div className="px-3 py-3 border-t border-gray-200">
        <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2 font-sans">
          Upload Document
        </p>
        <FileUpload onUploadComplete={onDocumentUploaded} />
      </div>

      {/* User info */}
      <div className="px-3 py-3 border-t border-gray-200">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 bg-gray-200 rounded-full flex items-center justify-center flex-shrink-0">
            <span className="text-xs font-semibold text-gray-600 font-sans">
              {user?.full_name?.[0]?.toUpperCase() || user?.email?.[0]?.toUpperCase() || 'U'}
            </span>
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-xs font-medium text-gray-700 truncate">
              {user?.full_name || user?.email}
            </p>
            <p className="text-xs text-gray-400 capitalize">{user?.billing_tier || 'free'}</p>
          </div>
          <button
            onClick={onLogout}
            className="p-1 rounded hover:bg-gray-100 text-gray-400 hover:text-gray-600 transition-colors"
            title="Sign out"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"
              />
            </svg>
          </button>
        </div>
      </div>
    </div>
  )
}
