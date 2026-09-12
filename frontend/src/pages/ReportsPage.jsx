import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  getReportsBundle,
  getRealizationReport,
  getWipReport,
  getAgingReport,
  downloadRealizationCsv,
  downloadWipCsv,
  downloadAgingCsv,
  triggerBlobDownload,
} from '../api'
import {
  BarChart2, Users, AlertTriangle, Scale, ArrowLeft,
  Download, TrendingUp, Clock, FileWarning, ChevronUp, ChevronDown,
} from 'lucide-react'

function StatRow({ label, value }) {
  return (
    <div className="flex justify-between items-center py-1.5 border-b border-brand-line last:border-0">
      <span className="text-sm text-brand-muted capitalize">{label.replace(/_/g, ' ')}</span>
      <span className="text-sm font-mono font-semibold text-brand-ink">{value}</span>
    </div>
  )
}

function Card({ icon: Icon, title, children }) {
  return (
    <div className="bg-brand-surface-2 border border-brand-line p-6 flex flex-col gap-4">
      <div className="flex items-center gap-2 border-b border-brand-line pb-3">
        <Icon className="w-4 h-4 text-brand-accent" strokeWidth={1.5} />
        <h2 className="font-serif font-semibold text-brand-ink">{title}</h2>
      </div>
      {children}
    </div>
  )
}

// ── Formatters ────────────────────────────────────────────────────────────

