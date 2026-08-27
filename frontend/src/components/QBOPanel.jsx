import { useState, useEffect, useCallback } from 'react'
import { useConfirm } from './dialog/ConfirmProvider'
import {
  getQBOStatus,
  connectQBO,
  disconnectQBO,
  getQBOItems,
  getQBOAccounts,
  updateQBOSettings,
  getQBOMappings,
  upsertQBOMapping,
  syncAllToQBO,
} from '../api'

const QBO_GREEN = '#2CA01C'

// Billing source types that can be mapped to QBO service items
const SOURCE_TYPES = [
  { source_type: 'time_entry', expense_category: null, label: 'Time Entry (billable hours)' },
  { source_type: 'expense', expense_category: null, label: 'Expense (general)' },
  { source_type: 'expense', expense_category: 'court filing', label: 'Expense — Court / Filing Fee' },
  { source_type: 'expense', expense_category: 'process service', label: 'Expense — Service of Process' },
  { source_type: 'expense', expense_category: 'certified mail', label: 'Expense — Certified Mail' },
  { source_type: 'expense', expense_category: 'investigator', label: 'Expense — Investigator' },
  { source_type: 'expense', expense_category: 'expert/consultant', label: 'Expense — Expert / Consultant' },
  { source_type: 'expense', expense_category: 'records retrieval', label: 'Expense — Records Retrieval' },
  { source_type: 'expense', expense_category: 'research/database', label: 'Expense — Research / Database' },
  { source_type: 'expense', expense_category: 'copies/printing', label: 'Expense — Copies / Printing' },
  { source_type: 'expense', expense_category: 'postage/courier', label: 'Expense — Postage / Courier' },
  { source_type: 'expense', expense_category: 'travel/mileage/parking', label: 'Expense — Travel / Mileage / Parking' },
  { source_type: 'expense', expense_category: 'lodging', label: 'Expense — Lodging' },
  { source_type: 'expense', expense_category: 'interpreter/translation', label: 'Expense — Interpreter / Translation' },
  { source_type: 'expense', expense_category: 'filing_fee', label: 'Expense — Filing Fee' },
  { source_type: 'expense', expense_category: 'travel', label: 'Expense — Travel' },
  { source_type: 'expense', expense_category: 'courier', label: 'Expense — Courier' },
  { source_type: 'expense', expense_category: 'other', label: 'Expense — Other' },
  { source_type: 'flat_fee', expense_category: null, label: 'Flat Fee' },
  { source_type: 'adjustment', expense_category: null, label: 'Adjustment / Discount' },
]

