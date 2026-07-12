import React, { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { format, parseISO } from 'date-fns'
import {
  getPortalCase,
  createPortalAsset, updatePortalAsset, submitPortalAsset, decidePortalAsset,
  uploadPortalDocument, downloadPortalDocumentUrl,
  createPortalProposal,
} from '../api'
import { Handshake, Plus, Upload, Download, Send, Check, X, AlertTriangle } from 'lucide-react'
import { useConfirm } from '../components/dialog/ConfirmProvider'
import { useToast } from '../components/toast/useToast'

function Pill({ children, color }) {
  const cls = color || 'bg-brand-ink/5 text-brand-ink-2 border-brand-ink/10'
  return <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wider font-sans border ${cls}`}>{children}</span>
}

function StatusBadge({ status }) {
  const cfg = {
    draft: 'bg-brand-bg-soft text-brand-muted border-brand-line',
    submitted: 'bg-blue-50 text-blue-700 border-blue-200',
    attorney_approved: 'bg-brand-green/10 text-brand-green border-brand-green/20',
    sent: 'bg-purple-50 text-purple-700 border-purple-200',
    opposing_approved: 'bg-brand-green/10 text-brand-green border-brand-green/20',
    disputed: 'bg-brand-rose/10 text-brand-rose border-brand-rose/20',
  }[status?.toLowerCase()] || 'bg-brand-bg-soft text-brand-muted border-brand-line'
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wider font-sans border ${cfg}`}>
      {status?.replace(/_/g, ' ') || 'draft'}
    </span>
  )
}

const ASSET_KINDS = ['asset', 'debt']
const ASSET_CATEGORIES = ['real_property', 'bank_account', 'retirement', 'investment', 'vehicle', 'business', 'personal_property', 'credit_card', 'mortgage', 'loan', 'other']
const OWNERSHIP = ['party_a', 'party_b', 'joint']

