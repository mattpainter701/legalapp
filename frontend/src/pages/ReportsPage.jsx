import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getReportsBundle } from '../api'
import { BarChart2, Users, AlertTriangle, Scale, ArrowLeft } from 'lucide-react'

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

export default function ReportsPage() {
  const navigate = useNavigate()
  const [bundle, setBundle] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

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
    <div className="min-h-screen bg-brand-bg text-brand-ink">
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
    </div>
  )
}