function relTime(iso) {
  if (!iso) return 'never'
  const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

export default function QBOPanel() {
  const confirmAction = useConfirm()
  const [status, setStatus] = useState(null)
  const [items, setItems] = useState([])
  const [accounts, setAccounts] = useState([])
  const [selectedAccount, setSelectedAccount] = useState('')
  const [mappings, setMappings] = useState({})      // key: "source_type:expense_category" → { qbo_item_id, qbo_item_name }
  const [pendingMappings, setPendingMappings] = useState({})
  const [loading, setLoading] = useState(true)
  const [itemsLoading, setItemsLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [syncResult, setSyncResult] = useState(null)
  const [error, setError] = useState(null)

  const mappingKey = (source_type, expense_category) =>
    `${source_type}:${expense_category ?? ''}`

  const loadStatus = useCallback(async () => {
    try {
      const s = await getQBOStatus()
      setStatus(s)
      setSelectedAccount(s.qbo_ar_account_id || '')
    } catch {
      setError('Failed to load QBO status.')
    } finally {
      setLoading(false)
    }
  }, [])

  const loadMappings = useCallback(async () => {
    try {
      const list = await getQBOMappings()
      const map = {}
      list.forEach((m) => {
        map[mappingKey(m.source_type, m.expense_category)] = {
          qbo_item_id: m.qbo_item_id,
          qbo_item_name: m.qbo_item_name,
        }
      })
      setMappings(map)
    } catch {
      // non-fatal — mappings just start empty
    }
  }, [])

  const loadItems = useCallback(async () => {
    setItemsLoading(true)
    try {
      const [itemList, accountList] = await Promise.all([getQBOItems(), getQBOAccounts()])
      setItems(itemList)
      setAccounts(accountList)
    } catch {
      setError('Failed to fetch QBO items. Make sure QBO is connected.')
    } finally {
      setItemsLoading(false)
    }
  }, [])

  useEffect(() => {
    loadStatus()
    loadMappings()
  }, [loadStatus, loadMappings])

  useEffect(() => {
    if (status?.connected) loadItems()
  }, [status?.connected, loadItems])

  const handleConnect = async () => {
    try {
      const { redirect_url } = await connectQBO()
      window.location.href = redirect_url
    } catch {
      setError('Failed to initiate QBO OAuth.')
    }
  }

  const handleDisconnect = async () => {
    if (!await confirmAction({ title: 'Disconnect QuickBooks Online?', message: 'Existing synced data in QuickBooks will remain.', confirmLabel: 'Disconnect', destructive: true })) return
    try {
      await disconnectQBO()
      await loadStatus()
    } catch {
      setError('Failed to disconnect QBO.')
    }
  }

  const handleMappingChange = (source_type, expense_category, itemId) => {
    const key = mappingKey(source_type, expense_category)
    const item = items.find((i) => i.id === itemId)
    setPendingMappings((prev) => ({
      ...prev,
      [key]: item ? { qbo_item_id: item.id, qbo_item_name: item.name } : null,
    }))
  }

  const handleSaveAccount = async () => {
    setSaving(true)
    try {
      const account = accounts.find((item) => item.id === selectedAccount)
      await updateQBOSettings({
        qbo_ar_account_id: account?.id || "",
        qbo_ar_account_name: account?.name || "",
      })
      await loadStatus()
    } catch {
      setError("Failed to save the QBO accounts-receivable account.")
    } finally {
      setSaving(false)
    }
  }
  const handleSaveMappings = async () => {
    setSaving(true)
    try {
      const toSave = SOURCE_TYPES.filter(({ source_type, expense_category }) => {
        const key = mappingKey(source_type, expense_category)
        return pendingMappings[key] !== undefined
      })
      await Promise.all(
        toSave.map(({ source_type, expense_category }) => {
          const key = mappingKey(source_type, expense_category)
          const val = pendingMappings[key]
          if (!val) return Promise.resolve()
          return upsertQBOMapping({ source_type, expense_category, ...val })
        })
      )
      await loadMappings()
      setPendingMappings({})
    } catch {
      setError('Failed to save mappings.')
    } finally {
      setSaving(false)
    }
  }

  const handleSyncAll = async () => {
    setSyncing(true)
    setSyncResult(null)
    try {
      const result = await syncAllToQBO()
      setSyncResult(result)
      await loadStatus()
    } catch {
      setError('Sync failed.')
    } finally {
      setSyncing(false)
    }
  }

  if (loading) {
    return (
      <div className="flex justify-center py-12">
        <div className="w-8 h-8 border-4 border-brand-ink border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  const hasPending = Object.keys(pendingMappings).length > 0

  return (
    <div className="space-y-6">
      {error && (
        <div className="px-4 py-3 bg-red-50 border border-red-200 rounded-xl text-red-700 text-xs font-medium">
          {error}
          <button className="ml-2 underline" onClick={() => setError(null)}>Dismiss</button>
        </div>
      )}

      {/* Connection card */}
      <div className="bg-brand-surface border border-brand-line rounded-xl p-6">
        <div className="flex items-center justify-between mb-4">
          <div>
            <div className="flex items-center gap-2">
              {/* QBO logo text mark */}
              <span className="font-bold text-base" style={{ color: QBO_GREEN }}>QuickBooks Online</span>
              <span className={`inline-block px-2.5 py-0.5 rounded-full text-xs font-bold ${
                status?.connected
                  ? 'bg-green-100 text-green-700'
                  : 'bg-red-100 text-red-700'
              }`}>
                {status?.connected ? 'Connected' : 'Disconnected'}
              </span>
              {status?.connected && status?.sandbox_mode && (
                <span className="inline-block px-2 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-700">
                  Sandbox
                </span>
              )}
            </div>
            {status?.connected && (
              <p className="mt-1 text-xs text-brand-ink-2 font-sans">
                Realm ID: {status.qbo_realm_id}
                {status.last_sync_at && ` · Last sync ${relTime(status.last_sync_at)}`}
                {status.last_sync_status && ` (${status.last_sync_status})`}
              </p>
            )}
            {status?.last_sync_error && (
              <p className="mt-1 text-xs text-red-600 font-mono bg-red-50 px-2 py-1 rounded">
                {status.last_sync_error}
              </p>
            )}
          </div>
          <div className="flex gap-2">
            {status?.connected ? (
              <button
                onClick={handleDisconnect}
                className="px-4 py-2 border border-brand-line text-brand-ink font-sans text-xs font-medium rounded-lg hover:bg-red-50 hover:border-red-200 hover:text-red-700 transition-colors"
              >
                Disconnect
              </button>
            ) : (
              <button
                onClick={handleConnect}
                className="px-4 py-2 font-sans text-xs font-medium rounded-lg text-white transition-colors"
                style={{ background: QBO_GREEN }}
              >
                Connect to QuickBooks
              </button>
            )}
          </div>
        </div>

        {!status?.connected && (
          <p className="text-sm text-brand-ink-2 font-sans">
            Connect your QuickBooks Online account to push invoices directly from this app.
            You'll need an admin Intuit account for the company you want to sync with.
          </p>
        )}
      </div>

      {status?.connected && (
        <div className="bg-brand-surface border border-brand-line rounded-xl p-6">
          <h3 className="text-brand-ink font-sans text-base font-bold">Accounts receivable</h3>
          <p className="text-xs text-brand-ink-2 mt-1 mb-3">
            Choose the QuickBooks A/R account that will receive LawHand invoices.
          </p>
          <div className="flex gap-2">
            <select
              value={selectedAccount}
              onChange={(event) => setSelectedAccount(event.target.value)}
              className="flex-1 text-xs px-3 py-2 border border-brand-line rounded-lg bg-brand-surface"
            >
              <option value="">Use the QuickBooks default</option>
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>{account.name}</option>
              ))}
            </select>
            <button
              type="button"
              onClick={handleSaveAccount}
              disabled={saving}
              className="px-4 py-2 rounded-lg text-white text-xs font-medium disabled:opacity-50"
              style={{ background: QBO_GREEN }}
            >
              Save
            </button>
          </div>
        </div>
      )}
      {/* Field mapping card */}
      {status?.connected && (
        <div className="bg-brand-surface border border-brand-line rounded-xl p-6">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-brand-ink font-sans text-base font-bold">Field Mapping</h3>
              <p className="text-xs text-brand-ink-2 mt-1">
                Map each billing type to a QBO service item. Used when syncing invoice line items.
              </p>
            </div>
            {hasPending && (
              <button
                onClick={handleSaveMappings}
                disabled={saving}
                className="px-4 py-2 font-sans text-xs font-medium rounded-lg text-white disabled:opacity-50"
                style={{ background: QBO_GREEN }}
              >
                {saving ? 'Saving…' : 'Save Mappings'}
              </button>
            )}
          </div>

          {itemsLoading ? (
            <p className="text-sm text-brand-ink-2">Loading QBO items…</p>
          ) : items.length === 0 ? (
            <p className="text-sm text-brand-ink-2">
              No service items found in QBO. Create at least one Service-type item in QuickBooks first.
            </p>
          ) : (
            <div className="space-y-2">
              {SOURCE_TYPES.map(({ source_type, expense_category, label }) => {
                const key = mappingKey(source_type, expense_category)
                const saved = mappings[key]
                const pending = pendingMappings[key]
                const currentId = pending !== undefined
                  ? (pending?.qbo_item_id ?? '')
                  : (saved?.qbo_item_id ?? '')

                return (
                  <div
                    key={key}
                    className="flex items-center gap-4 px-3 py-2.5 rounded-lg bg-brand-bg"
                  >
                    <span className="text-sm text-brand-ink font-sans flex-1">{label}</span>
                    <select
                      value={currentId}
                      onChange={(e) => handleMappingChange(source_type, expense_category, e.target.value)}
                      className="text-xs font-sans px-2 py-1.5 border border-brand-line rounded-lg bg-brand-surface text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent min-w-[200px]"
                    >
                      <option value="">— Not mapped —</option>
                      {items.map((item) => (
                        <option key={item.id} value={item.id}>{item.name}</option>
                      ))}
                    </select>
                    {saved?.qbo_item_id && pending === undefined && (
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                        <path d="M5 13l4 4L19 7" stroke={QBO_GREEN} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* Sync card */}
      {status?.connected && (
        <div className="bg-brand-surface border border-brand-line rounded-xl p-6">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-brand-ink font-sans text-base font-bold">Sync Invoices</h3>
              <p className="text-xs text-brand-ink-2 mt-1">
                Push all unsynced invoices to QuickBooks in one go.
              </p>
              {syncResult && (
                <p className={`text-xs mt-2 font-medium ${syncResult.status === 'success' ? 'text-green-700' : 'text-amber-700'}`}>
                  {syncResult.status === 'success'
                    ? `Synced ${syncResult.invoices_synced} invoice${syncResult.invoices_synced !== 1 ? 's' : ''} successfully.`
                    : `Partial sync: ${syncResult.invoices_synced} synced, ${syncResult.errors?.length ?? 0} error(s).`}
                </p>
              )}
            </div>
            <button
              onClick={handleSyncAll}
              disabled={syncing}
              className="px-4 py-2 font-sans text-xs font-medium rounded-lg text-white disabled:opacity-50 flex items-center gap-2"
              style={{ background: QBO_GREEN }}
            >
              {syncing && (
                <span className="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin" />
              )}
              {syncing ? 'Syncing…' : 'Sync All'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