export default function PortalCasePage() {
  const confirmAction = useConfirm()
  const toast = useToast()
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [tab, setTab] = useState('my-assets')

  const [showAssetForm, setShowAssetForm] = useState(false)
  const [editingAsset, setEditingAsset] = useState(null)
  const [assetForm, setAssetForm] = useState({ description: '', kind: 'asset', category: '', value: '', owned_by: '', notes: '' })
  const [savingAsset, setSavingAsset] = useState(false)
  const [actionError, setActionError] = useState(null)

  const [showProposalForm, setShowProposalForm] = useState(false)
  const [proposalForm, setProposalForm] = useState({ title: '', body: '' })
  const [savingProposal, setSavingProposal] = useState(false)

  const [uploading, setUploading] = useState(false)
  const [uploadDesc, setUploadDesc] = useState('')
  const fileRef = useRef(null)

  const [deciding, setDeciding] = useState(null)
  const [disputeReason, setDisputeReason] = useState('')

  const caseId = data?.case?.id
  const isOpposing = data?.party_role === 'opposing_party'

  const loadCase = useCallback(() => {
    setLoading(true)
    getPortalCase()
      .then(setData)
      .catch((err) => {
        if (err?.response?.status === 401) setError('Please accept your invitation first, or log in if you have an account.')
        else setError('Failed to load case.')
      })
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { loadCase() }, [loadCase])

  const inputCls = 'w-full border border-brand-line rounded-lg px-4 py-2.5 text-[14px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface transition-all'
  const labelCls = 'block text-[11px] font-bold text-brand-ink uppercase tracking-widest mb-1.5'

  const resetAssetForm = () => {
    setAssetForm({ description: '', kind: 'asset', category: '', value: '', owned_by: '', notes: '' })
    setEditingAsset(null); setShowAssetForm(false); setActionError(null)
  }

  const startEditAsset = (a) => {
    setAssetForm({
      description: a.description || '', kind: a.kind || 'asset', category: a.category || '',
      value: a.value ?? '', owned_by: a.owned_by || '', notes: a.notes || '',
    })
    setEditingAsset(a); setShowAssetForm(true)
  }

  const handleSaveAsset = async () => {
    if (!assetForm.description.trim()) return
    setSavingAsset(true); setActionError(null)
    try {
      if (editingAsset) await updatePortalAsset(editingAsset.id, assetForm, caseId)
      else await createPortalAsset(assetForm, caseId)
      resetAssetForm(); loadCase()
    } catch (err) { setActionError(err?.response?.data?.detail || 'Failed to save.') }
    finally { setSavingAsset(false) }
  }

  const handleSubmitAsset = async (asset) => {
    if (!await confirmAction({ title: 'Submit asset for review?', message: 'You will not be able to edit it after submission.', confirmLabel: 'Submit asset' })) return
    try { await submitPortalAsset(asset.id, caseId); loadCase() } catch (error) { toast.error('Asset was not submitted', { message: error?.response?.data?.detail || 'Please try again.' }) }
  }

  const handleDecide = async (asset, decision) => {
    setDeciding(asset.id)
    try {
      await decidePortalAsset(asset.id, { decision, dispute_reason: decision === 'disputed' ? disputeReason : null }, caseId)
      setDisputeReason(''); loadCase()
    } catch (error) { toast.error('Decision was not submitted', { message: error?.response?.data?.detail || 'Please try again.' }) } finally { setDeciding(null) }
  }

  const handleUpload = async () => {
    const file = fileRef.current?.files?.[0]
    if (!file) return
    setUploading(true)
    try {
      await uploadPortalDocument(file, uploadDesc || undefined, caseId)
      setUploadDesc(''); if (fileRef.current) fileRef.current.value = ''; loadCase()
    } catch { setActionError('Upload failed.') } finally { setUploading(false) }
  }

  const handleCreateProposal = async () => {
    if (!proposalForm.title.trim()) return
    setSavingProposal(true)
    try {
      await createPortalProposal(proposalForm, caseId)
      setProposalForm({ title: '', body: '' }); setShowProposalForm(false); loadCase()
    } catch (err) { setActionError(err?.response?.data?.detail || 'Failed.') } finally { setSavingProposal(false) }
  }

  if (loading) {
    return <div className="min-h-screen bg-brand-bg flex items-center justify-center"><div className="w-8 h-8 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" /></div>
  }

  if (error || !data) {
    return (
      <div className="min-h-screen bg-brand-bg flex items-center justify-center px-4">
        <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm max-w-md w-full p-10 text-center">
          <Handshake size={48} className="mx-auto text-brand-rose mb-6" strokeWidth={1.5} />
          <h1 className="font-serif font-bold text-2xl text-brand-ink mb-3">Access Denied</h1>
          <p className="text-brand-ink-2 font-sans text-sm leading-relaxed mb-6">{error || 'Unable to load case.'}</p>
          <button onClick={() => navigate('/portal/accept')} className="px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 transition-all shadow-sm">Enter Invite Token</button>
        </div>
      </div>
    )
  }

  const { case: c, party_role, my_assets = [], shared_assets = [], documents = [], proposals = [] } = data

  return (
    <div className="min-h-screen bg-brand-bg">
      <div className="bg-brand-surface border-b border-brand-line px-8 py-4 flex items-center justify-between sticky top-0 z-30">
        <div className="flex items-center gap-3">
          <Handshake size={20} className="text-brand-accent" />
          <span className="font-serif font-bold text-lg text-brand-ink tracking-tight">{c?.case_name || 'Mediation Portal'}</span>
        </div>
        <span className="text-[12px] font-sans font-medium text-brand-muted uppercase tracking-wide">{party_role?.replace(/_/g, ' ') || ''}</span>
      </div>

      <div className="max-w-[1100px] mx-auto px-8 py-10">
        <div className="mb-8">
          <h1 className="font-serif text-3xl font-bold text-brand-ink tracking-tight mb-3">{c?.case_name || 'Mediation Case'}</h1>
          <div className="flex items-center gap-3 flex-wrap">
            <span className="text-[14px] text-brand-ink-2 font-sans font-medium bg-brand-ink/5 border border-brand-ink/10 px-3 py-1 rounded-md">
              {c?.party_a || 'Party A'} <span className="text-brand-muted">v.</span> {c?.party_b || 'Party B'}
            </span>
            {c?.dispute_type && <Pill>{c.dispute_type}</Pill>}
            {c?.status && <Pill color="bg-brand-green/10 text-brand-green border-brand-green/20">{c.status}</Pill>}
          </div>
        </div>

        <div className="flex gap-1 border-b border-brand-line mb-8 overflow-x-auto">
          {[
            { key: 'my-assets', label: 'My Assets & Debts', count: my_assets.length },
            { key: 'shared-assets', label: 'Shared With Me', count: shared_assets.length },
            { key: 'documents', label: 'Documents', count: documents.length },
            { key: 'proposals', label: 'Proposals', count: proposals.length },
          ].map((t) => (
            <button key={t.key} onClick={() => setTab(t.key)} className={`px-4 py-2.5 text-sm font-sans font-medium whitespace-nowrap border-b-2 -mb-px transition-colors ${tab === t.key ? 'border-brand-ink text-brand-ink' : 'border-transparent text-brand-muted hover:text-brand-ink-2'}`}>
              {t.label} <span className="ml-1.5 text-[11px] opacity-60">({t.count})</span>
            </button>
          ))}
        </div>

        {actionError && (
          <div className="bg-brand-rose/10 border border-brand-rose/20 rounded-xl px-5 py-4 mb-6 text-brand-rose text-sm font-sans flex items-start gap-3">
            <AlertTriangle size={16} className="shrink-0 mt-0.5" /> {actionError}
            <button onClick={() => setActionError(null)} className="ml-auto"><X size={14} /></button>
          </div>
        )}

        {/* My Assets tab */}
        {tab === 'my-assets' && (
          <div className="space-y-6">
            <div className="flex justify-between items-center">
              <h2 className="font-serif font-bold text-xl text-brand-ink">My Assets & Debts</h2>
              <button onClick={() => { resetAssetForm(); setShowAssetForm((v) => !v) }} className="flex items-center gap-2 px-4 py-2 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 transition-all shadow-sm"><Plus size={16} /> Add Item</button>
            </div>

            {showAssetForm && (
              <div className="bg-brand-surface border border-brand-line rounded-2xl p-6 shadow-sm">
                <h3 className="font-serif font-bold text-lg text-brand-ink mb-5">{editingAsset ? 'Edit Item' : 'New Asset or Debt'}</h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-5 mb-5">
                  <div className="md:col-span-2">
                    <label htmlFor="portalcasepage-description" className={labelCls}>Description *</label>
                    <input id="portalcasepage-description" type="text" value={assetForm.description} onChange={(e) => setAssetForm((p) => ({ ...p, description: e.target.value }))} className={inputCls} placeholder="e.g., 123 Main St residence" />
                  </div>
                  <div><label htmlFor="portalcasepage-type" className={labelCls}>Type</label><select id="portalcasepage-type" value={assetForm.kind} onChange={(e) => setAssetForm((p) => ({ ...p, kind: e.target.value }))} className={inputCls}>{ASSET_KINDS.map((k) => <option key={k} value={k}>{k.charAt(0).toUpperCase() + k.slice(1)}</option>)}</select></div>
                  <div><label htmlFor="portalcasepage-category" className={labelCls}>Category</label><select id="portalcasepage-category" value={assetForm.category} onChange={(e) => setAssetForm((p) => ({ ...p, category: e.target.value }))} className={inputCls}><option value="">--</option>{ASSET_CATEGORIES.map((c) => <option key={c} value={c}>{c.replace(/_/g, ' ')}</option>)}</select></div>
                  <div><label htmlFor="portalcasepage-value" className={labelCls}>Value</label><input id="portalcasepage-value" type="number" value={assetForm.value} onChange={(e) => setAssetForm((p) => ({ ...p, value: e.target.value }))} className={inputCls} placeholder="0.00" /></div>
                  <div><label htmlFor="portalcasepage-owned-by" className={labelCls}>Owned By</label><select id="portalcasepage-owned-by" value={assetForm.owned_by} onChange={(e) => setAssetForm((p) => ({ ...p, owned_by: e.target.value }))} className={inputCls}><option value="">--</option>{OWNERSHIP.map((o) => <option key={o} value={o}>{o.replace(/_/g, ' ')}</option>)}</select></div>
                  <div className="md:col-span-2"><label htmlFor="portalcasepage-notes" className={labelCls}>Notes</label><textarea id="portalcasepage-notes" value={assetForm.notes} onChange={(e) => setAssetForm((p) => ({ ...p, notes: e.target.value }))} rows={2} className={`${inputCls} resize-none`} /></div>
                </div>
                <div className="flex gap-3 justify-end">
                  <button onClick={resetAssetForm} className="px-5 py-2.5 text-brand-ink-2 text-sm font-sans font-medium hover:text-brand-ink transition-colors">Cancel</button>
                  <button onClick={handleSaveAsset} disabled={savingAsset || !assetForm.description.trim()} className="px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 disabled:bg-brand-line disabled:text-brand-muted transition-all shadow-sm">
                    {savingAsset ? 'Saving...' : editingAsset ? 'Save Changes' : 'Add Item'}
                  </button>
                </div>
              </div>
            )}

            {my_assets.length === 0 ? (
              <div className="bg-brand-surface border border-brand-line rounded-2xl p-16 text-center shadow-sm"><p className="text-brand-ink font-serif text-lg font-bold mb-1">No items yet</p><p className="text-brand-muted text-sm font-sans">Add your assets and debts for disclosure. Submit them for attorney review when ready.</p></div>
            ) : (
              <div className="bg-brand-surface border border-brand-line rounded-2xl overflow-x-auto shadow-sm">
                <table className="min-w-full text-left">
                  <thead><tr className="bg-brand-bg-soft/50 border-b border-brand-line">{['Description','Type','Category','Value','Owned By','Status','Actions'].map((h) => <th key={h} className="px-5 py-3 text-[11px] font-bold text-brand-muted uppercase tracking-widest font-sans">{h}</th>)}</tr></thead>
                  <tbody className="divide-y divide-brand-line">
                    {my_assets.map((a) => (
                      <tr key={a.id} className="hover:bg-brand-bg-soft transition-colors">
                        <td className="px-5 py-3 text-[13px] font-sans font-semibold text-brand-ink">{a.description}</td>
                        <td className="px-5 py-3"><Pill color={a.kind === 'asset' ? 'bg-brand-green/10 text-brand-green border-brand-green/20' : 'bg-brand-rose/10 text-brand-rose border-brand-rose/20'}>{a.kind}</Pill></td>
                        <td className="px-5 py-3 text-[13px] font-sans text-brand-ink-2">{a.category ? a.category.replace(/_/g, ' ') : '--'}</td>
                        <td className="px-5 py-3 text-[13px] font-sans font-medium text-brand-ink-2">{a.value ? Number(a.value).toLocaleString('en-US', { style: 'currency', currency: 'USD' }) : '--'}</td>
                        <td className="px-5 py-3 text-[13px] font-sans text-brand-ink-2">{a.owned_by ? a.owned_by.replace(/_/g, ' ') : '--'}</td>
                        <td className="px-5 py-3"><StatusBadge status={a.status} /></td>
                        <td className="px-5 py-3">
                          {a.status === 'draft' && (
                            <div className="flex items-center gap-1.5">
                              <button onClick={() => startEditAsset(a)} className="px-2.5 py-1.5 text-[11px] font-sans font-semibold uppercase rounded-md border hover:bg-brand-bg-soft">Edit</button>
                              <button onClick={() => handleSubmitAsset(a)} className="px-2.5 py-1.5 text-[11px] font-sans font-semibold uppercase rounded-md border bg-brand-ink text-white hover:bg-brand-ink-2 flex items-center gap-1"><Send size={11} /> Submit</button>
                            </div>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* Shared Assets tab */}
        {tab === 'shared-assets' && (
          <div className="space-y-6">
            <h2 className="font-serif font-bold text-xl text-brand-ink">Shared With Me</h2>
            <p className="text-brand-muted text-sm font-sans -mt-4">{isOpposing ? 'Assets the other party has sent for your review.' : 'Assets sent to the opposing party.'}</p>
            {shared_assets.length === 0 ? (
              <div className="bg-brand-surface border border-brand-line rounded-2xl p-16 text-center shadow-sm"><p className="text-brand-ink font-serif text-lg font-bold mb-1">Nothing shared yet</p><p className="text-brand-muted text-sm font-sans">No assets have been sent to you for review.</p></div>
            ) : (
              <div className="bg-brand-surface border border-brand-line rounded-2xl overflow-x-auto shadow-sm">
                <table className="min-w-full text-left">
                  <thead><tr className="bg-brand-bg-soft/50 border-b border-brand-line">{['Description','Type','Category','Value','Owned By','Status',''].map((h) => <th key={h} className="px-5 py-3 text-[11px] font-bold text-brand-muted uppercase tracking-widest font-sans">{h}</th>)}</tr></thead>
                  <tbody className="divide-y divide-brand-line">
                    {shared_assets.map((a) => (
                      <tr key={a.id} className="hover:bg-brand-bg-soft transition-colors">
                        <td className="px-5 py-3 text-[13px] font-sans font-semibold text-brand-ink">{a.description}</td>
                        <td className="px-5 py-3"><Pill color={a.kind === 'asset' ? 'bg-brand-green/10 text-brand-green border-brand-green/20' : 'bg-brand-rose/10 text-brand-rose border-brand-rose/20'}>{a.kind}</Pill></td>
                        <td className="px-5 py-3 text-[13px] font-sans text-brand-ink-2">{a.category ? a.category.replace(/_/g, ' ') : '--'}</td>
                        <td className="px-5 py-3 text-[13px] font-sans font-medium text-brand-ink-2">{a.value ? Number(a.value).toLocaleString('en-US', { style: 'currency', currency: 'USD' }) : '--'}</td>
                        <td className="px-5 py-3 text-[13px] font-sans text-brand-ink-2">{a.owned_by ? a.owned_by.replace(/_/g, ' ') : '--'}</td>
                        <td className="px-5 py-3"><StatusBadge status={a.status} /></td>
                        <td className="px-5 py-3">
                          {isOpposing && a.status === 'sent' && (
                            deciding === a.id ? (
                              <div className="flex items-center gap-2">
                                <input type="text" value={disputeReason} onChange={(e) => setDisputeReason(e.target.value)} placeholder="Reason..." className="border border-brand-line rounded px-2 py-1 text-[12px] w-28" />
                                <button onClick={() => handleDecide(a, 'approved')} className="p-1 text-brand-green hover:bg-brand-green/10 rounded"><Check size={16} /></button>
                                <button onClick={() => handleDecide(a, 'disputed')} className="p-1 text-brand-rose hover:bg-brand-rose/10 rounded"><X size={16} /></button>
                              </div>
                            ) : (
                              <div className="flex items-center gap-1.5">
                                <button onClick={() => { setDeciding(a.id); setDisputeReason('') }} className="px-2.5 py-1.5 text-[11px] font-sans font-semibold uppercase rounded-md border bg-brand-green/10 text-brand-green hover:bg-brand-green/20 flex items-center gap-1"><Check size={11} /> Approve</button>
                                <button onClick={() => handleDecide(a, 'disputed')} className="px-2.5 py-1.5 text-[11px] font-sans font-semibold uppercase rounded-md border bg-brand-rose/10 text-brand-rose hover:bg-brand-rose/20 flex items-center gap-1"><X size={11} /> Dispute</button>
                              </div>
                            )
                          )}
                          {a.status === 'opposing_approved' && <span className="text-[11px] text-brand-green font-sans font-semibold">Approved</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* Documents tab */}
        {tab === 'documents' && (
          <div className="space-y-6">
            <div className="flex justify-between items-center">
              <h2 className="font-serif font-bold text-xl text-brand-ink">Documents</h2>
              <div className="flex items-center gap-3">
                <input type="text" value={uploadDesc} onChange={(e) => setUploadDesc(e.target.value)} placeholder="Description (optional)" className="border border-brand-line rounded-lg px-3 py-2 text-[13px] font-sans w-48" />
                <input type="file" ref={fileRef} className="hidden" />
                <button onClick={() => fileRef.current?.click()} className="flex items-center gap-1.5 px-3 py-2 bg-brand-surface border border-brand-line text-brand-ink text-sm font-sans font-medium rounded-lg hover:border-brand-ink"><Upload size={14} /> Choose File</button>
                <button onClick={handleUpload} disabled={uploading || !fileRef.current?.files?.[0]} className="flex items-center gap-1.5 px-3 py-2 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-ink-2 disabled:bg-brand-line disabled:text-brand-muted">{uploading ? 'Uploading...' : 'Upload'}</button>
              </div>
            </div>
            {documents.length === 0 ? (
              <div className="bg-brand-surface border border-brand-line rounded-2xl p-16 text-center shadow-sm"><p className="text-brand-ink font-serif text-lg font-bold mb-1">No documents</p><p className="text-brand-muted text-sm font-sans">Upload supporting documents here.</p></div>
            ) : (
              <div className="bg-brand-surface border border-brand-line rounded-2xl overflow-x-auto shadow-sm">
                <table className="min-w-full text-left">
                  <thead><tr className="bg-brand-bg-soft/50 border-b border-brand-line">{['Filename','Description','Type','Size','Uploaded',''].map((h) => <th key={h} className="px-5 py-3 text-[11px] font-bold text-brand-muted uppercase tracking-widest font-sans">{h}</th>)}</tr></thead>
                  <tbody className="divide-y divide-brand-line">
                    {documents.map((d) => (
                      <tr key={d.id} className="hover:bg-brand-bg-soft">
                        <td className="px-5 py-3 text-[13px] font-sans font-semibold text-brand-ink">{d.filename}</td>
                        <td className="px-5 py-3 text-[13px] text-brand-ink-2">{d.description || '--'}</td>
                        <td className="px-5 py-3 text-[13px] text-brand-ink-2">{d.content_type || '--'}</td>
                        <td className="px-5 py-3 text-[13px] text-brand-ink-2">{d.file_size ? `${(d.file_size / 1024).toFixed(1)} KB` : '--'}</td>
                        <td className="px-5 py-3 text-[13px] text-brand-ink-2">{d.created_at ? (() => { try { return format(parseISO(d.created_at), 'MMM d, yyyy') } catch { return d.created_at } })() : '--'}</td>
                        <td className="px-5 py-3"><a href={downloadPortalDocumentUrl(d.id, caseId)} className="flex items-center gap-1 px-2.5 py-1.5 text-[11px] font-sans font-semibold uppercase rounded-md border hover:bg-brand-bg-soft text-brand-ink"><Download size={13} /> DL</a></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* Proposals tab */}
        {tab === 'proposals' && (
          <div className="space-y-6">
            <div className="flex justify-between items-center">
              <h2 className="font-serif font-bold text-xl text-brand-ink">Settlement Proposals</h2>
              <button onClick={() => setShowProposalForm((v) => !v)} className="flex items-center gap-2 px-4 py-2 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 shadow-sm"><Plus size={16} /> New Proposal</button>
            </div>
            {showProposalForm && (
              <div className="bg-brand-surface border border-brand-line rounded-2xl p-6 shadow-sm">
                <h3 className="font-serif font-bold text-lg text-brand-ink mb-5">New Proposal</h3>
                <div className="space-y-4 mb-5">
                  <div><label htmlFor="portalcasepage-title" className={labelCls}>Title *</label><input id="portalcasepage-title" type="text" value={proposalForm.title} onChange={(e) => setProposalForm((p) => ({ ...p, title: e.target.value }))} className={inputCls} placeholder="e.g., Initial Settlement Offer" /></div>
                  <div><label htmlFor="portalcasepage-details" className={labelCls}>Details</label><textarea id="portalcasepage-details" value={proposalForm.body} onChange={(e) => setProposalForm((p) => ({ ...p, body: e.target.value }))} rows={4} className={`${inputCls} resize-none`} placeholder="Describe your proposal terms..." /></div>
                </div>
                <div className="flex gap-3 justify-end">
                  <button onClick={() => { setShowProposalForm(false); setProposalForm({ title: '', body: '' }) }} className="px-5 py-2.5 text-brand-ink-2 text-sm font-sans font-medium hover:text-brand-ink">Cancel</button>
                  <button onClick={handleCreateProposal} disabled={savingProposal || !proposalForm.title.trim()} className="px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 disabled:bg-brand-line disabled:text-brand-muted shadow-sm">{savingProposal ? 'Sending...' : 'Send Proposal'}</button>
                </div>
              </div>
            )}
            {proposals.length === 0 ? (
              <div className="bg-brand-surface border border-brand-line rounded-2xl p-16 text-center shadow-sm"><p className="text-brand-ink font-serif text-lg font-bold mb-1">No proposals yet</p><p className="text-brand-muted text-sm font-sans">Create a proposal to begin settlement negotiations.</p></div>
            ) : (
              <div className="space-y-4">
                {proposals.slice().reverse().map((p) => (
                  <div key={p.id} className="bg-brand-surface border border-brand-line rounded-2xl p-6 shadow-sm">
                    <div className="flex items-start justify-between mb-3">
                      <div>
                        <h3 className="font-serif font-bold text-lg text-brand-ink">{p.title}</h3>
                        <div className="flex items-center gap-3 mt-1">
                          {p.proposed_by_name && <span className="text-[12px] text-brand-muted font-sans uppercase">{p.proposed_by_name}</span>}
                          <Pill color={p.status === 'open' ? 'bg-brand-amber/10 text-brand-amber border-brand-amber/20' : p.status === 'accepted' ? 'bg-brand-green/10 text-brand-green border-brand-green/20' : 'bg-brand-rose/10 text-brand-rose border-brand-rose/20'}>{p.status}</Pill>
                        </div>
                      </div>
                      <span className="text-[12px] text-brand-muted">{p.created_at ? (() => { try { return format(parseISO(p.created_at), 'MMM d, yyyy') } catch { return p.created_at } })() : ''}</span>
                    </div>
                    {p.body && <p className="text-[14px] text-brand-ink-2 font-sans leading-relaxed whitespace-pre-wrap">{p.body}</p>}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
