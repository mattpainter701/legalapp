import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
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

// ── Reusable sortable table ─────────────────────────────────────────────────

function ReportTable({ columns, rows, emptyText }) {
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
                onClick={() => handleSort(col.key)}
                className={`py-2 px-2 text-xs font-semibold uppercase tracking-wider text-brand-muted cursor-pointer select-none hover:text-brand-ink transition-colors ${col.align === 'right' ? 'text-right' : 'text-left'}`}
              >
                <span className="inline-flex items-center gap-1">
                  {col.label}
                  {sortKey === col.key && (
                    sortDir === 'asc'
                      ? <ChevronUp className="w-3 h-3" />
                      : <ChevronDown className="w-3 h-3" />
                  )}
                </span>
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
                  {col.format ? col.format(row[col.key]) : (row[col.key] ?? '—')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Billing report tab (shared shape for realization / wip / aging) ────────

function BillingReportTab({ title, icon: Icon, columns, emptyText, loader, onDownload, downloadFilename }) {
  const [rows, setRows] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [downloadError, setDownloadError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    loader()
      .then(data => { if (!cancelled) setRows(data) })
      .catch(e => { if (!cancelled) setError(e.message || 'Failed to load report') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  const handleDownload = async () => {
    setDownloadError(null)
    try {
      const blob = await onDownload()
      triggerBlobDownload(blob, downloadFilename)
    } catch (e) {
      setDownloadError(e.message || 'Failed to download CSV')
    }
  }

  return (
    <div className="bg-brand-surface-2 border border-brand-line p-6 flex flex-col gap-4">
      <div className="flex items-center justify-between border-b border-brand-line pb-3">
        <div className="flex items-center gap-2">
          <Icon className="w-4 h-4 text-brand-accent" strokeWidth={1.5} />
          <h2 className="font-serif font-semibold text-brand-ink">{title}</h2>
        </div>
        <button
          onClick={handleDownload}
          className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-brand-muted hover:text-brand-accent transition-colors border border-brand-line px-3 py-1.5"
        >
          <Download className="w-3.5 h-3.5" />
          Download CSV
        </button>
      </div>

      {downloadError && (
        <p className="text-xs text-brand-rose">{downloadError}</p>
      )}

      {loading && (
        <p className="text-sm text-brand-muted">Loading…</p>
      )}

      {!loading && error && (
        <p className="text-sm text-brand-rose">{error}</p>
      )}

      {!loading && !error && (
        <ReportTable columns={columns} rows={rows} emptyText={emptyText} />
      )}
    </div>
  )
}

// ── Tab definitions ──────────────────────────────────────────────────────────

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

  useEffect(() => {
    getReportsBundle()
      .then(setBundle)
      .catch(e => setError(e.message || 'Failed to load reports'))
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen bg-brand-bg">
        <p className="text-brand-muted font-serif">Loading reports…</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-screen bg-brand-bg">
        <p className="text-brand-rose font-serif">{error}</p>
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
      <div className="border-b border-brand-line bg-brand-surface-2 px-6 flex items-center gap-1">
        {TABS.map(tab => (
          <button
            key={tab.key}
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
        <div className="max-w-5xl mx-auto px-6 py-8 grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">

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
                    {overdue_tasks.tasks.map(task => (
                      <tr key={task.id} className="border-b border-brand-line last:border-0">
                        <td className="py-1.5 text-brand-ink truncate max-w-[120px]" title={task.title}>{task.title}</td>
                        <td className="py-1.5 font-mono text-brand-rose whitespace-nowrap">{task.due_date}</td>
                        <td className="py-1.5 text-brand-muted truncate max-w-[120px]" title={task.matter_name || ''}>{task.matter_name || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

        </div>
      )}

      {activeTab === 'realization' && (
        <div className="max-w-5xl mx-auto px-6 py-8">
          <BillingReportTab
            title="Billing Realization"
            icon={TrendingUp}
            loader={getRealizationReport}
            onDownload={downloadRealizationCsv}
            downloadFilename="realization.csv"
            emptyText="No billable activity"
            columns={[
              { key: 'matter_name', label: 'Matter' },
              { key: 'billable_hours', label: 'Billable Hours', align: 'right', format: hours },
              { key: 'billable_amount', label: 'Billable Amount', align: 'right', format: money },
              { key: 'collected_amount', label: 'Collected', align: 'right', format: money },
              { key: 'realization_pct', label: 'Realization %', align: 'right', format: percent },
            ]}
          />
        </div>
      )}

      {activeTab === 'wip' && (
        <div className="max-w-5xl mx-auto px-6 py-8">
          <BillingReportTab
            title="Work In Progress"
            icon={Clock}
            loader={getWipReport}
            onDownload={downloadWipCsv}
            downloadFilename="wip.csv"
            emptyText="No work in progress"
            columns={[
              { key: 'matter_name', label: 'Matter' },
              { key: 'wip_hours', label: 'WIP Hours', align: 'right', format: hours },
              { key: 'wip_value', label: 'WIP Value', align: 'right', format: money },
            ]}
          />
        </div>
      )}

      {activeTab === 'aging' && (
        <div className="max-w-5xl mx-auto px-6 py-8">
          <BillingReportTab
            title="Accounts Receivable Aging"
            icon={FileWarning}
            loader={getAgingReport}
            onDownload={downloadAgingCsv}
            downloadFilename="aging.csv"
            emptyText="No outstanding receivables"
            columns={[
              { key: 'matter_name', label: 'Matter' },
              { key: 'days_0_30', label: '0–30', align: 'right', format: money },
              { key: 'days_31_60', label: '31–60', align: 'right', format: money },
              { key: 'days_61_90', label: '61–90', align: 'right', format: money },
              { key: 'days_90_plus', label: '90+', align: 'right', format: money },
            ]}
          />
        </div>
      )}
    </div>
  )
}
