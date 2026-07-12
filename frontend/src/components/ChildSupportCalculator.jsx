import React, { useState, useEffect, useCallback, useRef } from 'react'
import { Calculator, AlertTriangle, Save, RefreshCw, Info, GitCompare, X } from 'lucide-react'
import { getCsJurisdictions, calculateChildSupport, saveChildSupportCalc } from '../api'

function money(v) {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD' })
}

const CUSTODY_TYPES = [
  { value: 'primary', label: 'Primary (one parent)' },
  { value: 'equal', label: 'Equal / shared' },
  { value: 'split', label: 'Split custody' },
]

function emptyParent(role, name) {
  return {
    role,
    name: name || '',
    gross_monthly_income: '',
    federal_income_tax: '',
    state_income_tax: '',
    fica_tax: '',
    required_retirement: '',
    union_dues: '',
    health_insurance_children: '',
    existing_support_paid: '',
    other_children_in_home: 0,
    annual_overnights: '',
  }
}

// Strip empty strings; numbers stay numbers, blanks become null/0 for the API.
function cleanParent(p) {
  const num = (v) => (v === '' || v === null || v === undefined ? null : Number(v))
  const num0 = (v) => (v === '' || v === null || v === undefined ? 0 : Number(v))
  return {
    role: p.role,
    name: p.name || null,
    gross_monthly_income: num0(p.gross_monthly_income),
    federal_income_tax: num(p.federal_income_tax),
    state_income_tax: num(p.state_income_tax),
    fica_tax: num(p.fica_tax),
    required_retirement: num0(p.required_retirement),
    union_dues: num0(p.union_dues),
    health_insurance_children: num0(p.health_insurance_children),
    existing_support_paid: num0(p.existing_support_paid),
    other_children_in_home: num0(p.other_children_in_home),
    annual_overnights: num0(p.annual_overnights),
  }
}

