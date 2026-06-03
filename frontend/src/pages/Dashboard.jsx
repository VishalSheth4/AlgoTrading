import { useState, useEffect, useRef } from 'react'
import { PlayCircle, RefreshCw, CheckCircle, AlertCircle, Zap } from 'lucide-react'
import clsx from 'clsx'
import CandleChart  from '../components/CandleChart'
import MetricsPanel from '../components/MetricsPanel'
import EquityCurve  from '../components/EquityCurve'
import TradeLog     from '../components/TradeLog'
import useStore     from '../store/useStore'
import { useWebSocket } from '../hooks/useWebSocket'

// ── Auto-rerun status banner ──────────────────────────────────────────────────
function AutoRerunBanner() {
  const [status, setStatus] = useState(null)   // null | 'running' | 'done' | 'error'
  const [msg,    setMsg]    = useState('')

  useEffect(() => {
    const poll = async () => {
      try {
        const r = await fetch('/api/run-backtest')
        const d = await r.json()
        if (d.running) {
          setStatus('running')
          setMsg('Recalculating — file change detected…')
        } else if (d.status === 'done' && status === 'running') {
          setStatus('done')
          setMsg(`Done at ${d.finished}`)
          setTimeout(() => setStatus(null), 4000)
        } else if (d.status === 'error') {
          setStatus('error')
          setMsg(d.error || 'Backtest error')
        }
      } catch {}
    }
    const id = setInterval(poll, 1500)
    return () => clearInterval(id)
  }, [status])

  if (!status) return null

  return (
    <div className={clsx(
      'flex items-center gap-2 px-3 py-2 rounded-sm border text-[11px] font-mono',
      status === 'running' ? 'border-yellow/30 bg-yellow/8 text-yellow' :
      status === 'done'    ? 'border-green/30  bg-green/8  text-green'  :
                             'border-red/30    bg-red/8    text-red'
    )}>
      {status === 'running' ? <RefreshCw size={12} className="animate-spin shrink-0" /> :
       status === 'done'    ? <CheckCircle size={12} className="shrink-0" /> :
                              <AlertCircle  size={12} className="shrink-0" />}
      <span className="font-bold mr-1">
        {status === 'running' ? 'AUTO-RECALC' : status === 'done' ? 'UPDATED' : 'ERROR'}
      </span>
      {msg}
    </div>
  )
}

// ── Run-Backtest button ────────────────────────────────────────────────────────
function RunBacktestBtn({ onDone }) {
  const [state, setState] = useState('idle')   // idle | running | done | error
  const [msg,   setMsg]   = useState('')
  const pollRef = useRef(null)

  const run = async () => {
    if (state === 'running') return
    setState('running')
    setMsg('Connecting to MT5 and fetching data…')
    try {
      await fetch('/api/run-backtest', { method: 'POST' })
      // Poll status every 2 s
      pollRef.current = setInterval(async () => {
        const r = await fetch('/api/run-backtest')
        const d = await r.json()
        if (d.status === 'done') {
          clearInterval(pollRef.current)
          setState('done')
          setMsg(`Finished at ${d.finished}`)
          onDone?.()
        } else if (d.status === 'error') {
          clearInterval(pollRef.current)
          setState('error')
          setMsg(d.error || 'Unknown error')
        } else {
          setMsg('Running backtest…')
        }
      }, 2000)
    } catch (e) {
      setState('error')
      setMsg('Could not reach backend')
    }
  }

  useEffect(() => () => clearInterval(pollRef.current), [])

  return (
    <div className="flex items-center gap-2">
      <button
        onClick={run}
        disabled={state === 'running'}
        className={clsx(
          'flex items-center gap-1.5 px-3 py-1.5 rounded-sm text-[11px] font-bold font-mono border transition-colors',
          state === 'running' ? 'border-yellow/30 bg-yellow/10 text-yellow cursor-wait' :
          state === 'done'    ? 'border-green/30  bg-green/10  text-green  cursor-pointer' :
          state === 'error'   ? 'border-red/30    bg-red/10    text-red    cursor-pointer' :
                                'border-accent/30 bg-accent/10 text-accent cursor-pointer hover:bg-accent/20'
        )}
      >
        {state === 'running' ? <RefreshCw  size={12} className="animate-spin" /> :
         state === 'done'    ? <CheckCircle size={12} /> :
         state === 'error'   ? <AlertCircle size={12} /> :
                               <PlayCircle  size={12} />}
        {state === 'running' ? 'Running…' :
         state === 'done'    ? 'Done — Reload' :
         state === 'error'   ? 'Retry Backtest' :
                               'Run Backtest'}
      </button>
      {msg && (
        <span className={clsx(
          'text-[10px] font-mono',
          state === 'error' ? 'text-red' : state === 'done' ? 'text-green' : 'text-txt-muted'
        )}>
          {msg}
        </span>
      )}
    </div>
  )
}

