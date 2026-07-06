/**
 * NseBacktest — NSE / BSE India equity backtesting module.
 *
 * Features:
 *  - Enable / Disable toggle (writes to config.yaml via API)
 *  - Exchange selector: NSE | BSE
 *  - Symbol input (comma-separated)
 *  - Timeframe selector with history-limit notes
 *  - Date range inputs
 *  - Capital + risk config
 *  - Run Backtest button with live status
 *  - Per-symbol results table
 *  - Total P&L summary
 */
import { useState, useEffect, useRef } from 'react'
import {
  ToggleLeft, ToggleRight, PlayCircle, RefreshCw,
  CheckCircle, AlertCircle, TrendingUp, TrendingDown,
  IndianRupee, BarChart2, Target,
} from 'lucide-react'
import clsx from 'clsx'

const API = (path) => `/api/nse${path}`

const TF_OPTIONS = [
  { value: '1d',  label: '1 Day',    note: 'Full history' },
  { value: '1wk', label: '1 Week',   note: 'Full history' },
  { value: '1mo', label: '1 Month',  note: 'Full history' },
  { value: '1h',  label: '1 Hour',   note: 'Max 730 days' },
  { value: '30m', label: '30 Min',   note: 'Max 60 days'  },
  { value: '15m', label: '15 Min',   note: 'Max 60 days'  },
  { value: '5m',  label: '5 Min',    note: 'Max 60 days'  },
  { value: '1m',  label: '1 Min',    note: 'Max 7 days'   },
]

const POPULAR_SYMBOLS = {
  'Large Cap':   'RELIANCE,TCS,INFY,HDFCBANK,ICICIBANK,SBIN,WIPRO,AXISBANK',
  'Banking':     'HDFCBANK,ICICIBANK,SBIN,AXISBANK,KOTAK,INDUSINDBK,BANDHANBNK',
  'IT':          'TCS,INFY,WIPRO,HCLTECH,TECHM,MPHASIS,LTIM',
  'Auto':        'TATAMOTORS,MARUTI,M&M,BAJAJ-AUTO,EICHERMOT,HEROMOTOCO',
  'Indices':     'NIFTY50,SENSEX,BANKNIFTY',
}

function fINR(v) {
  if (v == null) return '—'
  const abs = Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: 2 })
  return (v >= 0 ? '+₹' : '-₹') + abs
}

function StatCard({ label, value, color, icon: Icon }) {
  return (
    <div className="card flex flex-col gap-1">
      <div className="flex items-center justify-between">
        <span className="metric-label">{label}</span>
        {Icon && <Icon size={13} className={clsx('opacity-50', color)} />}
      </div>
      <span className={clsx('text-xl font-bold font-mono tabular-nums', color)}>{value}</span>
    </div>
  )
}

