import { useState, useEffect, useCallback, useId, useMemo, useRef } from 'react'
import { useDropzone } from 'react-dropzone'
import PrepareFormWorkspace from '../components/templates/PrepareFormWorkspace'
import {
  getTemplates,
  analyzeTemplateUpload,
  proposeTemplateFieldsWithAi,
  createTemplate,
  createTemplateFromUpload,
  updateTemplate,
  deleteTemplate,
  renderTemplate,
  renderTemplateFile,
  getMattersV2,
  discoverTemplateVariables,
  getMatterDocumentDownloadUrl,
  triggerBlobDownload,
} from '../api'
import {
  FileText,
  Plus,
  Pencil,
  Trash2,
  X,
  Send,
  Eye,
  Sparkles,
  Check,
  ClipboardList,
  Search,
  Wand2,
  Upload,
  Download,
} from 'lucide-react'

const CATEGORY_COLORS = {
  engagement_letter: 'bg-blue-100 text-blue-800',
  retainer: 'bg-purple-100 text-purple-800',
  NDA: 'bg-amber-100 text-amber-800',
  motion: 'bg-rose-100 text-rose-800',
  other: 'bg-gray-100 text-gray-800',
}

const CATEGORY_LABELS = {
  engagement_letter: 'Engagement Letter',
  retainer: 'Retainer',
  NDA: 'NDA',
  motion: 'Motion',
  other: 'Other',
}

const TABS = [
  { key: 'templates', label: 'Templates', icon: FileText },
  { key: 'generate', label: 'Generate / Smart Fill', icon: Wand2 },
]

const normalizeItems = (data) => (Array.isArray(data) ? data : (data?.items || []))

const getErrorMessage = (err, fallback) => (
  err?.response?.data?.detail || err?.message || fallback
)

const getTemplateVariables = (template) => {
  const matches = template?.body?.match(/\{\{(.+?)\}\}/g) || []
  const schemaFields = template?.variable_schema?.fields || []
  const excludedNames = new Set(schemaFields
    .filter((field) => field?.included === false)
    .map((field) => field?.name)
    .filter(Boolean))
  const bodyNames = matches
    .map((m) => m.slice(2, -2).trim())
    .filter((name) => name && !excludedNames.has(name))
  const schemaNames = schemaFields
    .filter((field) => field?.included !== false)
    .map((field) => field?.name)
    .filter(Boolean)
  return [...new Set([...bodyNames, ...schemaNames])]
}

const friendlyVariableLabel = (name) => name
  .replace(/[_-]+/g, ' ')
  .replace(/\b\w/g, (c) => c.toUpperCase())

const formatMatterLabel = (matter) => {
  if (!matter) return ''
  const name = matter.matter_name || matter.title || matter.name || 'Untitled matter'
  return [name, matter.client_name, matter.practice_area, matter.status].filter(Boolean).join(' - ')
}

const IMAGE_SAMPLE_PATTERN = /\.(png|jpe?g|tiff?|webp)$/i
const isImageSample = (sample) => Boolean(
  sample && (String(sample.type || '').startsWith('image/') || IMAGE_SAMPLE_PATTERN.test(sample.name || ''))
)

const DIALOG_FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

function useDialogKeyboard({ dialogRef, initialFocusRef, onDismiss }) {
  const previousFocusRef = useRef(null)
  const onDismissRef = useRef(onDismiss)

  useEffect(() => {
    onDismissRef.current = onDismiss
  }, [onDismiss])

  useEffect(() => {
    previousFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null
    const initialRoot = initialFocusRef.current
    const initialTarget = initialRoot?.matches?.(DIALOG_FOCUSABLE)
      ? initialRoot
      : initialRoot?.querySelector?.(DIALOG_FOCUSABLE)
    ;(initialTarget || dialogRef.current?.querySelector(DIALOG_FOCUSABLE) || dialogRef.current)?.focus()

    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onDismissRef.current?.()
        return
      }
      if (event.key !== 'Tab') return

      const focusable = Array.from(dialogRef.current?.querySelectorAll(DIALOG_FOCUSABLE) || [])
      if (!focusable.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      previousFocusRef.current?.focus()
    }
  }, [dialogRef, initialFocusRef])
}

function Modal({ title, children, onClose, wide = false }) {
  const titleId = useId()
  const dialogRef = useRef(null)
  const bodyRef = useRef(null)
  useDialogKeyboard({ dialogRef, initialFocusRef: bodyRef, onDismiss: onClose })

  return (
    <div className={`fixed inset-0 z-50 flex items-start justify-center ${wide ? 'pt-3' : 'pt-12'}`}>
      <div className="absolute inset-0 bg-black/40" onClick={onClose} aria-hidden="true" />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={`relative bg-brand-surface-2 border border-brand-line rounded-lg shadow-xl w-full mx-4 flex flex-col ${wide ? 'max-w-[97vw] h-[96vh] max-h-[96vh]' : 'max-w-3xl max-h-[85vh]'}`}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-brand-line">
          <h2 id={titleId} className="text-lg font-semibold text-brand-ink">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            className="p-1 text-brand-muted hover:text-brand-ink"
            aria-label="Close"
          >
            <X size={20} />
          </button>
        </div>
        <div ref={bodyRef} className="flex-1 overflow-y-auto px-6 py-4">{children}</div>
      </div>
    </div>
  )
}

function ConfirmDialog({ message, onConfirm, onCancel }) {
  const titleId = useId()
  const messageId = useId()
  const dialogRef = useRef(null)
  const cancelRef = useRef(null)
  useDialogKeyboard({ dialogRef, initialFocusRef: cancelRef, onDismiss: onCancel })

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/40" onClick={onCancel} aria-hidden="true" />
      <div
        ref={dialogRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={messageId}
        tabIndex={-1}
        className="relative bg-brand-surface-2 border border-brand-line rounded-lg shadow-xl p-6 max-w-sm w-full mx-4"
      >
        <h2 id={titleId} className="text-lg font-semibold text-brand-ink">Delete template?</h2>
        <p id={messageId} className="mt-2 text-brand-ink mb-6">{message}</p>
        <div className="flex justify-end gap-3">
          <button
            ref={cancelRef}
            type="button"
            onClick={onCancel}
            className="px-4 py-2 text-sm text-brand-muted hover:text-brand-ink border border-brand-line rounded"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className="px-4 py-2 text-sm text-white bg-brand-rose hover:bg-red-700 rounded"
          >
            Delete
          </button>
        </div>
      </div>
    </div>
  )
}