function Field({ label, value, onChange, type = 'number', placeholder, hint }) {
  const fieldId = React.useId()
  return (
    <div>
      <label htmlFor={fieldId} className="block text-[10px] font-bold text-brand-ink uppercase tracking-widest mb-1">{label}</label>
      <input id={fieldId}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full border border-brand-line rounded-lg px-3 py-2 text-[13px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface"
      />
      {hint && <p className="text-[10px] text-brand-muted font-sans mt-0.5">{hint}</p>}
    </div>
  )
}

function ParentColumn({ parent, onField }) {
  return (
    <div className="space-y-3">
      <Field label="Name" type="text" value={parent.name} onChange={(v) => onField('name', v)} placeholder="Party name" />
      <Field label="Gross Monthly Income" value={parent.gross_monthly_income} onChange={(v) => onField('gross_monthly_income', v)} placeholder="0.00" />
      <div className="grid grid-cols-3 gap-2">
        <Field label="Fed Tax" value={parent.federal_income_tax} onChange={(v) => onField('federal_income_tax', v)} placeholder="auto" hint="blank = est." />
        <Field label="State Tax" value={parent.state_income_tax} onChange={(v) => onField('state_income_tax', v)} placeholder="auto" hint="blank = est." />
        <Field label="FICA" value={parent.fica_tax} onChange={(v) => onField('fica_tax', v)} placeholder="auto" hint="blank = est." />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Req. Retirement" value={parent.required_retirement} onChange={(v) => onField('required_retirement', v)} placeholder="0" />
        <Field label="Union Dues" value={parent.union_dues} onChange={(v) => onField('union_dues', v)} placeholder="0" />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Child Health Ins." value={parent.health_insurance_children} onChange={(v) => onField('health_insurance_children', v)} placeholder="0" />
        <Field label="Other Support Paid" value={parent.existing_support_paid} onChange={(v) => onField('existing_support_paid', v)} placeholder="0" />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Other Kids in Home" value={parent.other_children_in_home} onChange={(v) => onField('other_children_in_home', v)} placeholder="0" />
        <Field label="Annual Overnights" value={parent.annual_overnights} onChange={(v) => onField('annual_overnights', v)} placeholder="0" />
      </div>
    </div>
  )
}

export default function ChildSupportCalculator({ caseId, jurisdiction = 'ND', onSaved }) {
  const [jurisdictions, setJurisdictions] = useState([])
  const [state, setState] = useState(jurisdiction || 'ND')
  const [numChildren, setNumChildren] = useState(1)
  const [custodyType, setCustodyType] = useState('primary')
  const [childrenWithA, setChildrenWithA] = useState(0)
  const [allowEstimates, setAllowEstimates] = useState(true)
  const [parents, setParents] = useState([
    emptyParent('petitioner', ''),
    emptyParent('respondent', ''),
  ])
  const [deviationOn, setDeviationOn] = useState(false)
  const [deviationAmount, setDeviationAmount] = useState('')
  const [deviationReason, setDeviationReason] = useState('')

  const [worksheet, setWorksheet] = useState(null)
  const [calcError, setCalcError] = useState(null)
  const [calculating, setCalculating] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveLabel, setSaveLabel] = useState('')
  const [scenarioA, setScenarioA] = useState(null) // pinned snapshot for A/B compare
  const debounceRef = useRef(null)

  useEffect(() => {
    getCsJurisdictions().then(setJurisdictions).catch(() => {})
  }, [])

  const buildRequest = useCallback(() => ({
    jurisdiction: state,
    num_children: Number(numChildren) || 0,
    parents: parents.map(cleanParent),
    custody_type: custodyType,
    children_with_parent_a: Number(childrenWithA) || 0,
    allow_estimates: allowEstimates,
    deviation_amount: deviationOn && deviationAmount !== '' ? Number(deviationAmount) : null,
    deviation_reason: deviationOn ? deviationReason || null : null,
  }), [state, numChildren, parents, custodyType, childrenWithA, allowEstimates, deviationOn, deviationAmount, deviationReason])

  const runCalc = useCallback(async () => {
    setCalculating(true)
    setCalcError(null)
    try {
      const ws = await calculateChildSupport(buildRequest())
      setWorksheet(ws)
    } catch (err) {
      setCalcError(err?.response?.data?.detail || 'Calculation failed.')
    } finally {
      setCalculating(false)
    }
  }, [buildRequest])

  // Debounced auto-recalc on input changes.
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(runCalc, 450)
    return () => clearTimeout(debounceRef.current)
  }, [runCalc])

  const setParentField = (idx, field, value) => {
    setParents((prev) => prev.map((p, i) => (i === idx ? { ...p, [field]: value } : p)))
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      const saved = await saveChildSupportCalc(caseId, { label: saveLabel || null, is_final: false, request: buildRequest() })
      setSaveLabel('')
      if (onSaved) onSaved(saved)
    } catch (err) {
      setCalcError(err?.response?.data?.detail || 'Save failed.')
    } finally {
      setSaving(false)
    }
  }

  const selectedJur = jurisdictions.find((j) => j.state_code === state)

  const pinScenarioA = () => {
    if (!worksheet) return
    setScenarioA({
      final: Number(worksheet.final_amount),
      obligor: worksheet.obligor_role,
      children: worksheet.num_children,
      state: worksheet.jurisdiction,
    })
  }
  const scenarioB = worksheet ? Number(worksheet.final_amount) : null
  const delta = scenarioA && scenarioB !== null ? scenarioB - scenarioA.final : null

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      {/* ── Inputs ── */}
      <div className="space-y-5">
        <div className="bg-brand-surface border border-brand-line rounded-2xl p-5 shadow-sm">
          <div className="flex items-center gap-2 mb-4">
            <Calculator size={18} className="text-brand-accent" />
            <h3 className="font-serif font-bold text-lg text-brand-ink">Calculation Inputs</h3>
          </div>
          <div className="grid grid-cols-3 gap-3 mb-4">
            <div>
              <label htmlFor="childsupportcalculator-state" className="block text-[10px] font-bold text-brand-ink uppercase tracking-widest mb-1">State</label>
              <select id="childsupportcalculator-state" value={state} onChange={(e) => setState(e.target.value)}
                className="w-full border border-brand-line rounded-lg px-3 py-2 text-[13px] font-sans text-brand-ink bg-brand-surface focus:outline-none focus:border-brand-accent">
                {jurisdictions.length === 0 && <option value={state}>{state}</option>}
                {jurisdictions.map((j) => <option key={j.state_code} value={j.state_code}>{j.state_code} — {j.state_name}</option>)}
              </select>
            </div>
            <div>
              <label htmlFor="childsupportcalculator-children" className="block text-[10px] font-bold text-brand-ink uppercase tracking-widest mb-1"># Children</label>
              <input id="childsupportcalculator-children" type="number" min={0} max={12} value={numChildren} onChange={(e) => setNumChildren(e.target.value)}
                className="w-full border border-brand-line rounded-lg px-3 py-2 text-[13px] font-sans text-brand-ink bg-brand-surface focus:outline-none focus:border-brand-accent" />
            </div>
            <div>
              <label htmlFor="childsupportcalculator-custody" className="block text-[10px] font-bold text-brand-ink uppercase tracking-widest mb-1">Custody</label>
              <select id="childsupportcalculator-custody" value={custodyType} onChange={(e) => setCustodyType(e.target.value)}
                className="w-full border border-brand-line rounded-lg px-3 py-2 text-[13px] font-sans text-brand-ink bg-brand-surface focus:outline-none focus:border-brand-accent">
                {CUSTODY_TYPES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
              </select>
            </div>
          </div>
          {custodyType === 'split' && (
            <div className="mb-4">
              <Field label="Children primarily with Party A (Petitioner)" value={childrenWithA} onChange={setChildrenWithA} placeholder="0" />
            </div>
          )}
          {selectedJur && (
            <div className="text-[11px] text-brand-muted font-sans flex items-center gap-1.5">
              <Info size={12} /> {selectedJur.model_type.replace(/_/g, ' ')} model · schedule {selectedJur.schedule_version}
              {!selectedJur.verified && <span className="text-brand-amber font-semibold">· provisional data</span>}
            </div>
          )}
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="bg-brand-surface border border-brand-line rounded-2xl p-4 shadow-sm">
            <h4 className="text-[11px] font-bold text-brand-ink uppercase tracking-widest mb-3 pb-2 border-b border-brand-line">Party A — Petitioner</h4>
            <ParentColumn parent={parents[0]} onField={(f, v) => setParentField(0, f, v)} />
          </div>
          <div className="bg-brand-surface border border-brand-line rounded-2xl p-4 shadow-sm">
            <h4 className="text-[11px] font-bold text-brand-ink uppercase tracking-widest mb-3 pb-2 border-b border-brand-line">Party B — Respondent</h4>
            <ParentColumn parent={parents[1]} onField={(f, v) => setParentField(1, f, v)} />
          </div>
        </div>

        <div className="bg-brand-surface border border-brand-line rounded-2xl p-4 shadow-sm space-y-3">
          <label className="flex items-center gap-2 text-[12px] font-sans font-medium text-brand-ink cursor-pointer">
            <input type="checkbox" checked={allowEstimates} onChange={(e) => setAllowEstimates(e.target.checked)} />
            Estimate missing tax/FICA deductions
          </label>
          <label className="flex items-center gap-2 text-[12px] font-sans font-medium text-brand-ink cursor-pointer">
            <input type="checkbox" checked={deviationOn} onChange={(e) => setDeviationOn(e.target.checked)} />
            Deviate from guideline amount
          </label>
          {deviationOn && (
            <div className="space-y-2 pl-6">
              <Field label="Deviation Amount (monthly)" value={deviationAmount} onChange={setDeviationAmount} placeholder="0.00" />
              <div>
                <label htmlFor="childsupportcalculator-reason-required" className="block text-[10px] font-bold text-brand-ink uppercase tracking-widest mb-1">Reason (required)</label>
                <textarea id="childsupportcalculator-reason-required" value={deviationReason} onChange={(e) => setDeviationReason(e.target.value)} rows={2}
                  placeholder="Written basis for departing from the guideline"
                  className="w-full border border-brand-line rounded-lg px-3 py-2 text-[13px] font-sans text-brand-ink bg-brand-surface focus:outline-none focus:border-brand-accent" />
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── Worksheet ── */}
      <div className="space-y-4">
        <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm overflow-hidden">
          <div className="flex items-center justify-between px-5 py-4 border-b border-brand-line bg-brand-bg-soft/50">
            <h3 className="font-serif font-bold text-lg text-brand-ink">Worksheet</h3>
            <div className="flex items-center gap-4">
              <button onClick={pinScenarioA} disabled={!worksheet}
                className="flex items-center gap-1.5 text-[12px] font-sans font-medium text-brand-ink-2 hover:text-brand-ink transition-colors disabled:text-brand-muted">
                <GitCompare size={13} /> Pin as Scenario A
              </button>
              <button onClick={runCalc} disabled={calculating}
                className="flex items-center gap-1.5 text-[12px] font-sans font-medium text-brand-ink-2 hover:text-brand-ink transition-colors">
                <RefreshCw size={13} className={calculating ? 'animate-spin' : ''} /> Recalculate
              </button>
            </div>
          </div>

          {scenarioA && (
            <div className="flex items-stretch border-b border-brand-line">
              <div className="flex-1 px-5 py-3 border-r border-brand-line">
                <p className="text-[10px] uppercase tracking-widest text-brand-muted font-sans mb-0.5">Scenario A (pinned)</p>
                <p className="font-serif text-xl font-bold text-brand-ink-2">{money(scenarioA.final)}</p>
              </div>
              <div className="flex-1 px-5 py-3 border-r border-brand-line">
                <p className="text-[10px] uppercase tracking-widest text-brand-muted font-sans mb-0.5">Scenario B (current)</p>
                <p className="font-serif text-xl font-bold text-brand-ink">{money(scenarioB)}</p>
              </div>
              <div className="px-5 py-3 flex flex-col justify-center min-w-[110px]">
                <p className="text-[10px] uppercase tracking-widest text-brand-muted font-sans mb-0.5">Difference</p>
                <p className={`font-serif text-xl font-bold ${delta > 0 ? 'text-brand-rose' : delta < 0 ? 'text-brand-green' : 'text-brand-ink'}`}>
                  {delta > 0 ? '+' : ''}{money(delta)}
                </p>
              </div>
              <button onClick={() => setScenarioA(null)} title="Clear comparison"
                className="px-2 text-brand-muted hover:text-brand-ink"><X size={14} /></button>
            </div>
          )}

          {calcError && (
            <div className="px-5 py-3 text-brand-rose text-sm font-sans bg-brand-rose/10 border-b border-brand-rose/20">{calcError}</div>
          )}

          {worksheet && (
            <>
              <div className="px-5 py-5 bg-brand-ink text-white">
                <p className="text-[11px] uppercase tracking-widest font-sans text-white/60 mb-1">Presumptive Monthly Support</p>
                <p className="font-serif text-4xl font-bold tracking-tight">{money(worksheet.final_amount)}</p>
                {worksheet.deviation_amount !== null && worksheet.deviation_amount !== undefined && (
                  <p className="text-[12px] font-sans text-white/70 mt-1">
                    Guideline {money(worksheet.presumptive_amount)} · deviated to {money(worksheet.final_amount)}
                  </p>
                )}
                <p className="text-[12px] font-sans text-white/60 mt-1">
                  Obligor: <span className="text-white font-medium capitalize">{worksheet.obligor_role || '—'}</span>
                </p>
              </div>

              <div className="divide-y divide-brand-line max-h-[420px] overflow-y-auto">
                {worksheet.lines.map((ln, i) => (
                  <div key={i} className="flex items-start justify-between px-5 py-2.5 hover:bg-brand-bg-soft">
                    <div className="min-w-0 pr-3">
                      <p className="text-[13px] font-sans text-brand-ink font-medium">
                        {ln.label}
                        {ln.estimated && <span className="ml-1.5 text-[10px] text-brand-amber uppercase tracking-wide font-bold">est</span>}
                      </p>
                      {ln.detail && <p className="text-[11px] text-brand-muted font-sans">{ln.detail}</p>}
                    </div>
                    <span className={`text-[13px] font-sans font-semibold whitespace-nowrap ${ln.amount && Number(ln.amount) < 0 ? 'text-brand-rose' : 'text-brand-ink'}`}>
                      {ln.amount === null || ln.amount === undefined ? '' : money(ln.amount)}
                    </span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>

        {worksheet?.warnings?.length > 0 && (
          <div className="bg-brand-amber/10 border border-brand-amber/30 rounded-xl p-4">
            <div className="flex items-center gap-2 mb-2">
              <AlertTriangle size={14} className="text-brand-amber" />
              <span className="text-[12px] font-bold text-brand-ink uppercase tracking-widest">Warnings</span>
            </div>
            <ul className="space-y-1">
              {worksheet.warnings.map((w, i) => <li key={i} className="text-[12px] text-brand-ink-2 font-sans">• {w}</li>)}
            </ul>
          </div>
        )}

        <div className="bg-brand-bg-soft border border-brand-line rounded-xl p-3 text-[11px] text-brand-muted font-sans flex items-start gap-2">
          <Info size={13} className="mt-0.5 shrink-0" />
          <span>
            This worksheet is a drafting aid, not legal advice. Verify amounts against the official
            {selectedJur ? ` ${selectedJur.state_name}` : ''} child support calculator before filing.
            {worksheet?.citations?.length > 0 && ` Authority: ${worksheet.citations[worksheet.citations.length - 1]}.`}
          </span>
        </div>

        <div className="bg-brand-surface border border-brand-line rounded-2xl p-4 shadow-sm flex items-end gap-3">
          <div className="flex-1">
            <label htmlFor="childsupportcalculator-save-this-run-as" className="block text-[10px] font-bold text-brand-ink uppercase tracking-widest mb-1">Save this run as…</label>
            <input id="childsupportcalculator-save-this-run-as" type="text" value={saveLabel} onChange={(e) => setSaveLabel(e.target.value)}
              placeholder="e.g., Initial guideline calc"
              className="w-full border border-brand-line rounded-lg px-3 py-2 text-[13px] font-sans text-brand-ink bg-brand-surface focus:outline-none focus:border-brand-accent" />
          </div>
          <button onClick={handleSave} disabled={saving || !worksheet}
            className="flex items-center gap-1.5 px-4 py-2 bg-brand-ink text-white text-[13px] font-sans font-medium rounded-xl hover:bg-brand-ink-2 disabled:bg-brand-line disabled:text-brand-muted transition-all shadow-sm">
            <Save size={14} /> {saving ? 'Saving…' : 'Save to Case'}
          </button>
        </div>
      </div>
    </div>
  )
}