const money = (v) => '$' + Number(v || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const percent = (v) => Number(v || 0).toFixed(1) + '%'
const hours = (v) => Number(v || 0).toFixed(1)

// ── Date range ──────────────────────────────────────────────────────────────

const isoDate = (d) => {
  // Local date parts, not toISOString(): a firm in UTC-7 asking for "this month"
  // on the 1st must not get a window that starts in the previous month.
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

const startOfMonth = (d) => new Date(d.getFullYear(), d.getMonth(), 1)

export const RANGE_PRESETS = [
  { key: 'all', label: 'All time', range: () => ({}) },
  {
    key: 'this_month',
    label: 'This month',
    range: () => {
      const now = new Date()
      return { start: isoDate(startOfMonth(now)), end: isoDate(now) }
    },
  },
  {
    key: 'last_month',
    label: 'Last month',
    range: () => {
      const now = new Date()
      const first = new Date(now.getFullYear(), now.getMonth() - 1, 1)
      const last = new Date(now.getFullYear(), now.getMonth(), 0)
      return { start: isoDate(first), end: isoDate(last) }
    },
  },
  {
    key: 'ytd',
    label: 'Year to date',
    range: () => {
      const now = new Date()
      return { start: isoDate(new Date(now.getFullYear(), 0, 1)), end: isoDate(now) }
    },
  },
]

function DateRangeBar({ preset, custom, onPreset, onCustom }) {
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-brand-line pb-3">
      <span className="text-xs font-semibold uppercase tracking-wider text-brand-muted">Period</span>
      {RANGE_PRESETS.map(option => (
        <button
          key={option.key}
          type="button"
          aria-pressed={preset === option.key}
          onClick={() => onPreset(option.key)}
          className={`px-3 py-1.5 text-xs font-semibold border transition-colors ${
            preset === option.key
              ? 'border-brand-accent text-brand-accent'
              : 'border-brand-line text-brand-muted hover:text-brand-ink'
          }`}
        >
          {option.label}
        </button>
      ))}
      <label className="flex items-center gap-1.5 text-xs text-brand-muted">
        <span className="sr-only">Start date</span>
        <input
          type="date"
          aria-label="Start date"
          value={custom.start || ''}
          onChange={(e) => onCustom({ ...custom, start: e.target.value })}
          className="border border-brand-line bg-brand-surface px-2 py-1 text-xs text-brand-ink"
        />
      </label>
      <span className="text-xs text-brand-muted">to</span>
      <label className="flex items-center gap-1.5 text-xs text-brand-muted">
        <span className="sr-only">End date</span>
        <input
          type="date"
          aria-label="End date"
          value={custom.end || ''}
          onChange={(e) => onCustom({ ...custom, end: e.target.value })}
          className="border border-brand-line bg-brand-surface px-2 py-1 text-xs text-brand-ink"
        />
      </label>
    </div>
  )
}

// ── Reusable sortable table ─────────────────────────────────────────────────

function ReportTable({ columns, rows, emptyText, totalRow = null }) {
  const [sortKey, setSortKey] = useState(null)
  const [sortDir, setSortDir] = useState('asc')

  if (!rows || rows.length === 0) {
    return <p className="text-sm text-brand-muted italic py-4">{emptyText}</p>
  }

  const handleSort = (key) => {
    if (sortKey === key) {
      setSortDir(d => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir('asc')
    }
  }

  const sortedRows = [...rows]
  if (sortKey) {
    sortedRows.sort((a, b) => {
      const av = a[sortKey]
      const bv = b[sortKey]
      let cmp
      if (typeof av === 'number' && typeof bv === 'number') {
        cmp = av - bv
      } else {
        cmp = String(av ?? '').localeCompare(String(bv ?? ''))
      }
      return sortDir === 'asc' ? cmp : -cmp
    })
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-brand-line">
            {columns.map(col => (
              <th
                key={col.key}
                scope="col"
                aria-sort={sortKey === col.key ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
                className={`py-2 px-2 text-xs font-semibold uppercase tracking-wider text-brand-muted ${col.align === 'right' ? 'text-right' : 'text-left'}`}
              >
                <button
                  type="button"
                  onClick={() => handleSort(col.key)}
                  className="inline-flex items-center gap-1 uppercase tracking-wider hover:text-brand-ink transition-colors"
                >
                  {col.label}
                  {sortKey === col.key && (
                    sortDir === 'asc'
                      ? <ChevronUp className="w-3 h-3" />
                      : <ChevronDown className="w-3 h-3" />
                  )}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sortedRows.map((row, idx) => (
            <tr key={row.matter_id ?? idx} className="border-b border-brand-line last:border-0">
              {columns.map(col => (
                <td
                  key={col.key}
                  className={`py-2 px-2 ${col.align === 'right' ? 'text-right font-mono text-brand-ink' : 'text-brand-ink'}`}
                >
                  {col.key === 'matter_name' && row.matter_id ? (
                    <Link
                      to={`/matters/${row.matter_id}?tab=billing`}
                      className="text-brand-ink underline decoration-brand-line underline-offset-2 hover:text-brand-accent"
                    >
                      {row[col.key] ?? '—'}
                    </Link>
                  ) : (
                    col.format ? col.format(row[col.key]) : (row[col.key] ?? '—')
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
        {totalRow && (
          <tfoot>
            <tr className="border-t-2 border-brand-line">
              {columns.map((col, idx) => (
                <td
                  key={col.key}
                  className={`py-2 px-2 font-semibold ${col.align === 'right' ? 'text-right font-mono text-brand-ink' : 'text-brand-ink'}`}
                >
                  {idx === 0
                    ? 'Total'
                    : (col.key in totalRow ? (col.format ? col.format(totalRow[col.key]) : totalRow[col.key]) : '')}
                </td>
              ))}
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  )
}

// ── Billing report tab (shared shape for realization / wip / aging) ────────

function BillingReportTab({ title, icon: Icon, columns, emptyText, loader, onDownload, downloadFilename, supportsRange = true, totals }) {
  const [rows, setRows] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [downloadError, setDownloadError] = useState(null)
  const [downloading, setDownloading] = useState(false)
  const [preset, setPreset] = useState('all')
  const [custom, setCustom] = useState({ start: '', end: '' })
  const [reloadKey, setReloadKey] = useState(0)

  // A custom date wins over the preset chips; otherwise the chip defines the window.
  const range = (() => {
    if (!supportsRange) return {}
    if (custom.start || custom.end) return { start: custom.start, end: custom.end }
    return (RANGE_PRESETS.find(p => p.key === preset) || RANGE_PRESETS[0]).range()
  })()
  const rangeKey = `${range.start || ''}:${range.end || ''}`

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    loader(range)
      .then(data => { if (!cancelled) setRows(data) })
      .catch(e => { if (!cancelled) setError(e.message || 'Failed to load report') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [rangeKey, reloadKey])

  const handlePreset = (key) => {
    setCustom({ start: '', end: '' })
    setPreset(key)
  }

  const handleDownload = async () => {
    setDownloadError(null)
    setDownloading(true)
    try {
      const blob = await onDownload(range)
      triggerBlobDownload(blob, downloadFilename)
    } catch (e) {
      setDownloadError(e.message || 'Failed to download CSV')
    } finally {
      setDownloading(false)
    }
  }

  // Column totals, so the reader never has to add a page of money by hand.
  const totalRow = totals && rows && rows.length > 0
    ? totals.reduce((acc, key) => ({ ...acc, [key]: rows.reduce((sum, r) => sum + Number(r[key] || 0), 0) }), {})
    : null

  return (
    <div className="bg-brand-surface-2 border border-brand-line p-6 flex flex-col gap-4">
      <div className="flex items-center justify-between border-b border-brand-line pb-3">
        <div className="flex items-center gap-2">
          <Icon className="w-4 h-4 text-brand-accent" strokeWidth={1.5} />
          <h2 className="font-serif font-semibold text-brand-ink">{title}</h2>
        </div>
        <button
          onClick={handleDownload}
          disabled={downloading}
          className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-brand-muted hover:text-brand-accent transition-colors border border-brand-line px-3 py-1.5 disabled:opacity-50"
        >
          <Download className="w-3.5 h-3.5" />
          {downloading ? 'Preparing…' : 'Download CSV'}
        </button>
      </div>

      {supportsRange && (
        <DateRangeBar preset={preset} custom={custom} onPreset={handlePreset} onCustom={setCustom} />
      )}

      {downloadError && (
        <p className="text-xs text-brand-rose">{downloadError}</p>
      )}

      {loading && (
        <p className="text-sm text-brand-muted">Loading…</p>
      )}

      {!loading && error && (
        <div role="alert" className="flex items-center gap-3">
          <p className="text-sm text-brand-rose">{error}</p>
          <button
            type="button"
            onClick={() => setReloadKey(k => k + 1)}
            className="text-xs font-semibold uppercase tracking-wider text-brand-muted hover:text-brand-accent border border-brand-line px-3 py-1.5"
          >
            Retry
          </button>
        </div>
      )}

      {!loading && !error && (
        <ReportTable columns={columns} rows={rows} emptyText={emptyText} totalRow={totalRow} />
      )}
    </div>
  )
}

// ── Tab definitions ──────────────────────────────────────────────────────────

// The overview card is a summary, not the task list. Long lists live in Tasks.
const OVERDUE_PREVIEW_LIMIT = 8

const TABS = [
  { key: 'overview', label: 'Overview' },
  { key: 'realization', label: 'Realization' },
  { key: 'wip', label: 'WIP' },
  { key: 'aging', label: 'A/R Aging' },
]

export default function ReportsPage() {
  const navigate = useNavigate()
  const [bundle, setBundle] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [activeTab, setActiveTab] = useState('overview')

  const loadBundle = useCallback(() => {
    setLoading(true)
    setError(null)
    getReportsBundle()
      .then(setBundle)
      .catch(e => setError(e.message || 'Failed to load reports'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { loadBundle() }, [loadBundle])

  if (loading) {
    return (
      <div className="px-6 py-16 text-center">
        <p className="text-brand-muted font-serif">Loading reports…</p>
      </div>
    )
  }

  if (error || !bundle) {
    return (
      <div role="alert" className="px-6 py-16 flex flex-col items-center gap-4">
        <p className="text-brand-rose font-serif">{error || 'Reports are unavailable.'}</p>
        <button
          type="button"
          onClick={loadBundle}
          className="text-xs font-semibold uppercase tracking-wider text-brand-muted hover:text-brand-accent border border-brand-line px-4 py-2"
        >
          Retry
        </button>
      </div>
    )
  }

  const { matter_status, intake_funnel, overdue_tasks, generated_at } = bundle

  return (
    <div className="">
      {/* Header */}
      <div className="border-b border-brand-line bg-brand-surface-2 px-6 py-4 flex items-center gap-4">
        <button
          onClick={() => navigate(-1)}
          className="text-brand-muted hover:text-brand-ink transition-colors"
          title="Back"
        >
          <ArrowLeft className="w-4 h-4" />
        </button>
        <Scale className="w-5 h-5 text-brand-accent" strokeWidth={1.5} />
        <h1 className="font-serif font-semibold text-xl tracking-tight">Firm Reports</h1>
        <span className="ml-auto text-xs text-brand-muted font-mono">
          Generated {new Date(generated_at).toLocaleString()}
        </span>
      </div>

      {/* Tab bar */}
      <div role="tablist" aria-label="Report sections" className="border-b border-brand-line bg-brand-surface-2 px-6 flex items-center gap-1">
        {TABS.map(tab => (
          <button
            key={tab.key}
            role="tab"
            id={`reports-tab-${tab.key}`}
            aria-selected={activeTab === tab.key}
            aria-controls={`reports-panel-${tab.key}`}
            onClick={() => setActiveTab(tab.key)}
            className={`px-4 py-2.5 text-sm font-semibold transition-colors border-b-2 -mb-px ${
              activeTab === tab.key
                ? 'text-brand-accent border-brand-accent'
                : 'text-brand-muted border-transparent hover:text-brand-ink'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === 'overview' && (
        <div role="tabpanel" id="reports-panel-overview" aria-labelledby="reports-tab-overview" className="max-w-5xl mx-auto px-6 py-8 grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">

          {/* Matter Status Card */}
          <Card icon={BarChart2} title="Matter Status">
            <div className="flex justify-between items-center">
              <span className="text-sm text-brand-muted">Total Matters</span>
              <span className="text-2xl font-mono font-bold text-brand-ink">{matter_status.total_matters}</span>
            </div>
            {Object.keys(matter_status.by_status).length > 0 && (
              <div>
                <p className="text-xs font-semibold uppercase tracking-wider text-brand-muted mb-2">By Status</p>
                {Object.entries(matter_status.by_status).map(([k, v]) => (
                  <StatRow key={k} label={k} value={v} />
                ))}
              </div>
            )}
            {Object.keys(matter_status.by_type).length > 0 && (
              <div>
                <p className="text-xs font-semibold uppercase tracking-wider text-brand-muted mb-2">By Type</p>
                {Object.entries(matter_status.by_type).map(([k, v]) => (
                  <StatRow key={k} label={k} value={v} />
                ))}
              </div>
            )}
            {Object.keys(matter_status.by_risk_level).length > 0 && (
              <div>
                <p className="text-xs font-semibold uppercase tracking-wider text-brand-muted mb-2">By Risk Level</p>
                {Object.entries(matter_status.by_risk_level).map(([k, v]) => (
                  <StatRow key={k} label={k} value={v} />
                ))}
              </div>
            )}
          </Card>

          {/* Intake Funnel Card */}
          <Card icon={Users} title="Intake Funnel">
            <div className="flex justify-between items-center">
              <span className="text-sm text-brand-muted">Total Leads</span>
              <span className="text-2xl font-mono font-bold text-brand-ink">{intake_funnel.total_leads}</span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-sm text-brand-muted">Conversion Rate</span>
              <span className="text-lg font-mono font-semibold text-brand-accent">
                {(intake_funnel.conversion_rate * 100).toFixed(1)}%
              </span>
            </div>
            {Object.keys(intake_funnel.by_status).length > 0 && (
              <div>
                <p className="text-xs font-semibold uppercase tracking-wider text-brand-muted mb-2">By Status</p>
                {Object.entries(intake_funnel.by_status).map(([k, v]) => (
                  <StatRow key={k} label={k} value={v} />
                ))}
              </div>
            )}
          </Card>

          {/* Overdue Tasks Card */}
          <Card icon={AlertTriangle} title="Overdue Tasks">
            <div className="flex justify-between items-center">
              <span className="text-sm text-brand-muted">Total Overdue</span>
              <span className={`text-2xl font-mono font-bold ${overdue_tasks.total_overdue > 0 ? 'text-brand-rose' : 'text-brand-ink'}`}>
                {overdue_tasks.total_overdue}
              </span>
            </div>
            {overdue_tasks.tasks.length === 0 ? (
              <p className="text-sm text-brand-muted italic">No overdue tasks</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-brand-line">
                      <th className="text-left py-1.5 text-brand-muted font-semibold uppercase tracking-wider">Task</th>
                      <th className="text-left py-1.5 text-brand-muted font-semibold uppercase tracking-wider">Due</th>
                      <th className="text-left py-1.5 text-brand-muted font-semibold uppercase tracking-wider">Matter</th>
                    </tr>
                  </thead>
                  <tbody>
                    {overdue_tasks.tasks.slice(0, OVERDUE_PREVIEW_LIMIT).map(task => (
                      <tr key={task.id} className="border-b border-brand-line last:border-0">
                        <td className="py-1.5 text-brand-ink truncate max-w-[120px]" title={task.title}>
                          <Link to="/tasks" className="underline decoration-brand-line underline-offset-2 hover:text-brand-accent">
                            {task.title}
                          </Link>
                        </td>
                        <td className="py-1.5 font-mono text-brand-rose whitespace-nowrap">{task.due_date}</td>
                        <td className="py-1.5 text-brand-muted truncate max-w-[120px]" title={task.matter_name || ''}>{task.matter_name || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {overdue_tasks.tasks.length > OVERDUE_PREVIEW_LIMIT && (
                  <Link to="/tasks" className="mt-3 inline-block text-xs font-semibold uppercase tracking-wider text-brand-accent">
                    View all {overdue_tasks.tasks.length} in Tasks
                  </Link>
                )}
              </div>
            )}
          </Card>

        </div>
      )}

      {activeTab === 'realization' && (
        <div role="tabpanel" id="reports-panel-realization" aria-labelledby="reports-tab-realization" className="max-w-5xl mx-auto px-6 py-8">
          <BillingReportTab
            title="Billing Realization"
            icon={TrendingUp}
            loader={getRealizationReport}
            onDownload={downloadRealizationCsv}
            downloadFilename="realization.csv"
            emptyText="No billable activity"
            totals={['billable_hours', 'billable_amount', 'invoiced_amount', 'collected_amount']}
            columns={[
              { key: 'matter_name', label: 'Matter' },
              { key: 'billable_hours', label: 'Worked Hours', align: 'right', format: hours },
              { key: 'billable_amount', label: 'Worked Value', align: 'right', format: money },
              { key: 'invoiced_amount', label: 'Invoiced', align: 'right', format: money },
              { key: 'collected_amount', label: 'Collected', align: 'right', format: money },
              { key: 'billing_realization_pct', label: 'Billed %', align: 'right', format: percent },
              { key: 'collection_pct', label: 'Collected %', align: 'right', format: percent },
            ]}
          />
        </div>
      )}

      {activeTab === 'wip' && (
        <div role="tabpanel" id="reports-panel-wip" aria-labelledby="reports-tab-wip" className="max-w-5xl mx-auto px-6 py-8">
          <BillingReportTab
            title="Work In Progress"
            icon={Clock}
            loader={getWipReport}
            onDownload={downloadWipCsv}
            downloadFilename="wip.csv"
            emptyText="No work in progress"
            totals={['wip_hours', 'wip_value']}
            columns={[
              { key: 'matter_name', label: 'Matter' },
              { key: 'wip_hours', label: 'WIP Hours', align: 'right', format: hours },
              { key: 'wip_value', label: 'WIP Value', align: 'right', format: money },
            ]}
          />
        </div>
      )}

      {activeTab === 'aging' && (
        <div role="tabpanel" id="reports-panel-aging" aria-labelledby="reports-tab-aging" className="max-w-5xl mx-auto px-6 py-8">
          <BillingReportTab
            title="Accounts Receivable Aging"
            icon={FileWarning}
            loader={getAgingReport}
            onDownload={downloadAgingCsv}
            downloadFilename="aging.csv"
            emptyText="No outstanding receivables"
            supportsRange={false}
            totals={['current', 'days_1_30', 'days_31_60', 'days_61_90', 'days_90_plus', 'total']}
            columns={[
              { key: 'matter_name', label: 'Matter' },
              { key: 'current', label: 'Current', align: 'right', format: money },
              { key: 'days_1_30', label: '1–30', align: 'right', format: money },
              { key: 'days_31_60', label: '31–60', align: 'right', format: money },
              { key: 'days_61_90', label: '61–90', align: 'right', format: money },
              { key: 'days_90_plus', label: '90+', align: 'right', format: money },
              { key: 'total', label: 'Total Due', align: 'right', format: money },
            ]}
          />
        </div>
      )}
    </div>
  )
}