function TemplateForm({ initial, onSubmit, onCancel }) {
  const [title, setTitle] = useState(initial?.title || '')
  const [body, setBody] = useState(initial?.body || '')
  const [category, setCategory] = useState(initial?.category || 'other')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const sourceFormat = String(initial?.format || '').toLowerCase()
  const isSourceBackedTemplate = ['pdf', 'docx'].includes(sourceFormat) && Boolean(initial?.source_sha256)

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!title.trim() || (!isSourceBackedTemplate && !body.trim())) return
    setSaving(true)
    setError(null)
    try {
      await onSubmit(isSourceBackedTemplate
        ? { title: title.trim(), category }
        : { title: title.trim(), body: body.trim(), category })
    } catch (err) {
      setError(getErrorMessage(err, 'Failed to save template.'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {error && (
        <div className="text-sm text-brand-rose bg-brand-rose/10 border border-brand-rose/30 px-3 py-2">
          {error}
        </div>
      )}
      <div>
        <label htmlFor="template-title" className="block text-sm font-medium text-brand-ink mb-1">
          Title
        </label>
        <input
          id="template-title"
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-brand-bg text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent"
          placeholder="Engagement Letter Template"
          required
        />
      </div>
      <div>
        <label htmlFor="template-category" className="block text-sm font-medium text-brand-ink mb-1">
          Category
        </label>
        <select
          id="template-category"
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-brand-bg text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent"
        >
          {Object.entries(CATEGORY_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>
      {isSourceBackedTemplate ? (
        <div role="note" className="rounded border border-brand-line bg-brand-bg px-3 py-2">
          <p className="text-sm font-medium text-brand-ink">{sourceFormat.toUpperCase()} layout and field mappings come from the retained source file.</p>
          <p className="mt-1 text-xs text-brand-muted">
            Rename or recategorize this template here. To replace the document or its field map, upload the new source as a fresh draft and verify it before activation.
          </p>
        </div>
      ) : (
        <div>
          <label htmlFor="template-body" className="block text-sm font-medium text-brand-ink mb-1">
            Body
          </label>
          <textarea
            id="template-body"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-brand-bg text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent font-mono"
            rows={16}
            placeholder={'Dear {{client_name}},\n\nThis letter confirms...\n\nSincerely,\n{{attorney_name}}'}
            required
          />
          <p className="text-xs text-brand-muted mt-1">
            Use {'{{variable_name}}'} for placeholders.
          </p>
        </div>
      )}
      <div className="flex justify-end gap-3 pt-2">
        <button
          type="button"
          onClick={onCancel}
          className="px-4 py-2 text-sm text-brand-muted hover:text-brand-ink border border-brand-line rounded"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={saving}
          className="px-4 py-2 text-sm text-white bg-brand-ink hover:bg-brand-ink-2 rounded disabled:opacity-50"
        >
          {saving ? 'Saving...' : initial ? 'Update' : 'Create'}
        </button>
      </div>
    </form>
  )
}

const isSourceBackedTemplateMissing = (template) => (
  ['pdf', 'docx'].includes(String(template?.format || '').toLowerCase())
  && (!template?.source_filename || !template?.source_sha256)
)

export const replaceTemplateVariable = (body, from, to) => {
  if (!from || !to || from === to) return body
  const escaped = from.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return String(body || '').replace(new RegExp(`\\{\\{\\s*${escaped}\\s*\\}\\}`, 'g'), `{{${to}}}`)
}

export const normalizeVariableName = (value) => String(value || '')
  .trim()
  .toLowerCase()
  .replace(/[^a-z0-9]+/g, '_')
  .replace(/^_+|_+$/g, '')

const fieldsRequireHumanReview = (fields, analysis) => (
  String(analysis?.suggested_variable_schema?.detection?.method || '').toLowerCase().includes('ocr')
  || fields.some((field) => (
    field?.review_required === true
    || (field?.confidence != null && Number(field.confidence) < 0.75)
    || field?.pdf_overlay?.source_kind === 'ocr'
    || field?.pdf_overlays?.some((overlay) => overlay?.source_kind === 'ocr')
  ))
)

const workspaceFieldIdentity = (field, index = 0) => (
  field?.pdf_source_key
  || field?.pdf_field_name
  || field?._bodyName
  || `${field?.name || 'field'}:${index}`
)

export const downloadRenderedText = (rendered, title) => {
  const filename = `${String(title || 'generated-document').replace(/[^a-z0-9._-]+/gi, '_')}.md`
  triggerBlobDownload(new Blob([String(rendered || '')], { type: 'text/markdown;charset=utf-8' }), filename)
}

function UploadTemplateForm({ onCreated, onCancel }) {
  const [file, setFile] = useState(null)
  const [title, setTitle] = useState('')
  const [category, setCategory] = useState('other')
  const [analysis, setAnalysis] = useState(null)
  const [draftBody, setDraftBody] = useState('')
  const [mappedFields, setMappedFields] = useState([])
  const [sourcePreviewUrl, setSourcePreviewUrl] = useState('')
  const [sourcePreviewKind, setSourcePreviewKind] = useState('')
  const [analyzing, setAnalyzing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [analysisFileKey, setAnalysisFileKey] = useState('')
  const [rejection, setRejection] = useState('')
  const [reviewConfirmed, setReviewConfirmed] = useState(false)
  const [sourceReviewReady, setSourceReviewReady] = useState(false)
  const [aiConsent, setAiConsent] = useState(false)
  const [aiAnalyzing, setAiAnalyzing] = useState(false)
  const analysisRequestRef = useRef(0)

  const fileKey = file ? `${file.name}:${file.size}:${file.lastModified}` : ''

  useEffect(() => () => {
    if (sourcePreviewUrl) URL.revokeObjectURL(sourcePreviewUrl)
  }, [sourcePreviewUrl])

  const reviewedFields = () => mappedFields.map(({ _bodyName, ...field }) => field)

  const reviewedVariableSchema = () => ({
    ...(analysis?.suggested_variable_schema || {}),
    fields: reviewedFields(),
  })

  const buildFormData = ({ includeCategory = false, includeReview = false, sourceFile = file } = {}) => {
    const form = new FormData()
    form.append('file', sourceFile)
    // A newly selected File is analyzed before React commits its reset state.
    // Never carry the previous document's title into that request.
    if (sourceFile === file && title.trim()) form.append('title', title.trim())
    if (includeCategory) form.append('category', category)
    if (includeReview) {
      if (draftBody.trim()) form.append('reviewed_body', draftBody)
      form.append('variable_schema', JSON.stringify(reviewedVariableSchema()))
      if (analysis?.analysis_token) form.append('analysis_token', analysis.analysis_token)
    }
    return form
  }

  const handleAnalyze = async (selectedFile = file) => {
    if (!selectedFile) {
      setError('Choose a DOCX, PDF, TXT, PNG, JPEG, TIFF, BMP, or WebP sample first.')
      return
    }
    const requestId = analysisRequestRef.current + 1
    analysisRequestRef.current = requestId
    const requestedFileKey = `${selectedFile.name}:${selectedFile.size}:${selectedFile.lastModified}`
    const shouldUseSuggestedTitle = selectedFile !== file || !title.trim()
    setAnalyzing(true)
    setError(null)
    setSourceReviewReady(false)
    setAiConsent(false)
    try {
      const form = buildFormData({ sourceFile: selectedFile })
      const result = await analyzeTemplateUpload(form)
      if (analysisRequestRef.current !== requestId) return
      setAnalysis(result)
      setReviewConfirmed(false)
      setAnalysisFileKey(requestedFileKey)
      setDraftBody(result.body || result.extracted_text || '')
      setMappedFields((result.suggested_variable_schema?.fields || []).map((field) => ({ ...field, _bodyName: field.name })))
      setSourcePreviewUrl(
        ['pdf', 'image'].includes(String(result.format || '').toLowerCase()) || isImageSample(selectedFile)
          ? URL.createObjectURL(selectedFile)
          : '',
      )
      setSourcePreviewKind(isImageSample(selectedFile) ? 'image' : selectedFile.type === 'application/pdf' || String(result.format || '').toLowerCase() === 'pdf' ? 'pdf' : '')
      if (shouldUseSuggestedTitle) setTitle(result.title || '')
    } catch (err) {
      if (analysisRequestRef.current !== requestId) return
      setSourcePreviewUrl('')
      setSourcePreviewKind('')
      setAnalysisFileKey('')
      setReviewConfirmed(false)
      setSourceReviewReady(false)
      setError(getErrorMessage(err, 'Could not analyze that sample.'))
    } finally {
      if (analysisRequestRef.current === requestId) setAnalyzing(false)
    }
  }

  const handleAiProposal = async () => {
    if (!file || !analysis || analysisFileKey !== fileKey) {
      setError('Run the local document scan before requesting a premium AI proposal.')
      return
    }
    if (!aiConsent) {
      setError('Confirm the premium AI text-sharing notice before continuing.')
      return
    }
    const requestId = analysisRequestRef.current
    setAiAnalyzing(true)
    setError(null)
    try {
      const form = buildFormData()
      form.append('consent_to_external_ai', 'true')
      const result = await proposeTemplateFieldsWithAi(form)
      if (analysisRequestRef.current !== requestId) return
      const proposals = result?.suggested_variable_schema?.fields || []
      setAnalysis(result)
      setDraftBody(result.body || result.extracted_text || '')
      setMappedFields(proposals.map((field) => ({ ...field, _bodyName: field.name })))
      setReviewConfirmed(false)
      setSourceReviewReady(false)
      if (!proposals.some((field) => field?.ai_suggested)) {
        setError('Premium AI found no additional source-backed fields to propose.')
      }
    } catch (err) {
      if (analysisRequestRef.current === requestId) setError(getErrorMessage(err, 'Premium AI could not propose template fields.'))
    } finally {
      if (analysisRequestRef.current === requestId) setAiAnalyzing(false)
    }
  }

  const selectFile = (selectedFile) => {
    analysisRequestRef.current += 1
    setFile(selectedFile)
    setTitle('')
    setAnalysis(null)
    setAnalysisFileKey('')
    setDraftBody('')
    setMappedFields([])
    setSourcePreviewUrl('')
    setSourcePreviewKind('')
    setSourceReviewReady(false)
    setAnalyzing(false)
    setAiAnalyzing(false)
    setAiConsent(false)
    setError(null)
    setRejection('')
    if (selectedFile) void handleAnalyze(selectedFile)
  }

  const onDrop = useCallback((acceptedFiles) => {
    if (acceptedFiles[0]) selectFile(acceptedFiles[0])
  }, [file, title, category, draftBody, analysis])

  const onDropRejected = useCallback((rejections) => {
    const rejection = rejections[0]
    const reason = rejection?.errors?.[0]
    const message = reason?.code === 'file-too-large'
      ? 'That file is too large. Choose a file up to 50 MB.'
      : reason?.code === 'file-invalid-type'
        ? 'Use a PDF, DOCX, TXT, PNG, JPEG, TIFF, BMP, or WebP file.'
        : reason?.message || 'Choose one supported document or image.'
    setRejection(message)
    setError(null)
  }, [])

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    onDropRejected,
    disabled: saving,
    multiple: false,
    maxFiles: 1,
    maxSize: 50 * 1024 * 1024,
    accept: {
      'application/pdf': ['.pdf'],
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
      'text/plain': ['.txt'],
      'image/png': ['.png'],
      'image/jpeg': ['.jpg', '.jpeg'],
      'image/tiff': ['.tif', '.tiff'],
      'image/bmp': ['.bmp'],
      'image/webp': ['.webp'],
    },
  })

  const handleCreate = async () => {
    if (!file || !analysis || analysisFileKey !== fileKey) {
      setError('Analyze the sample and review the extracted text and fields before creating the template.')
      return
    }
    const isPdfUpload = String(analysis.format || '').toLowerCase() === 'pdf'
    const requiresHumanReview = fieldsRequireHumanReview(mappedFields, analysis)
    if (isPdfUpload && !sourceReviewReady) {
      setError('Wait for the source preview to load, or open the original document from the review workspace before saving.')
      return
    }
    if (requiresHumanReview && !reviewConfirmed) {
      setError('Compare the detected values with the source document and confirm the review before creating the template.')
      return
    }
    if (isPdfUpload && !mappedFields.some((field) => field?.included !== false && (field?.pdf_field_name || field?.pdf_overlay || field?.pdf_overlays?.length))) {
      setError('Include at least one reusable field. Add a field on the page, or re-include a detected field before saving.')
      return
    }
    if (!title.trim() || (!isPdfUpload && !draftBody.trim())) {
      setError(isPdfUpload ? 'Template title is required.' : 'Template title and extracted body are required.')
      return
    }
    if (mappedFields.some((field) => !/^[A-Za-z][A-Za-z0-9_.-]*$/.test(String(field.name || '')))) {
      setError('Every field needs a valid automation key that starts with a letter.')
      return
    }
    const fieldNames = mappedFields.map((field) => field.name)
    if (new Set(fieldNames).size !== fieldNames.length) {
      setError('Every field needs a unique automation key.')
      return
    }
    const reviewedNames = new Set(mappedFields
      .filter((field) => field?.included !== false)
      .map((field) => field.name))
    const bodyNames = [...String(draftBody || '').matchAll(/\{\{\s*([a-zA-Z][a-zA-Z0-9_.-]*)\s*\}\}/g)].map((match) => match[1])
    const unmappedBodyNames = bodyNames.filter((name) => !reviewedNames.has(name))
    if (unmappedBodyNames.length > 0) {
      setError(`Map every placeholder before creating this source-backed template. Missing: ${[...new Set(unmappedBodyNames)].join(', ')}.`)
      return
    }
    if (String(analysis.format || '').toLowerCase() === 'docx' && mappedFields.some((field) => !String(field.source_text || field.example || '').trim())) {
      setError('Every Word field needs the exact text it replaces in the source document.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      // Every uploaded template uses the same source-backed lifecycle. This
      // keeps Word formatting intact and binds the reviewed analysis to the
      // exact bytes that are persisted by the server.
      await createTemplateFromUpload(buildFormData({ includeCategory: true, includeReview: true }))
      onCreated()
    } catch (err) {
      setError(getErrorMessage(err, 'Could not create template from upload.'))
    } finally {
      setSaving(false)
    }
  }

  const fields = mappedFields
  const branding = analysis?.detected_branding_profile || {}
  const detection = analysis?.suggested_variable_schema?.detection || {}
  const detectionProviderLabel = detection.provider === 'azure'
    ? 'Azure Document Intelligence'
    : detection.provider === 'local'
      ? 'private local OCR'
      : ''
  const isPdfAnalysis = String(analysis?.format || '').toLowerCase() === 'pdf'
  const hasPdfMappings = fields.some((field) => field?.included !== false && (field?.pdf_field_name || field?.pdf_overlay || field?.pdf_overlays?.length))
  const requiresHumanReview = fieldsRequireHumanReview(fields, analysis)
  const lowConfidenceFieldCount = fields.filter((field) => (
    Number(field?.confidence ?? 1) < 0.75 || field?.ai_suggested
  )).length
  const unmappedAiSuggestions = analysis?.suggested_variable_schema?.unmapped_ai_suggestions || []
  const handleWorkspaceFieldsChange = (nextFields) => {
    const previousByIdentity = new Map(fields.map((field, index) => [workspaceFieldIdentity(field, index), field]))
    setDraftBody((current) => {
      let nextBody = current
      nextFields.forEach((field, index) => {
        const previous = previousByIdentity.get(workspaceFieldIdentity(field, index))
        if (!previous) return
        if (previous.name && previous.name !== field.name) {
          nextBody = replaceTemplateVariable(nextBody, previous.name, field.name)
        }
        if (previous.included !== false && field.included === false && field.name) {
          const escaped = field.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
          nextBody = String(nextBody || '').replace(
            new RegExp(`\\{\\{\\s*${escaped}\\s*\\}\\}`, 'g'),
            field.source_text || field.example || '',
          )
        }
      })
      return nextBody
    })
    setReviewConfirmed(false)
    setMappedFields(nextFields)
  }
  const analysisReady = analysisFileKey === fileKey && Boolean(analysis)
  const reviewComplete = analysisReady
    && (!isPdfAnalysis || sourceReviewReady)
    && (!requiresHumanReview || reviewConfirmed)

  const renameField = (index, rawName) => {
    const nextName = normalizeVariableName(rawName)
    const currentField = fields[index]
    const previousBodyName = currentField?._bodyName || currentField?.name
    setReviewConfirmed(false)
    setMappedFields((current) => current.map((field, fieldIndex) => (
      fieldIndex === index
        ? { ...field, name: nextName, _bodyName: nextName || field._bodyName || field.name }
        : field
    )))
    if (previousBodyName && nextName) setDraftBody((current) => replaceTemplateVariable(current, previousBodyName, nextName))
  }

  const addManualField = () => {
    const existing = new Set(fields.map((field) => field.name))
    let suffix = fields.length + 1
    while (existing.has(`field_${suffix}`)) suffix += 1
    setReviewConfirmed(false)
    setMappedFields((current) => [
      ...current,
      {
        name: `field_${suffix}`,
        label: 'New replacement field',
        source_text: '',
        confidence: 1,
        review_required: true,
        _bodyName: `field_${suffix}`,
      },
    ])
  }

  const updateSourceText = (index, sourceText) => {
    setReviewConfirmed(false)
    setMappedFields((current) => current.map((field, fieldIndex) => (
      fieldIndex === index ? { ...field, source_text: sourceText, example: sourceText } : field
    )))
  }

  const applySourceText = (index) => {
    const field = fields[index]
    const sourceText = String(field?.source_text || '').trim()
    if (!sourceText || !field?.name) return
    if (!draftBody.includes(sourceText) && !draftBody.includes(`{{${field.name}}}`)) {
      setError(`“${sourceText}” was not found in the extracted document text.`)
      return
    }
    setReviewConfirmed(false)
    setDraftBody((current) => current.split(sourceText).join(`{{${field.name}}}`))
    setError(null)
  }

  const removeField = (index) => {
    const field = fields[index]
    const sourceText = field?.source_text || field?.example || ''
    if (field?.name && sourceText) {
      const escaped = field.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      setDraftBody((current) => String(current || '').replace(
        new RegExp(`\\{\\{\\s*${escaped}\\s*\\}\\}`, 'g'),
        sourceText,
      ))
    }
    setReviewConfirmed(false)
    setMappedFields((current) => current.filter((_, fieldIndex) => fieldIndex !== index))
  }

  return (
    <div className="space-y-4">
      {error && (
        <div className="text-sm text-brand-rose bg-brand-rose/10 border border-brand-rose/30 px-3 py-2">
          {error}
        </div>
      )}
      {rejection && (
        <div role="alert" className="text-sm text-brand-rose bg-brand-rose/10 border border-brand-rose/30 px-3 py-2">
          {rejection}
        </div>
      )}

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3" aria-label="Template setup progress">
        {[
          ['1', 'Choose source', file ? 'Complete' : 'Current'],
          ['2', 'Review fields', reviewComplete ? 'Complete' : file ? 'Current' : 'Next'],
          ['3', 'Create draft', reviewComplete ? 'Current' : 'Next'],
        ].map(([step, label, state]) => (
          <div key={step} className={`rounded border px-3 py-2 ${state === 'Current' ? 'border-brand-accent bg-brand-accent/10' : 'border-brand-line bg-brand-bg'}`}>
            <p className="text-[10px] font-semibold uppercase tracking-wider text-brand-muted">Step {step} · {state}</p>
            <p className="mt-0.5 text-sm font-medium text-brand-ink">{label}</p>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_220px] gap-4">
        <div>
          <label htmlFor="template-sample-file" className="block text-sm font-medium text-brand-ink mb-1">
            Sample document or filled scan
          </label>
          <div
            {...getRootProps({
              role: 'button',
              'aria-label': 'Choose sample document or filled scan',
              'aria-describedby': 'template-sample-guidance',
              className: `rounded-lg border-2 border-dashed p-5 text-center transition-colors ${isDragActive ? 'border-brand-accent bg-brand-accent/10' : 'border-brand-line bg-brand-bg hover:border-brand-accent/60'} ${saving ? 'pointer-events-none opacity-60' : 'cursor-pointer'}`,
            })}
          >
            <input
              {...getInputProps({ id: 'template-sample-file', disabled: saving, 'aria-label': 'Sample document' })}
            />
            <Upload size={22} className="mx-auto mb-2 text-brand-accent-2" />
            <p className="text-sm font-semibold text-brand-ink">{isDragActive ? 'Drop the sample here' : 'Drop a sample here or browse'}</p>
            <p className="mt-1 text-xs text-brand-muted">One PDF, DOCX, TXT, PNG, JPEG, TIFF, BMP, or WebP · up to 50 MB</p>
          </div>
          <p id="template-sample-guidance" className="mt-2 text-xs text-brand-muted">
            Upload the document your team already reuses. We read Word files, PDFs, and image-only scans while preserving the original design. Filled or handwritten entries help locate reusable details; uncertain readings are flagged for review. Image uploads are converted to a safe PDF so reviewed field locations and the page design stay together.
          </p>
          <details className="mt-2 rounded border border-brand-line bg-brand-surface-2 p-3 text-xs text-brand-muted">
            <summary className="cursor-pointer font-semibold text-brand-ink">Pro tips for reliable field detection</summary>
            <ul className="mt-2 list-disc space-y-1 pl-5">
              <li>In Word, use a clear label beside the value, a labeled blank, or an explicit placeholder such as {'{{client_name}}'}.</li>
              <li>In PDFs, real form controls work best. For scans, use upright, high-contrast pages and include a filled sample when possible.</li>
              <li>If something is missed, add the exact Word replacement text or place a field directly on the PDF page, then verify it against the source.</li>
            </ul>
          </details>
          {file && (
            <p className="mt-2 text-xs font-medium text-brand-accent-2" role="status">
              Current source: {file.name} ({Math.max(1, Math.round(file.size / 1024))} KB)
              {analysisFileKey === fileKey ? ' · ready to review' : analyzing ? ' · reading now' : ' · waiting to be read'}
            </p>
          )}
        </div>
        <div>
          <label htmlFor="templatespage-category" className="block text-sm font-medium text-brand-ink mb-1">
            Category
          </label>
          <select id="templatespage-category"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-brand-bg text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent"
          >
            {Object.entries(CATEGORY_LABELS).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </div>
      </div>

      <div>
        <label htmlFor="templatespage-template-title" className="block text-sm font-medium text-brand-ink mb-1">
          Template title
        </label>
        <input id="templatespage-template-title"
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-brand-bg text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent"
          placeholder="Auto-filled from file name if blank"
        />
      </div>

      <div className="sticky bottom-0 z-10 -mx-2 flex flex-col gap-3 border-t border-brand-line bg-brand-surface-2/95 px-2 py-3 backdrop-blur sm:flex-row">
        {analysis && (
          <div className="order-first rounded border border-brand-accent/30 bg-brand-accent/5 p-3 text-left sm:order-none sm:flex-1">
            <p className="text-sm font-semibold text-brand-ink">Optional premium AI field proposal</p>
            <p className="mt-1 text-xs text-brand-muted">Only extracted text and field metadata are sent after local redaction; the original file and page images stay here. AI suggestions are review-only and never save or activate a template.</p>
            <label className="mt-2 flex items-start gap-2 text-xs text-brand-muted">
              <input type="checkbox" checked={aiConsent} onChange={(event) => { setAiConsent(event.target.checked); setError(null) }} className="mt-0.5" />
              I consent to sending extracted text to the configured premium AI provider for this proposal.
            </label>
            <button type="button" onClick={handleAiProposal} disabled={aiAnalyzing || !aiConsent} className="mt-2 rounded border border-brand-accent/40 px-3 py-1.5 text-xs text-brand-ink hover:bg-brand-bg disabled:opacity-50">
              {aiAnalyzing ? 'Proposing fields…' : 'Suggest fields with premium AI'}
            </button>
          </div>
        )}
        <button
          type="button"
          onClick={() => handleAnalyze()}
          disabled={analyzing || !file}
          className="flex items-center justify-center gap-2 px-4 py-2 text-sm text-brand-ink border border-brand-line rounded hover:bg-brand-bg disabled:opacity-50"
        >
          <Wand2 size={16} />
          {analyzing ? 'Reading document and finding reusable details...' : analysis ? 'Scan again' : 'Find reusable details'}
        </button>
        <button
          type="button"
          onClick={handleCreate}
          disabled={saving || !file || !analysis || (isPdfAnalysis && (!hasPdfMappings || !sourceReviewReady)) || (requiresHumanReview && !reviewConfirmed)}
          className="flex items-center justify-center gap-2 px-4 py-2 text-sm text-white bg-brand-ink hover:bg-brand-ink-2 rounded disabled:opacity-50"
        >
          <Upload size={16} />
          {saving
            ? 'Creating...'
            : isPdfAnalysis && !sourceReviewReady
              ? 'Waiting for source preview'
              : requiresHumanReview && !reviewConfirmed
                ? 'Confirm review below to save'
                : analysis
                  ? 'Save reusable template'
                  : 'Reading document first'}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="px-4 py-2 text-sm text-brand-muted hover:text-brand-ink border border-brand-line rounded"
        >
          Cancel
        </button>
      </div>

      {analysis && (
        <div className="space-y-4 pt-2">
          <div className={`rounded border p-4 ${fields.length ? 'border-brand-green/30 bg-brand-green/10' : 'border-brand-amber/40 bg-brand-amber/10'}`} role="status">
            <p className="text-base font-semibold text-brand-ink">
              {fields.length
                ? `${fields.length} reusable detail${fields.length === 1 ? '' : 's'} found`
                : 'We read the document but need a little help'}
            </p>
            <p className="mt-1 text-sm text-brand-muted">
              {fields.length
                ? `Read using ${detection.label || 'document structure'}${detectionProviderLabel ? ` (${detectionProviderLabel})` : ''}. We’ll ask for these details whenever someone uses this template.`
                : 'Try a clearer copy, or add a replacement field below. Nothing has been saved yet.'}
            </p>
            <p className="mt-2 text-xs text-brand-muted">
              Detected: {fields.length} · Needs verification: {lowConfidenceFieldCount}
            </p>
            {detection.pages_analyzed && (
              <p className="mt-2 text-xs text-brand-muted">
                Pages checked: {detection.pages_analyzed}{detection.pages_total ? ` of ${detection.pages_total}` : ''}
              </p>
            )}
            {Array.isArray(detection.ocr_pages) && detection.ocr_pages.length > 0 && (
              <p className="mt-1 text-xs text-brand-muted">Pages OCR-checked: {detection.ocr_pages.join(', ')}</p>
            )}
          </div>
          {unmappedAiSuggestions.length > 0 && (
            <div className="mt-3 rounded border border-brand-amber/40 bg-brand-amber/10 p-3 text-xs text-brand-ink" role="status">
              <p className="font-semibold">AI suggestions needing source confirmation</p>
              <p className="mt-1 text-brand-muted">These ideas were not added because the scan could not locate exact source evidence.</p>
              <ul className="mt-2 list-disc space-y-1 pl-5">
                {unmappedAiSuggestions.map((item, index) => <li key={`${item?.name || item?.label || 'suggestion'}-${index}`}>{item?.label || item?.name || item?.text || 'Unmapped suggestion'}</li>)}
              </ul>
            </div>
          )}
          {isPdfAnalysis && (
            <div className="border border-brand-green/30 rounded bg-brand-green/10 p-4 text-sm text-brand-ink">
              <p className="font-semibold">{sourcePreviewKind === 'image' ? 'Original scan design preserved' : 'Original PDF design preserved'}</p>
              <p className="mt-1 text-brand-muted">The source page remains the visual design. Existing controls, ordinary page text, and scanned pages become reusable through reviewed field placements.</p>
            </div>
          )}
          {isPdfAnalysis && !hasPdfMappings && (
            <div role="alert" className="border border-brand-amber/40 rounded bg-brand-amber/10 p-4 text-sm text-brand-ink">
              <p className="font-semibold">No reusable details located confidently</p>
              <p className="mt-1 text-brand-muted">Try a clearer copy or add visible labels next to the details that change. Image-only scans are read automatically.</p>
            </div>
          )}
          {String(analysis.format || '').toLowerCase() === 'docx' && (
            <div role="alert" className="border border-brand-amber/40 rounded bg-brand-amber/10 p-4 text-sm text-brand-ink">
              <p className="font-semibold">Original Word document preserved</p>
              <p className="mt-1 text-brand-muted">The generated file remains a DOCX with the source layout, tables, headers, and footers. Review the detected replacement values below.</p>
            </div>
          )}
          {!isPdfAnalysis && sourcePreviewUrl && (
            <div className="border border-brand-line rounded bg-brand-bg p-4">
              <div className="mb-3">
                <p className="text-sm font-semibold text-brand-ink">Original document preview</p>
                <p className="mt-1 text-xs text-brand-muted">Compare the original source with the reusable details found below.</p>
              </div>
              {sourcePreviewKind === 'image' ? (
                <img title={`Source image preview: ${file?.name || analysis.title}`} src={sourcePreviewUrl} alt={`Uploaded source ${file?.name || ''}`} className="max-h-[55vh] min-h-[240px] w-full rounded border border-brand-line bg-white object-contain" />
              ) : (
                <object
                  title={`Source PDF preview: ${file?.name || analysis.title}`}
                  data={sourcePreviewUrl}
                  type="application/pdf"
                  className="h-[55vh] min-h-[420px] w-full rounded border border-brand-line bg-white"
                >
                  <p className="p-4 text-sm text-brand-muted">This browser cannot display the validated source PDF inline. Review the detected page and field details below, or open the original file locally.</p>
                </object>
              )}
            </div>
          )}
          {requiresHumanReview && !isPdfAnalysis && (
            <label className="mb-3 flex items-start gap-2 rounded border border-brand-amber/40 bg-brand-amber/10 p-3 text-sm text-brand-ink">
              <input type="checkbox" aria-label="Confirm source comparison" checked={reviewConfirmed} onChange={(event) => setReviewConfirmed(event.target.checked)} className="mt-0.5 h-4 w-4 accent-brand-accent" />
              <span>I compared the detected values with the original source and corrected anything uncertain.</span>
            </label>
          )}
          {isPdfAnalysis ? (
            <PrepareFormWorkspace file={file} analysis={analysis} fields={fields} previewUrl={sourcePreviewUrl} reviewConfirmed={reviewConfirmed} onReviewConfirmed={setReviewConfirmed} onSourceReviewReadyChange={setSourceReviewReady} onFieldsChange={handleWorkspaceFieldsChange} />
          ) : (
          <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_320px] gap-4">
          <div className="border border-brand-line rounded bg-brand-bg p-4">
            <div className="flex items-center justify-between gap-3 mb-3">
              <div>
                <p className="text-sm font-semibold text-brand-ink">{analysis.title}</p>
                <p className="text-xs text-brand-muted uppercase">{sourcePreviewKind === 'image' ? 'Scanned image → PDF' : analysis.format}</p>
              </div>
              <span className="text-xs text-brand-muted">{fields.length} field{fields.length === 1 ? '' : 's'}</span>
            </div>
            <label htmlFor="reviewed-template-body" className="block text-xs font-semibold text-brand-muted mb-2">{isPdfAnalysis ? 'Text found in the document (the page design is unchanged)' : 'Extracted template body'}</label>
            <textarea id="reviewed-template-body" value={draftBody} readOnly={isPdfAnalysis} onChange={(event) => { setReviewConfirmed(false); setDraftBody(event.target.value) }} rows={18} className="w-full rounded border border-brand-line bg-brand-surface-2 p-3 font-mono text-xs text-brand-ink read-only:opacity-75" />
          </div>

          <div className="space-y-3">
            <div className="border border-brand-line rounded bg-brand-bg p-4">
              <p className="text-sm font-semibold text-brand-ink mb-2">Details we’ll ask for</p>
              {fields.length > 0 ? (
                <div className="space-y-3 max-h-80 overflow-y-auto pr-1">
                  {fields.map((field, index) => {
                    const optionLabels = (field.options || []).map((option) => (
                      typeof option === 'object'
                        ? option.label ?? option.name ?? option.value
                        : option
                    )).filter(Boolean)
                    return (
                      <div key={`${index}-${field.name}`} className="rounded border border-brand-line bg-brand-surface-2 p-3 text-xs">
                        <p className="font-semibold text-brand-ink">{field.label || `Detail ${index + 1}`}</p>
                        <div className="mt-1 flex flex-wrap gap-1.5">
                          <span className={`rounded border px-2 py-0.5 ${Number(field.confidence || 0) >= 0.75 ? 'border-brand-green/30 bg-brand-green/10 text-brand-ink' : 'border-brand-amber/40 bg-brand-amber/10 text-brand-ink'}`}>
                            {Number(field.confidence || 0) >= 0.75 ? 'Strong match' : 'Please verify'}
                          </span>
                          {(field.pdf_overlay?.source_kind === 'ocr' || field.pdf_overlays?.some((item) => item?.source_kind === 'ocr')) && <span className="rounded border border-brand-line bg-brand-bg px-2 py-0.5 text-brand-muted">Read from scan</span>}
                          {field.ai_suggested && <span className="rounded border border-brand-accent/30 bg-brand-accent/10 px-2 py-0.5 text-brand-ink">AI proposal · verify</span>}
                        </div>
                        <details className="mt-2">
                          <summary className="cursor-pointer text-brand-muted">Advanced field settings</summary>
                          <label htmlFor={`mapped-field-${index}`} className="mt-2 block text-brand-muted">Automation key</label>
                          <input id={`mapped-field-${index}`} value={field.name || ''} onChange={(event) => renameField(index, event.target.value)} className="mt-1 w-full rounded border border-brand-line bg-brand-bg px-2 py-1.5 font-mono text-brand-ink" />
                        </details>
                        {field.pdf_field_name && <p className="mt-1 font-mono text-brand-accent-2">PDF control: {field.pdf_field_name}</p>}
                        {(field.pdf_overlay || field.pdf_overlays?.length) && <p className="mt-1 font-mono text-brand-accent-2">Detected PDF location · {field.pdf_overlays?.length > 1 ? `${field.pdf_overlays.length} placements` : `Page ${field.page}`}</p>}
                        {(field.pdf_field_name || field.pdf_overlay || field.pdf_overlays?.length) && (
                          <div className="mt-2 flex flex-wrap gap-1.5" aria-label={`PDF metadata for ${field.label || field.name}`}>
                            <span className="rounded border border-brand-line bg-brand-bg px-2 py-0.5 capitalize text-brand-muted">{field.field_type || 'text'}</span>
                            {field.page && <span className="rounded border border-brand-line bg-brand-bg px-2 py-0.5 text-brand-muted">Page {field.page}</span>}
                            <span className={`rounded border px-2 py-0.5 ${field.required ? 'border-brand-amber/40 bg-brand-amber/10 text-brand-ink' : 'border-brand-line bg-brand-bg text-brand-muted'}`}>{field.required ? 'Required' : 'Optional'}</span>
                            {field.multiline && <span className="rounded border border-brand-line bg-brand-bg px-2 py-0.5 text-brand-muted">Multiline</span>}
                          </div>
                        )}
                        {optionLabels.length > 0 && <p className="mt-2 text-brand-muted break-words"><span className="font-semibold text-brand-ink">Options:</span> {optionLabels.join(', ')}</p>}
                        {(field.example || field.source_text || field.source_path) && <p className="mt-1 text-brand-muted break-words"><span className="font-semibold text-brand-ink">Replaces:</span> {field.example || field.source_text || field.source_path}</p>}
                        {field.ai_reason && <p className="mt-1 text-brand-muted break-words"><span className="font-semibold text-brand-ink">AI rationale:</span> {field.ai_reason}</p>}
                        {!isPdfAnalysis && (
                          <div className="mt-2 space-y-1.5">
                            <label htmlFor={`source-text-${index}`} className="block text-brand-muted">Exact text in the source</label>
                            <div className="flex gap-1.5">
                              <input id={`source-text-${index}`} value={field.source_text || field.example || ''} onChange={(event) => updateSourceText(index, event.target.value)} className="min-w-0 flex-1 rounded border border-brand-line bg-brand-bg px-2 py-1.5 text-brand-ink" />
                              <button type="button" onClick={() => applySourceText(index)} className="rounded border border-brand-line px-2 py-1 text-brand-ink hover:bg-brand-bg">Mark</button>
                              <button type="button" onClick={() => removeField(index)} aria-label={`Remove ${field.label || field.name}`} className="rounded border border-brand-line px-2 py-1 text-brand-rose hover:bg-brand-bg"><Trash2 size={13} /></button>
                            </div>
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              ) : (
                <p className="text-xs text-brand-muted">No fields detected yet.</p>
              )}
              {!isPdfAnalysis && (
                <button type="button" onClick={addManualField} className="mt-3 inline-flex items-center gap-1.5 rounded border border-brand-line px-3 py-1.5 text-xs font-semibold text-brand-ink hover:bg-brand-surface-2">
                  <Plus size={13} /> Add replacement field
                </button>
              )}
            </div>

            <div className="border border-brand-line rounded bg-brand-bg p-4">
              <p className="text-sm font-semibold text-brand-ink mb-2">Letterhead</p>
              <p className="text-xs text-brand-muted whitespace-pre-wrap">
                {branding.letterhead_detected
                  ? branding.header_text || 'Letterhead-like header detected.'
                  : 'No letterhead detected.'}
              </p>
            </div>

            {analysis.warnings?.length > 0 && (
              <div className="border border-brand-amber/40 rounded bg-brand-amber/10 p-4">
                <p className="text-sm font-semibold text-brand-ink mb-2">Things to check</p>
                <ul className="space-y-1">
                  {analysis.warnings.map((warning) => (
                    <li key={warning} className="text-xs text-brand-muted">{warning}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
          </div>
          )}
        </div>
      )}
    </div>
  )
}

function MatterPicker({ matters, selectedMatterId, onSelect, loading, disabled = false }) {
  const [query, setQuery] = useState('')
  const selected = matters.find((matter) => matter.id === selectedMatterId)
  const filtered = matters.filter((matter) => {
    const q = query.trim().toLowerCase()
    if (!q) return true
    return (
      matter.matter_name?.toLowerCase().includes(q) ||
      matter.client_name?.toLowerCase().includes(q) ||
      matter.practice_area?.toLowerCase().includes(q) ||
      matter.id?.toLowerCase().includes(q)
    )
  }).slice(0, 8)

  return (
    <div className="border border-brand-line rounded bg-brand-bg p-3">
      <label htmlFor="templatespage-matter" className="block text-sm font-medium text-brand-ink mb-2">
        Matter
      </label>
      <div className="relative">
        <Search size={15} className="absolute left-3 top-2.5 text-brand-muted" />
        <input id="templatespage-matter"
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          disabled={disabled}
          className="w-full pl-9 pr-3 py-2 border border-brand-line rounded text-sm bg-brand-surface-2 text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent"
          placeholder={loading ? 'Loading matters...' : 'Search by matter, client, or practice area'}
        />
      </div>
      {selected && (
        <div className="mt-2 flex items-center justify-between gap-2 text-xs bg-brand-surface-2 border border-brand-line rounded px-3 py-2">
          <span className="text-brand-ink truncate">{formatMatterLabel(selected)}</span>
          <button
            type="button"
            onClick={() => onSelect('')}
            disabled={disabled}
            className="text-brand-muted hover:text-brand-ink"
          >
            Clear
          </button>
        </div>
      )}
      <div className="mt-2 max-h-48 overflow-y-auto space-y-1">
        {filtered.map((matter) => (
          <button
            key={matter.id}
            type="button"
            onClick={() => onSelect(matter.id)}
            disabled={disabled}
            className={`w-full text-left px-3 py-2 rounded border text-sm transition-colors ${
              selectedMatterId === matter.id
                ? 'border-brand-accent bg-brand-accent/10 text-brand-ink'
                : 'border-transparent hover:border-brand-line hover:bg-brand-surface-2 text-brand-ink'
            }`}
          >
            <span className="block font-medium truncate">{matter.matter_name || 'Untitled matter'}</span>
            <span className="block text-xs text-brand-muted truncate">
              {[matter.client_name, matter.practice_area, matter.status].filter(Boolean).join(' - ') || matter.id}
            </span>
          </button>
        ))}
        {!loading && filtered.length === 0 && (
          <p className="text-xs text-brand-muted px-1 py-2">
            No matching matters. Paste a matter UUID below if needed.
          </p>
        )}
      </div>
    </div>
  )
}

function RenderModal({ template, matters, matterLoading, onClose }) {
  const [variables, setVariables] = useState({})
  const [matterId, setMatterId] = useState('')
  const [rendered, setRendered] = useState(null)
  const [matterDocId, setMatterDocId] = useState(null)
  const [savedDownloadUrl, setSavedDownloadUrl] = useState('')
  const [outputFilename, setOutputFilename] = useState('')
  const [outputFormat, setOutputFormat] = useState('')
  const [storageBackend, setStorageBackend] = useState('')
  const [storageWarning, setStorageWarning] = useState('')
  const [filePreview, setFilePreview] = useState(null)
  const [filePreviewUrl, setFilePreviewUrl] = useState('')
  const [previewId, setPreviewId] = useState('')
  const [previewPurpose, setPreviewPurpose] = useState('')
  const [rendering, setRendering] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState(null)
  const [smartFillState, setSmartFillState] = useState('idle')
  const [smartFillMessage, setSmartFillMessage] = useState('')
  const previewRequestGenerationRef = useRef(0)
  const smartFillRequestGenerationRef = useRef(0)
  const formRevisionRef = useRef(0)

  const names = useMemo(() => getTemplateVariables(template), [template])
  const fieldDefinitions = useMemo(() => Object.fromEntries(
    (template?.variable_schema?.fields || [])
      .filter((field) => field?.name && field?.included !== false)
      .map((field) => [field.name, field]),
  ), [template])
  const isPdfTemplate = String(template?.format || '').toLowerCase() === 'pdf'
  const isDocxTemplate = String(template?.format || '').toLowerCase() === 'docx' && Boolean(template?.source_sha256)
  const isFileTemplate = isPdfTemplate || isDocxTemplate
  const canSaveToMatter = Boolean(template?.is_active)
  const fillableNames = useMemo(
    () => names.filter((name) => fieldDefinitions[name]?.field_type !== 'signature'),
    [names, fieldDefinitions],
  )
  const requiredUnresolvedNames = fillableNames.filter((name) => {
    const field = fieldDefinitions[name]
    if (!field?.required) return false
    if (field.field_type === 'checkbox') return variables[name] !== 'true'
    return !String(variables[name] || '').trim()
  })
  const optionalUnfilledNames = fillableNames.filter((name) => {
    const field = fieldDefinitions[name]
    if (field?.required) return false
    if (field?.field_type === 'checkbox') return variables[name] === ''
    return !String(variables[name] || '').trim()
  })
  const activationUnresolvedNames = fillableNames.filter((name) => (
    fieldDefinitions[name]?.field_type !== 'checkbox'
    && !String(variables[name] || '').trim()
  ))

  useEffect(() => {
    const initialVars = {}
    fillableNames.forEach((name) => {
      initialVars[name] = fieldDefinitions[name]?.field_type === 'checkbox' ? 'false' : ''
    })
    setVariables(initialVars)
    setSaved(false)
    setRendered(null)
    setMatterDocId(null)
    setSavedDownloadUrl('')
    setOutputFilename('')
    setOutputFormat('')
    setStorageBackend('')
    setStorageWarning('')
    setFilePreview(null)
    setFilePreviewUrl('')
    setPreviewId('')
    setPreviewPurpose('')
    previewRequestGenerationRef.current += 1
    smartFillRequestGenerationRef.current += 1
    formRevisionRef.current += 1
  }, [fillableNames, fieldDefinitions])

  useEffect(() => () => {
    if (filePreviewUrl) URL.revokeObjectURL(filePreviewUrl)
  }, [filePreviewUrl])

  useEffect(() => () => {
    previewRequestGenerationRef.current += 1
    smartFillRequestGenerationRef.current += 1
  }, [])

  const invalidatePreview = () => {
    previewRequestGenerationRef.current += 1
    smartFillRequestGenerationRef.current += 1
    formRevisionRef.current += 1
    setRendered(null)
    setFilePreview(null)
    setFilePreviewUrl('')
    setPreviewId('')
    setPreviewPurpose('')
    setRendering(false)
    setOutputFilename('')
    setOutputFormat('')
  }

  const setVariable = (name, value) => {
    setSaved(false)
    invalidatePreview()
    setVariables((prev) => ({ ...prev, [name]: value }))
  }

  const normalizeDiscovery = (res) => {
    const values = res?.variables || res?.values || res?.field_values || {}
    const next = {}
    if (Array.isArray(values)) {
      values.forEach((item) => {
        const key = item?.variable || item?.name || item?.key
        if (!key) return
        next[key] = item?.suggested_value ?? item?.value ?? item?.text ?? ''
      })
      return next
    }
    Object.entries(values).forEach(([key, value]) => {
      if (value && typeof value === 'object') {
        next[key] = value.suggested_value ?? value.value ?? value.text ?? ''
      } else {
        next[key] = value ?? ''
      }
    })
    return next
  }

  const handleSmartFill = async () => {
    if (!matterId.trim()) {
      setSmartFillState('error')
      setSmartFillMessage('Choose a matter before smart fill.')
      return
    }
    const requestGeneration = smartFillRequestGenerationRef.current + 1
    smartFillRequestGenerationRef.current = requestGeneration
    const requestRevision = formRevisionRef.current
    const requestMatterId = matterId.trim()
    setSmartFillState('loading')
    setSmartFillMessage('')
    try {
      const res = await discoverTemplateVariables(template.id, {
        matter_id: requestMatterId,
        variables: fillableNames,
      })
      if (
        smartFillRequestGenerationRef.current !== requestGeneration
        || formRevisionRef.current !== requestRevision
      ) {
        setSmartFillState('idle')
        setSmartFillMessage('Smart-fill results were not applied because the matter or fields changed. Run Smart Fill again if needed.')
        return
      }
      const discovered = normalizeDiscovery(res)
      if (Object.keys(discovered).length === 0) {
        setSmartFillState('empty')
        setSmartFillMessage('No smart-fill values were returned for this template yet.')
        return
      }
      setVariables((prev) => ({ ...prev, ...discovered }))
      invalidatePreview()
      setSaved(false)
      setSmartFillState('ready')
      setSmartFillMessage('Smart-fill values loaded. Review each field before saving.')
    } catch (err) {
      if (smartFillRequestGenerationRef.current !== requestGeneration) return
      if ([404, 405, 501].includes(err?.response?.status)) {
        setSmartFillState('unavailable')
        setSmartFillMessage('Smart fill is not enabled on this server yet. Manual fields are ready for review.')
      } else {
        setSmartFillState('error')
        setSmartFillMessage(getErrorMessage(err, 'Smart fill failed.'))
      }
    }
  }

  const handleRender = async (requestedPdfPurpose = null) => {
    const pdfPurpose = isPdfTemplate
      ? (requestedPdfPurpose || (canSaveToMatter ? 'generation' : 'draft'))
      : null
    if (isPdfTemplate && canSaveToMatter && !matterId.trim()) {
      setError('Choose the destination matter before previewing the exact PDF values for save.')
      return
    }
    if (pdfPurpose === 'activation' && activationUnresolvedNames.length > 0) {
      setError(`Enter representative values for every non-signature PDF field before the activation preview. Missing: ${activationUnresolvedNames.join(', ')}.`)
      return
    }
    const requestGeneration = previewRequestGenerationRef.current + 1
    previewRequestGenerationRef.current = requestGeneration
    const requestVariables = { ...variables }
    const requestMatterId = isPdfTemplate && canSaveToMatter ? matterId.trim() : null
    setRendering(true)
    setError(null)
    try {
      const payload = {
        variables: requestVariables,
        matter_id: requestMatterId,
        ...(isPdfTemplate
          ? { preview_purpose: pdfPurpose }
          : {}),
      }
      if (isFileTemplate) {
        const result = await renderTemplateFile(template.id, payload)
        const nextUrl = URL.createObjectURL(result.blob)
        if (previewRequestGenerationRef.current !== requestGeneration) {
          URL.revokeObjectURL(nextUrl)
          return
        }
        if (isPdfTemplate && !result.previewId) {
          URL.revokeObjectURL(nextUrl)
          throw new Error('The server did not return PDF preview evidence. Preview again before saving or activating.')
        }
        if (isPdfTemplate && result.previewPurpose !== pdfPurpose) {
          URL.revokeObjectURL(nextUrl)
          throw new Error('The server returned preview evidence for a different review purpose. Preview again.')
        }
        setFilePreview({ blob: result.blob, filename: result.filename, contentType: result.contentType })
        setFilePreviewUrl(nextUrl)
        setPreviewId(result.previewId)
        setPreviewPurpose(result.previewPurpose)
        setOutputFilename(result.filename)
        setOutputFormat(isPdfTemplate ? 'pdf' : 'docx')
        setRendered(null)
      } else {
        const res = await renderTemplate(template.id, payload)
        if (previewRequestGenerationRef.current !== requestGeneration) return
        setRendered(res.rendered)
        setFilePreview(null)
        setFilePreviewUrl('')
      }
      setMatterDocId(null)
      setSaved(false)
    } catch (err) {
      if (previewRequestGenerationRef.current === requestGeneration) {
        setError(getErrorMessage(err, 'Render failed.'))
      }
    } finally {
      if (previewRequestGenerationRef.current === requestGeneration) {
        setRendering(false)
      }
    }
  }

  const handleSave = async () => {
    if (!matterId.trim()) return
    if (!canSaveToMatter) {
      setError('Activate this template after verifying its preview before saving a generated document to a matter.')
      return
    }
    if (requiredUnresolvedNames.length > 0) {
      setError(`Complete ${requiredUnresolvedNames.length} required field${requiredUnresolvedNames.length === 1 ? '' : 's'} before saving.`)
      return
    }
    if (isPdfTemplate && !previewId) {
      setError('Preview the exact current PDF values for this matter before saving.')
      return
    }
    if (isDocxTemplate && !filePreview) {
      setError('Download and review the current Word preview before saving it to the matter.')
      return
    }
    if (smartFillState === 'loading') {
      setError('Wait for Smart Fill to finish, or change a field to discard it, before saving.')
      return
    }
    const saveRevision = formRevisionRef.current
    const saveVariables = { ...variables }
    const saveMatterId = matterId.trim()
    const savePreviewId = previewId
    smartFillRequestGenerationRef.current += 1
    setSaving(true)
    setError(null)
    setStorageWarning('')
    try {
      const res = await renderTemplate(template.id, {
        variables: saveVariables,
        matter_id: saveMatterId,
        ...(isPdfTemplate ? { preview_id: savePreviewId } : {}),
      })
      if (formRevisionRef.current !== saveRevision) {
        setError('The form changed while the save was in flight, so this response was not marked Saved. Review the matter document before continuing.')
        return
      }
      setError(null)
      if (!isPdfTemplate) setRendered(res.rendered || rendered)
      setSavedDownloadUrl(res.download_url || '')
      setOutputFilename(res.output_filename || res.filename || outputFilename || '')
      setOutputFormat(res.output_format || res.format || (isPdfTemplate ? 'pdf' : 'markdown'))
      setStorageBackend(res.storage_backend || '')
      setStorageWarning(res.storage_warning || '')
      if (res.matter_document_id) {
        setMatterDocId(res.matter_document_id)
        setSaved(true)
      } else {
        setError('The server rendered the text but did not return a saved matter document.')
      }
    } catch (err) {
      setError(getErrorMessage(err, 'Save failed.'))
    } finally {
      setSaving(false)
    }
  }

  const handleClose = () => {
    if (saving) {
      setError('Save in progress. Keep this window open until the matter document is finalized.')
      return
    }
    onClose()
  }

  return (
    <Modal title={`${canSaveToMatter ? (isPdfTemplate ? 'Generate PDF' : isDocxTemplate ? 'Generate Word Document' : 'Generate Document') : 'Preview Draft'}: ${template.title}`} onClose={handleClose}>
      <div className="space-y-4">
        {error && (
          <div className="text-sm text-brand-rose bg-brand-rose/10 border border-brand-rose/30 px-3 py-2">
            {error}
          </div>
        )}

        {!canSaveToMatter && (
          <div role="status" className="text-sm text-brand-amber bg-brand-amber/10 border border-brand-amber/30 px-3 py-2">
            This template is inactive. Preview and verify it here, then activate it before saving any generated document to a matter.
          </div>
        )}

        <MatterPicker
          matters={matters}
          selectedMatterId={matterId}
          onSelect={(id) => { setMatterId(id); setSaved(false); invalidatePreview() }}
          loading={matterLoading}
          disabled={saving}
        />

        <div>
          <label htmlFor="templatespage-matter-uuid-fallback" className="block text-xs font-medium text-brand-muted mb-0.5">
            Matter UUID fallback
          </label>
          <input id="templatespage-matter-uuid-fallback"
            type="text"
            value={matterId}
            onChange={(e) => { setMatterId(e.target.value); setSaved(false); invalidatePreview() }}
            disabled={saving}
            className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-brand-bg text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent font-mono"
            placeholder="Paste matter UUID if the matter is not listed"
          />
        </div>

        {names.length > 0 && (
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 border border-brand-line rounded bg-brand-bg px-3 py-2">
            <div>
              <p className="text-sm font-medium text-brand-ink">Smart fill</p>
              <p className="text-xs text-brand-muted">
                Pull matter-aware values when the backend endpoint is available.
              </p>
            </div>
            <button
              onClick={handleSmartFill}
              disabled={saving || smartFillState === 'loading' || !matterId.trim()}
              className="flex items-center justify-center gap-2 px-3 py-2 text-sm text-brand-ink border border-brand-line rounded hover:bg-brand-surface-2 disabled:opacity-50"
            >
              <Wand2 size={15} />
              {smartFillState === 'loading' ? 'Filling...' : 'Smart Fill'}
            </button>
          </div>
        )}

        {smartFillMessage && (
          <div className={`text-sm border px-3 py-2 ${
            smartFillState === 'ready'
              ? 'text-brand-green bg-brand-green/10 border-brand-green/30'
              : smartFillState === 'error'
                ? 'text-brand-rose bg-brand-rose/10 border-brand-rose/30'
                : 'text-brand-muted bg-brand-bg border-brand-line'
          }`}>
            {smartFillMessage}
          </div>
        )}

        {names.length > 0 && (
          <div>
            <h3 className="text-sm font-medium text-brand-ink mb-2">
              Fields To Review
            </h3>
            <div className="space-y-2">
              {names.map((name) => {
                const field = fieldDefinitions[name] || {}
                const fieldType = field.field_type || 'text'
                const label = field.label || friendlyVariableLabel(name)
                const inputId = `template-variable-${name}`
                const options = (field.options || []).map((option) => (
                  typeof option === 'object'
                    ? { value: option.value ?? option.name ?? option.label ?? '', label: option.label ?? option.name ?? option.value ?? '' }
                    : { value: option, label: option }
                ))
                return (
                <div key={name} className={fieldType === 'signature' ? 'border border-brand-line rounded bg-brand-bg px-3 py-2' : ''}>
                  {fieldType === 'signature' ? (
                    <p className="block text-xs font-medium text-brand-muted mb-0.5">
                      {label}
                      <span className="font-mono text-brand-muted ml-2">
                        {'{{'}{name}{'}}'}
                      </span>
                    </p>
                  ) : (
                    <label htmlFor={inputId} className="block text-xs font-medium text-brand-muted mb-0.5">
                      {label}{field.required ? ' *' : ''}
                      <span className="font-mono text-brand-muted ml-2">
                        {'{{'}{name}{'}}'}
                      </span>
                    </label>
                  )}
                  {fieldType === 'signature' ? (
                    <p className="text-sm text-brand-muted">
                      Signature area is left blank for signing; it is not populated during document generation.
                      {field.pdf_field_name ? ` PDF field: ${field.pdf_field_name}.` : ''}
                    </p>
                  ) : fieldType === 'checkbox' ? (
                    <label className="inline-flex items-center gap-2 text-sm text-brand-ink py-1">
                      <input
                        id={inputId}
                        type="checkbox"
                        checked={variables[name] === 'true'}
                        onChange={(e) => setVariable(name, e.target.checked ? 'true' : 'false')}
                        disabled={saving}
                        className="h-4 w-4 rounded border-brand-line text-brand-accent focus:ring-brand-accent"
                      />
                      Checked
                    </label>
                  ) : (fieldType === 'choice' || fieldType === 'radio') && options.length > 0 ? (
                    <select
                      id={inputId}
                      value={variables[name] || ''}
                      onChange={(e) => setVariable(name, e.target.value)}
                      disabled={saving}
                      className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-brand-bg text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent"
                    >
                      <option value="">Select {label}</option>
                      {options.map((option) => <option key={String(option.value)} value={option.value}>{option.label}</option>)}
                    </select>
                  ) : fieldType === 'multiline' || field.multiline ? (
                    <textarea
                      id={inputId}
                      rows={3}
                      value={variables[name] || ''}
                      onChange={(e) => setVariable(name, e.target.value)}
                      disabled={saving}
                      className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-brand-bg text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent"
                      placeholder={`Enter ${label}`}
                    />
                  ) : (
                    <input
                      id={inputId}
                      type="text"
                      value={variables[name] || ''}
                      onChange={(e) => setVariable(name, e.target.value)}
                      disabled={saving}
                      className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-brand-bg text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent"
                      placeholder={`Enter ${label}`}
                    />
                  )}
                  {field.pdf_field_name && fieldType !== 'signature' && (
                    <p className="mt-1 text-[11px] text-brand-muted">PDF field: {field.pdf_field_name}{field.page ? ` · Page ${field.page}` : ''}</p>
                  )}
                </div>
                )
              })}
            </div>
            <p className={`mt-2 text-xs ${requiredUnresolvedNames.length ? 'text-brand-amber' : 'text-brand-green'}`} role="status">
              {requiredUnresolvedNames.length
                ? `${requiredUnresolvedNames.length} required field${requiredUnresolvedNames.length === 1 ? '' : 's'} still need review before saving.`
                : 'All required fields are ready.'}
            </p>
            {optionalUnfilledNames.length > 0 && (
              <p className="mt-1 text-xs text-brand-muted">
                {optionalUnfilledNames.length} optional field{optionalUnfilledNames.length === 1 ? '' : 's'} left unfilled; saving is still allowed.
              </p>
            )}
          </div>
        )}

        {names.length === 0 && (
          <p className="text-sm text-brand-muted italic">
            This template has no variables. Preview or save it directly.
          </p>
        )}

        <div className="flex flex-col sm:flex-row gap-3">
          {isPdfTemplate && !canSaveToMatter ? (
            <>
              <button
                onClick={() => handleRender('draft')}
                disabled={rendering || saving}
                className="flex items-center justify-center gap-2 px-4 py-2 text-sm text-brand-ink border border-brand-line bg-brand-surface hover:bg-brand-surface-2 rounded disabled:opacity-50"
              >
                <Eye size={16} />
                {rendering ? 'Rendering...' : 'Preview draft'}
              </button>
              <button
                onClick={() => handleRender('activation')}
                disabled={rendering || saving}
                className="flex items-center justify-center gap-2 px-4 py-2 text-sm text-white bg-brand-ink hover:bg-brand-ink-2 rounded disabled:opacity-50"
              >
                <Check size={16} />
                {rendering ? 'Recording...' : 'Record activation preview'}
              </button>
            </>
          ) : (
            <button
              onClick={() => handleRender()}
              disabled={rendering || saving}
              className="flex items-center justify-center gap-2 px-4 py-2 text-sm text-white bg-brand-ink hover:bg-brand-ink-2 rounded disabled:opacity-50"
            >
              <Eye size={16} />
              {rendering ? 'Rendering...' : 'Preview'}
            </button>
          )}
          <button
            onClick={handleSave}
            disabled={saving || smartFillState === 'loading' || saved || !matterId.trim() || !canSaveToMatter || (isPdfTemplate && !previewId) || (isDocxTemplate && !filePreview)}
            title={!canSaveToMatter
              ? 'Activate this verified template before saving to a matter'
              : (isPdfTemplate && !previewId)
                ? 'Preview the exact current PDF values before saving'
                : (isDocxTemplate && !filePreview)
                  ? 'Download and review the current Word preview before saving'
                : undefined}
            className="flex items-center justify-center gap-2 px-4 py-2 text-sm text-white bg-brand-accent hover:opacity-90 rounded disabled:opacity-50"
          >
            {saved ? (
              <>
                <Check size={16} /> Saved
              </>
            ) : (
              <>
                <Send size={16} />
                {saving ? 'Saving...' : 'Render & Save to Matter'}
              </>
            )}
          </button>
        </div>

        {rendered && (
          <div>
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-medium text-brand-ink">
              {outputFormat === 'pdf' ? 'PDF Preview' : 'Text Preview'}
            </h3>
              <button type="button" onClick={() => downloadRenderedText(rendered, template.title)} className="inline-flex items-center gap-1.5 rounded border border-brand-line px-3 py-1.5 text-xs font-semibold text-brand-ink hover:bg-brand-surface-2">
                <Download size={14} /> Download preview
              </button>
            </div>
            <div className="bg-brand-bg border border-brand-line rounded p-4 whitespace-pre-wrap font-mono text-sm text-brand-ink max-h-96 overflow-y-auto">
              {rendered}
            </div>
          </div>
        )}

        {filePreview && (
          <div>
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <div>
                <h3 className="text-sm font-medium text-brand-ink">{isPdfTemplate ? 'PDF Preview' : 'Generated Word Preview'}</h3>
                <p className="text-xs text-brand-muted">{filePreview.filename}</p>
              </div>
              <button type="button" onClick={() => triggerBlobDownload(filePreview.blob, filePreview.filename)} className="inline-flex items-center gap-1.5 rounded border border-brand-line px-3 py-1.5 text-xs font-semibold text-brand-ink hover:bg-brand-surface-2">
                <Download size={14} /> Download preview
              </button>
            </div>
            {isPdfTemplate ? (
              <>
                <object title={`Preview of ${template.title}`} data={filePreviewUrl} type="application/pdf" className="h-[65vh] min-h-[480px] w-full rounded border border-brand-line bg-white">
                  <p className="p-4 text-sm text-brand-muted">This browser cannot display the PDF inline. Use Download preview instead.</p>
                </object>
                <p className="mt-2 text-xs font-medium text-brand-green" role="status">
                  {previewPurpose === 'generation'
                    ? 'These exact values and this matter are previewed. Inspect every page, then save without changing the fields.'
                    : previewPurpose === 'activation'
                      ? 'Representative activation preview recorded. Inspect every page, then activate this unchanged template.'
                      : 'Draft preview only. This is diagnostic and does not record activation evidence. Use Record activation preview after every field has a representative value.'}
                </p>
              </>
            ) : (
              <div className="rounded border border-brand-green/30 bg-brand-green/10 px-4 py-3 text-sm text-brand-ink">
                Word formatting was preserved. Download and open this generated DOCX to inspect its exact pagination, tables, headers, and footers.
              </div>
            )}
          </div>
        )}

        {matterDocId && (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2 rounded border border-brand-green/30 bg-brand-green/10 px-3 py-2">
              <p className="text-xs text-brand-green">
                Saved to the matter{outputFilename ? ` as ${outputFilename}` : ''}{outputFormat ? ` (${outputFormat.toUpperCase()})` : ''}{storageBackend ? ` in ${storageBackend.replaceAll('_', ' ')}` : ''}.
              </p>
              <a href={savedDownloadUrl || getMatterDocumentDownloadUrl(matterId.trim(), matterDocId)} className="inline-flex items-center gap-1.5 text-xs font-semibold text-brand-accent-2 underline">
                <Download size={14} /> Download saved document
              </a>
            </div>
            {storageWarning && (
              <div role="alert" className="rounded border border-brand-amber/40 bg-brand-amber/10 px-3 py-2 text-xs text-brand-ink">
                {storageWarning}
              </div>
            )}
          </div>
        )}
      </div>
    </Modal>
  )
}

export default function TemplatesPage() {
  const [templates, setTemplates] = useState([])
  const [matters, setMatters] = useState([])
  const [loading, setLoading] = useState(true)
  const [matterLoading, setMatterLoading] = useState(true)
  const [error, setError] = useState(null)
  const [activeTab, setActiveTab] = useState('templates')
  const [showCreate, setShowCreate] = useState(false)
  const [showUpload, setShowUpload] = useState(false)
  const [editTemplate, setEditTemplate] = useState(null)
  const [deleteTarget, setDeleteTarget] = useState(null)
  const [renderTarget, setRenderTarget] = useState(null)

  const load = useCallback(async () => {
    try {
      setError(null)
      const res = await getTemplates({ include_inactive: true })
      setTemplates(res.items || [])
    } catch (err) {
      setError(getErrorMessage(err, 'Failed to load templates.'))
    } finally {
      setLoading(false)
    }
  }, [])

  const loadMatters = useCallback(async () => {
    setMatterLoading(true)
    try {
      const res = await getMattersV2({
        page_size: 100,
        sort_by: 'updated_at',
        sort_dir: 'desc',
      })
      setMatters(normalizeItems(res))
    } catch {
      setMatters([])
    } finally {
      setMatterLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    loadMatters()
  }, [load, loadMatters])

  const handleCreate = async (data) => {
    await createTemplate(data)
    setShowCreate(false)
    await load()
  }

  const handleUploadedTemplate = async () => {
    setShowUpload(false)
    await load()
  }

  const handleUpdate = async (data) => {
    await updateTemplate(editTemplate.id, data)
    setEditTemplate(null)
    await load()
  }

  const handleDelete = async () => {
    try {
      await deleteTemplate(deleteTarget.id)
      setDeleteTarget(null)
      await load()
    } catch (err) {
      setError(getErrorMessage(err, 'Failed to delete template.'))
    }
  }

  const toggleActive = async (tpl) => {
    try {
      await updateTemplate(tpl.id, { is_active: !tpl.is_active })
      await load()
    } catch (err) {
      setError(getErrorMessage(err, 'Failed to toggle template status.'))
    }
  }

  const bodyPreview = (body) => {
    const text = String(body || '')
    return text.length > 100 ? `${text.slice(0, 100)}...` : text
  }

  const activeGenerationTemplates = templates.filter((tpl) => tpl.is_active && !isSourceBackedTemplateMissing(tpl))
  const activeTemplateCount = templates.filter((tpl) => tpl.is_active).length
  const variableCount = templates.reduce((sum, tpl) => sum + getTemplateVariables(tpl).length, 0)
  const selectedTemplate = activeGenerationTemplates[0] || null

  const renderTemplatesPanel = () => {
    if (templates.length === 0) {
      return (
        <div className="text-center py-16 bg-brand-surface-2 border border-brand-line rounded-lg">
          <FileText
            size={48}
            className="mx-auto text-brand-muted mb-4 opacity-30"
          />
          <p className="text-brand-muted">
            No templates yet. Create the first automation template.
          </p>
        </div>
      )
    }

    return (
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {templates.map((tpl) => {
          const vars = getTemplateVariables(tpl)
          const sourceMissing = isSourceBackedTemplateMissing(tpl)
          return (
            <div
              key={tpl.id}
              className="bg-brand-surface-2 border border-brand-line rounded-lg shadow-sm hover:shadow-md transition-shadow"
            >
              <div className="p-4">
                <div className="flex items-start justify-between mb-2">
                  <h3 className="font-medium text-brand-ink truncate">
                    {tpl.title}
                  </h3>
                  <span
                    className={`text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full font-semibold shrink-0 ml-2 ${
                      CATEGORY_COLORS[tpl.category] || CATEGORY_COLORS.other
                    }`}
                  >
                    {CATEGORY_LABELS[tpl.category] || tpl.category}
                  </span>
                </div>
                <p className="text-xs text-brand-muted font-mono mb-3 min-h-10">
                  {bodyPreview(tpl.body)}
                </p>

                {sourceMissing && (
                  <div role="alert" className="mb-3 border border-brand-amber/40 rounded bg-brand-amber/10 px-3 py-2">
                    <p className="text-xs font-semibold text-brand-ink">Source missing — recreate this PDF template</p>
                    <p className="mt-0.5 text-[11px] text-brand-muted">This older record cannot generate documents. Upload the original Word or PDF file again; ordinary PDFs and scans are supported.</p>
                    <button
                      type="button"
                      onClick={() => setShowUpload(true)}
                      className="mt-2 text-xs font-semibold text-brand-accent-2 underline"
                    >
                      Recreate from Upload Sample
                    </button>
                  </div>
                )}

                <div className="flex flex-wrap items-center gap-2 mb-3">
                  <span className="inline-flex items-center text-xs font-semibold uppercase text-brand-accent-2 border border-brand-line rounded px-2 py-1">
                    {tpl.format || 'markdown'}
                  </span>
                  <span className="inline-flex items-center gap-1 text-xs text-brand-muted border border-brand-line rounded px-2 py-1">
                    <ClipboardList size={12} />
                    {vars.length} field{vars.length === 1 ? '' : 's'}
                  </span>
                  <button
                    onClick={() => toggleActive(tpl)}
                    disabled={sourceMissing}
                    title={sourceMissing ? 'Recreate the template from its original source document before activating it' : undefined}
                    className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                      tpl.is_active ? 'bg-brand-green' : 'bg-brand-muted/30'
                    }`}
                    aria-label={tpl.is_active ? 'Deactivate template' : 'Activate template'}
                  >
                    <span
                      className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
                        tpl.is_active ? 'translate-x-4' : 'translate-x-1'
                      }`}
                    />
                  </button>
                  <span className="text-xs text-brand-muted">
                    {tpl.is_active ? 'Active' : 'Inactive'}
                  </span>
                </div>

                <div className="flex items-center gap-2 pt-2 border-t border-brand-line">
                  <button
                    onClick={() => setEditTemplate(tpl)}
                    className="flex items-center gap-1 px-3 py-1.5 text-xs text-brand-muted hover:text-brand-ink border border-brand-line rounded"
                  >
                    <Pencil size={14} />
                    Edit
                  </button>
                  <button
                    onClick={() => setRenderTarget(tpl)}
                    disabled={sourceMissing}
                    title={sourceMissing
                      ? 'Recreate the template from its original source document before previewing it'
                      : (tpl.is_active ? 'Generate a document' : 'Preview this draft; activate it before saving to a matter')}
                    className="flex items-center gap-1 px-3 py-1.5 text-xs text-brand-muted hover:text-brand-ink border border-brand-line rounded disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    {tpl.is_active ? <Sparkles size={14} /> : <Eye size={14} />}
                    {tpl.is_active ? 'Generate' : 'Preview draft'}
                  </button>
                  <button
                    onClick={() => setDeleteTarget(tpl)}
                    className="flex items-center gap-1 px-3 py-1.5 text-xs text-brand-rose hover:text-red-800 border border-brand-line rounded ml-auto"
                  >
                    <Trash2 size={14} />
                    Delete
                  </button>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    )
  }

  const renderGeneratePanel = () => (
    <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_340px] gap-5">
      <div className="bg-brand-surface-2 border border-brand-line rounded-lg p-5">
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-brand-ink">Generate / Smart Fill</h2>
            <p className="text-sm text-brand-muted mt-1">
              Start from an active template, choose a matter, review fields, preview, then save the generated document to the matter.
            </p>
          </div>
          <button
            onClick={() => selectedTemplate && setRenderTarget(selectedTemplate)}
            disabled={!selectedTemplate || isSourceBackedTemplateMissing(selectedTemplate)}
            title={isSourceBackedTemplateMissing(selectedTemplate) ? 'Re-upload the source document before generating' : undefined}
            className="flex items-center justify-center gap-2 px-4 py-2 text-sm text-white bg-brand-ink hover:bg-brand-ink-2 rounded disabled:opacity-50"
          >
            <Sparkles size={16} />
            Generate
          </button>
        </div>

        <div className="mt-5 grid grid-cols-1 md:grid-cols-2 gap-3">
          {activeGenerationTemplates.slice(0, 6).map((tpl) => {
            const vars = getTemplateVariables(tpl)
            return (
              <button
                key={tpl.id}
                onClick={() => setRenderTarget(tpl)}
                className="text-left border border-brand-line rounded p-4 bg-brand-bg hover:border-brand-accent/50"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-brand-ink truncate">{tpl.title}</p>
                    <p className="text-xs text-brand-muted mt-1">
                      {vars.length} review field{vars.length === 1 ? '' : 's'}
                    </p>
                  </div>
                  <span className="text-[10px] uppercase px-2 py-0.5 rounded-full font-semibold shrink-0 bg-brand-green/10 text-brand-green">
                    Active
                  </span>
                </div>
              </button>
            )
          })}
          {activeGenerationTemplates.length === 0 && (
            <div className="border border-dashed border-brand-line rounded p-6 text-center md:col-span-2">
              <p className="text-sm text-brand-muted">Activate a verified template before generating matter documents.</p>
            </div>
          )}
        </div>
      </div>

      <div className="bg-brand-surface-2 border border-brand-line rounded-lg p-5">
        <h3 className="text-sm font-semibold text-brand-ink">Reliable template workflow</h3>
        <div className="mt-4 space-y-3">
          <div className="flex gap-3">
            <Check size={16} className="text-brand-green shrink-0 mt-0.5" />
            <p className="text-sm text-brand-muted">Upload the Word or PDF document your team already uses. Text PDFs and scans are read automatically, and the original source is retained.</p>
          </div>
          <div className="flex gap-3">
            <Eye size={16} className="text-brand-amber shrink-0 mt-0.5" />
            <p className="text-sm text-brand-muted">Review detected replacement values, then preview with realistic matter data before activation.</p>
          </div>
          <div className="flex gap-3">
            <Download size={16} className="text-brand-muted shrink-0 mt-0.5" />
            <p className="text-sm text-brand-muted">Generated documents keep their source format: DOCX stays editable; final PDFs are flattened.</p>
          </div>
        </div>
      </div>
    </div>
  )

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full bg-brand-bg">
        <div className="text-brand-muted">Loading document automation...</div>
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto bg-brand-bg p-4 md:p-6">
      <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4 mb-5">
        <div>
          <h1 className="text-xl font-semibold text-brand-ink">
            Document Automation
          </h1>
          <p className="text-sm text-brand-muted mt-1">
            Build high-fidelity templates, smart-fill matter data, preview the result, and save finalized documents to the matter file.
          </p>
        </div>
        <div className="flex flex-col sm:flex-row gap-2">
          <button
            onClick={() => setShowUpload(true)}
            className="flex items-center justify-center gap-2 px-4 py-2 text-sm text-brand-ink border border-brand-line rounded hover:bg-brand-surface-2"
          >
            <Upload size={18} />
            Upload Sample
          </button>
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center justify-center gap-2 px-4 py-2 text-sm text-white bg-brand-ink hover:bg-brand-ink-2 rounded"
          >
            <Plus size={18} />
            New Template
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5">
        <div className="bg-brand-surface-2 border border-brand-line rounded p-4">
          <p className="text-xs text-brand-muted uppercase tracking-wider">Templates</p>
          <p className="text-2xl font-semibold text-brand-ink mt-1">{templates.length}</p>
        </div>
        <div className="bg-brand-surface-2 border border-brand-line rounded p-4">
          <p className="text-xs text-brand-muted uppercase tracking-wider">Active</p>
          <p className="text-2xl font-semibold text-brand-ink mt-1">{activeTemplateCount}</p>
        </div>
        <div className="bg-brand-surface-2 border border-brand-line rounded p-4">
          <p className="text-xs text-brand-muted uppercase tracking-wider">Mapped Fields</p>
          <p className="text-2xl font-semibold text-brand-ink mt-1">{variableCount}</p>
        </div>
        <div className="bg-brand-surface-2 border border-brand-line rounded p-4">
          <p className="text-xs text-brand-muted uppercase tracking-wider">Recent Matters</p>
          <p className="text-2xl font-semibold text-brand-ink mt-1">{matters.length}</p>
        </div>
      </div>

      <div className="mb-5 overflow-x-auto border-b border-brand-line">
        <div className="flex min-w-max gap-1">
          {TABS.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              onClick={() => setActiveTab(key)}
              className={`flex items-center gap-2 px-4 py-3 text-sm border-b-2 transition-colors ${
                activeTab === key
                  ? 'border-brand-accent text-brand-ink'
                  : 'border-transparent text-brand-muted hover:text-brand-ink'
              }`}
            >
              <Icon size={16} />
              {label}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="mb-4 text-sm text-brand-rose bg-brand-rose/10 border border-brand-rose/30 px-4 py-3">
          {error}
        </div>
      )}

      {activeTab === 'templates' && renderTemplatesPanel()}
      {activeTab === 'generate' && renderGeneratePanel()}

      {showCreate && (
        <Modal title="Create Template" onClose={() => setShowCreate(false)}>
          <TemplateForm
            onSubmit={handleCreate}
            onCancel={() => setShowCreate(false)}
          />
        </Modal>
      )}

      {showUpload && (
          <Modal title="Create Template From Sample" wide onClose={() => setShowUpload(false)}>
          <UploadTemplateForm
            onCreated={handleUploadedTemplate}
            onCancel={() => setShowUpload(false)}
          />
        </Modal>
      )}

      {editTemplate && (
        <Modal title="Edit Template" onClose={() => setEditTemplate(null)}>
          <TemplateForm
            initial={editTemplate}
            onSubmit={handleUpdate}
            onCancel={() => setEditTemplate(null)}
          />
        </Modal>
      )}

      {deleteTarget && (
        <ConfirmDialog
          message={`Delete "${deleteTarget.title}"? This cannot be undone.`}
          onConfirm={handleDelete}
          onCancel={() => setDeleteTarget(null)}
        />
      )}

      {renderTarget && (
        <RenderModal
          template={renderTarget}
          matters={matters}
          matterLoading={matterLoading}
          onClose={() => setRenderTarget(null)}
        />
      )}
    </div>
  )
}