// ── Dashboard ──────────────────────────────────────────────────────────────────
export default function Dashboard() {
  const { analytics, setAnalytics } = useStore()

  // Trades WebSocket — initial load + live push when trade_data.csv changes
  useWebSocket('ws://localhost:8000/ws/trades/', {
    onMessage: (msg) => {
      if (msg.type === 'trades' && msg.data) setAnalytics(msg.data)
    },
  })

  const rows       = analytics?.rows ?? []
  const recentRows = [...rows].reverse().slice(0, 15)
  const meta       = analytics?.meta ?? {}

  // Reload analytics after backtest finishes
  const reloadAnalytics = async () => {
    try {
      const r = await fetch('/api/trades')
      const d = await r.json()
      if (!d.error) setAnalytics(d)
    } catch {}
  }

  return (
    <div className="flex flex-col gap-3 p-3 min-h-full">

      {/* ── Auto-rerun banner (appears when watcher triggers backtest) ── */}
      <AutoRerunBanner />

      {/* ── Top bar: data info + backtest button ──────────────────── */}
      <div className="flex items-center justify-between flex-wrap gap-2 px-1">
        <div className="flex items-center gap-3 text-[10px] font-mono text-txt-muted">
          {meta.date_from && (
            <>
              <span>Data: <span className="text-txt-secondary">{meta.date_from}</span> → <span className="text-txt-secondary">{meta.date_to}</span></span>
              <span className="w-px h-3 bg-border" />
              <span>{meta.symbols} · {meta.timeframe}</span>
              <span className="w-px h-3 bg-border" />
              <span><span className="text-txt-secondary">{meta.n}</span> trades</span>
            </>
          )}
          {!meta.date_from && <span className="text-yellow">No backtest data — run backtest first</span>}
        </div>
        <RunBacktestBtn onDone={reloadAnalytics} />
      </div>

      {/* ── Candle Chart ──────────────────────────────────────────── */}
      <div style={{ height: '480px' }} className="shrink-0">
        <CandleChart />
      </div>

      {/* ── Metrics ───────────────────────────────────────────────── */}
      <MetricsPanel analytics={analytics} />

      {/* ── Equity + Recent Trades ────────────────────────────────── */}
      <div className="grid grid-cols-1 xl:grid-cols-5 gap-3">
        <div className="xl:col-span-2">
          <EquityCurve analytics={analytics} />
        </div>

        <div className="xl:col-span-3 card p-0 overflow-hidden">
          <div className="px-4 py-3 border-b border-border flex items-center justify-between">
            <div>
              <span className="section-label">Recent Trades</span>
              <span className="text-[10px] font-mono text-txt-muted ml-2">last 15</span>
            </div>
            {meta.date_to && (
              <span className="text-[9px] font-mono text-txt-muted">
                latest: {meta.date_to}
              </span>
            )}
          </div>
          <div className="overflow-x-auto">
            <TradeLog rows={recentRows} compact />
          </div>
        </div>
      </div>
    </div>
  )
}