export default function NseBacktest() {
  const [cfg,       setCfg]    = useState(null)
  const [status,    setStatus] = useState({ running: false, status: 'idle' })
  const [results,   setResults]= useState(null)
  const [loading,   setLoading]= useState(true)
  const [saveMsg,   setSaveMsg]= useState('')
  const pollRef = useRef(null)

  // ── Load config on mount ───────────────────────────────────────────────────
  useEffect(() => {
    Promise.all([
      fetch(API('/config')).then(r => r.json()),
      fetch(API('/results')).then(r => r.json()).catch(() => null),
      fetch(API('/run')).then(r => r.json()).catch(() => null),
    ]).then(([cfgRes, resRes, statusRes]) => {
      if (cfgRes.ok) setCfg(cfgRes.config)
      if (resRes && !resRes.error) setResults(resRes)
      if (statusRes) setStatus(statusRes)
      setLoading(false)
    })
  }, [])

  // ── Toggle enabled ─────────────────────────────────────────────────────────
  const toggleEnabled = async () => {
    const next = !cfg?.enabled
    const r = await fetch(API('/toggle'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: next }),
    })
    const d = await r.json()
    if (d.ok) setCfg(prev => ({ ...prev, enabled: next }))
  }

  // ── Save config ────────────────────────────────────────────────────────────
  const saveConfig = async () => {
    const r = await fetch(API('/save'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg),
    })
    const d = await r.json()
    setSaveMsg(d.ok ? 'Saved' : d.error)
    setTimeout(() => setSaveMsg(''), 3000)
  }

  // ── Run backtest ───────────────────────────────────────────────────────────
  const runBacktest = async () => {
    if (status.running) return
    await fetch(API('/save'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg),
    })
    await fetch(API('/run'), { method: 'POST' })
    setStatus({ running: true, status: 'running' })

    pollRef.current = setInterval(async () => {
      const r  = await fetch(API('/run'))
      const d  = await r.json()
      setStatus(d)
      if (!d.running) {
        clearInterval(pollRef.current)
        if (d.status === 'done') {
          const res = await fetch(API('/results')).then(r => r.json())
          if (!res.error) setResults(res)
        }
      }
    }, 2000)
  }

  useEffect(() => () => clearInterval(pollRef.current), [])

  const inp = 'bg-bg-tertiary border border-border rounded-sm px-2 py-1.5 text-[11px] font-mono text-txt-primary outline-none focus:border-accent/50 w-full'
  const sel = `${inp} cursor-pointer`

  if (loading) return (
    <div className="flex items-center justify-center h-64 text-txt-muted text-sm">Loading…</div>
  )

  const enabled = cfg?.enabled ?? false
  const symResults = results?.symbol_results ?? []
  const totalPnl   = results?.total_pnl ?? null

  return (
    <div className="flex flex-col gap-4 p-4 max-w-5xl">

      {/* ── Header ───────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-sm font-bold text-txt-primary tracking-wide">NSE / BSE Backtester</h1>
          <p className="text-[10px] font-mono text-txt-muted mt-0.5">
            India equity backtest using Yahoo Finance data — no MT5 required
          </p>
        </div>

        {/* Enable / Disable toggle */}
        <button
          onClick={toggleEnabled}
          className={clsx(
            'flex items-center gap-2 px-3 py-1.5 rounded-sm border text-[11px] font-bold font-mono transition-colors',
            enabled
              ? 'border-green/30 bg-green/10 text-green hover:bg-green/15'
              : 'border-border bg-bg-elevated text-txt-muted hover:text-txt-secondary'
          )}
        >
          {enabled ? <ToggleRight size={16} /> : <ToggleLeft size={16} />}
          {enabled ? 'MODULE ENABLED' : 'MODULE DISABLED'}
        </button>
      </div>

      {!enabled && (
        <div className="card border-yellow/20 bg-yellow/5 text-yellow text-[11px] font-mono py-3 text-center">
          NSE/BSE module is disabled. Toggle above to enable it.
        </div>
      )}

      {/* ── Config Form ──────────────────────────────────────────────── */}
      {cfg && (
        <div className={clsx('card flex flex-col gap-4', !enabled && 'opacity-50 pointer-events-none')}>
          <span className="section-label">Configuration</span>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {/* Exchange */}
            <div className="flex flex-col gap-1">
              <label className="section-label">Exchange</label>
              <select className={sel} value={cfg.exchange ?? 'NSE'}
                onChange={e => setCfg(p => ({ ...p, exchange: e.target.value }))}>
                <option value="NSE">NSE</option>
                <option value="BSE">BSE</option>
              </select>
            </div>

            {/* Timeframe */}
            <div className="flex flex-col gap-1">
              <label className="section-label">Timeframe</label>
              <select className={sel} value={cfg.timeframe ?? '1d'}
                onChange={e => setCfg(p => ({ ...p, timeframe: e.target.value }))}>
                {TF_OPTIONS.map(t => (
                  <option key={t.value} value={t.value}>{t.label} — {t.note}</option>
                ))}
              </select>
            </div>

            {/* Capital */}
            <div className="flex flex-col gap-1">
              <label className="section-label">Capital per symbol (₹)</label>
              <input className={inp} type="number" value={cfg.initial_capital ?? 100000}
                onChange={e => setCfg(p => ({ ...p, initial_capital: Number(e.target.value) }))} />
            </div>

            {/* Risk */}
            <div className="flex flex-col gap-1">
              <label className="section-label">Risk per trade (%)</label>
              <input className={inp} type="number" step="0.5" value={cfg.risk_per_trade ?? 1}
                onChange={e => setCfg(p => ({ ...p, risk_per_trade: Number(e.target.value) }))} />
            </div>

            {/* Start date */}
            <div className="flex flex-col gap-1">
              <label className="section-label">Start Date</label>
              <input className={inp} type="date" value={cfg.start_date ?? '2020-01-01'}
                onChange={e => setCfg(p => ({ ...p, start_date: e.target.value }))} />
            </div>

            {/* End date */}
            <div className="flex flex-col gap-1">
              <label className="section-label">End Date</label>
              <input className={inp} type="date" value={cfg.end_date ?? '2026-06-01'}
                onChange={e => setCfg(p => ({ ...p, end_date: e.target.value }))} />
            </div>

            {/* Strategy */}
            <div className="flex flex-col gap-1 col-span-2">
              <label className="section-label">Strategy (blank = use active_preset)</label>
              <input className={inp} placeholder="e.g. SupertrendCounterFlip_X1"
                value={cfg.strategy ?? ''}
                onChange={e => setCfg(p => ({ ...p, strategy: e.target.value }))} />
            </div>
          </div>

          {/* Symbols */}
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <label className="section-label">Symbols (comma-separated)</label>
              <div className="flex gap-1 flex-wrap">
                {Object.entries(POPULAR_SYMBOLS).map(([grp, syms]) => (
                  <button key={grp} onClick={() => setCfg(p => ({ ...p, symbols: syms }))}
                    className="text-[9px] font-mono px-1.5 py-0.5 border border-border rounded-sm text-txt-muted hover:text-accent hover:border-accent/30 transition-colors">
                    {grp}
                  </button>
                ))}
              </div>
            </div>
            <textarea className={`${inp} h-16 resize-none`}
              value={cfg.symbols ?? ''}
              onChange={e => setCfg(p => ({ ...p, symbols: e.target.value }))}
              placeholder="RELIANCE,TCS,INFY,HDFCBANK..." />
            <p className="text-[9px] font-mono text-txt-muted">
              NSE symbols: RELIANCE, TCS, INFY… | Indices: NIFTY50, SENSEX, BANKNIFTY
            </p>
          </div>

          {/* Action buttons */}
          <div className="flex items-center gap-3 flex-wrap">
            <button onClick={saveConfig}
              className="btn border-accent/30 bg-accent/10 text-accent hover:bg-accent/20">
              Save Config
            </button>
            {saveMsg && (
              <span className={clsx('text-[10px] font-mono', saveMsg === 'Saved' ? 'text-green' : 'text-red')}>
                {saveMsg}
              </span>
            )}

            <button
              onClick={runBacktest}
              disabled={status.running}
              className={clsx(
                'flex items-center gap-1.5 px-4 py-1.5 rounded-sm text-[12px] font-bold font-mono border transition-colors ml-auto',
                status.running
                  ? 'border-yellow/30 bg-yellow/10 text-yellow cursor-wait'
                  : status.status === 'done'
                  ? 'border-green/30 bg-green/10 text-green cursor-pointer hover:bg-green/20'
                  : status.status === 'error'
                  ? 'border-red/30 bg-red/10 text-red cursor-pointer'
                  : 'border-accent/30 bg-accent/10 text-accent cursor-pointer hover:bg-accent/20'
              )}
            >
              {status.running
                ? <><RefreshCw size={13} className="animate-spin" /> Running…</>
                : status.status === 'done'
                ? <><CheckCircle size={13} /> Re-run Backtest</>
                : status.status === 'error'
                ? <><AlertCircle size={13} /> Retry</>
                : <><PlayCircle size={13} /> Run Backtest</>}
            </button>
          </div>

          {status.status === 'error' && status.error && (
            <div className="text-[10px] font-mono text-red border border-red/20 bg-red/5 rounded-sm p-2">
              {status.error}
            </div>
          )}
        </div>
      )}

      {/* ── Results ──────────────────────────────────────────────────── */}
      {results && results.ok && (
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <span className="section-label">Results</span>
            <span className="text-[9px] font-mono text-txt-muted">{results.timestamp}</span>
          </div>

          {/* Summary cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            <StatCard
              label="Total P&L"
              value={fINR(results.total_pnl)}
              color={results.total_pnl >= 0 ? 'text-green' : 'text-red'}
              icon={results.total_pnl >= 0 ? TrendingUp : TrendingDown}
            />
            <StatCard label="Symbols" value={results.symbols?.length ?? 0} color="text-accent" icon={BarChart2} />
            <StatCard label="Exchange" value={results.exchange} color="text-txt-primary" />
            <StatCard label="Timeframe" value={results.timeframe} color="text-purple" icon={Target} />
          </div>

          {/* Per-symbol table */}
          <div className="card p-0 overflow-hidden">
            <div className="px-4 py-3 border-b border-border">
              <span className="section-label">Per-Symbol Performance</span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr>
                    <th className="tbl-th">Symbol</th>
                    <th className="tbl-th">Exchange</th>
                    <th className="tbl-th text-right">Trades</th>
                    <th className="tbl-th text-right">Wins</th>
                    <th className="tbl-th text-right">Losses</th>
                    <th className="tbl-th text-right">Win Rate</th>
                    <th className="tbl-th text-right">P&L (₹)</th>
                  </tr>
                </thead>
                <tbody>
                  {symResults.length === 0 && (
                    <tr><td colSpan={7} className="tbl-td text-center text-txt-muted py-8">No results</td></tr>
                  )}
                  {[...symResults]
                    .sort((a, b) => b.pnl - a.pnl)
                    .map((r, i) => (
                    <tr key={r.symbol} className="hover:bg-bg-elevated transition-colors">
                      <td className="tbl-td font-bold text-accent">{r.symbol}</td>
                      <td className="tbl-td text-txt-muted">{r.exchange}</td>
                      <td className="tbl-td text-right">{r.trades}</td>
                      <td className="tbl-td text-right text-green">{r.wins}</td>
                      <td className="tbl-td text-right text-red">{r.losses}</td>
                      <td className={clsx('tbl-td text-right font-mono',
                        r.win_rate >= 50 ? 'text-green' : 'text-red')}>
                        {r.win_rate}%
                      </td>
                      <td className={clsx('tbl-td text-right font-mono font-bold',
                        r.pnl >= 0 ? 'text-green' : 'text-red')}>
                        {fINR(r.pnl)}
                      </td>
                    </tr>
                  ))}
                  {symResults.length > 0 && (
                    <tr className="border-t border-border bg-bg-elevated">
                      <td className="tbl-td font-bold text-txt-primary" colSpan={2}>TOTAL</td>
                      <td className="tbl-td text-right font-bold">{symResults.reduce((s,r)=>s+r.trades,0)}</td>
                      <td className="tbl-td text-right text-green font-bold">{symResults.reduce((s,r)=>s+r.wins,0)}</td>
                      <td className="tbl-td text-right text-red font-bold">{symResults.reduce((s,r)=>s+r.losses,0)}</td>
                      <td className="tbl-td text-right" />
                      <td className={clsx('tbl-td text-right font-mono font-bold text-sm',
                        results.total_pnl >= 0 ? 'text-green' : 'text-red')}>
                        {fINR(results.total_pnl)}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
