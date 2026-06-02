/**
 * Trades — full trade log with all filters + pagination.
 */
import { useEffect } from 'react'
import TradeLog   from '../components/TradeLog'
import useStore   from '../store/useStore'
import { useWebSocket } from '../hooks/useWebSocket'
import { BarChart2, TrendingUp, TrendingDown, Percent } from 'lucide-react'
import clsx from 'clsx'

function StatPill({ label, value, color }) {
  return (
    <div className="flex items-center gap-2 px-3 py-1.5 border border-border rounded-sm bg-bg-tertiary">
      <span className="text-[9px] font-bold tracking-widest uppercase text-txt-muted">{label}</span>
      <span className={clsx('text-sm font-bold font-mono tabular-nums', color ?? 'text-txt-primary')}>{value}</span>
    </div>
  )
}

export default function Trades() {
  const { analytics, setAnalytics } = useStore()

  useWebSocket('ws://localhost:8000/ws/trades/', {
    onMessage: (msg) => {
      if (msg.type === 'trades' && msg.data) setAnalytics(msg.data)
    },
  })

  const rows = analytics?.rows ?? []
  const m    = analytics?.metrics ?? {}
  const meta = analytics?.meta ?? {}

  const allRows = [...rows].reverse()

  return (
    <div className="flex flex-col gap-3 p-3 min-h-full">
      {/* Header bar */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-sm font-bold text-txt-primary tracking-wide">Trade Log</h1>
          {meta.date_from && (
            <p className="text-[10px] font-mono text-txt-muted mt-0.5">
              {meta.date_from} → {meta.date_to} · {meta.symbols} · {meta.timeframe}
            </p>
          )}
        </div>

        {/* Quick stats */}
        <div className="flex flex-wrap gap-2">
          <StatPill label="Total" value={meta.n ?? 0} />
          <StatPill
            label="P&L"
            value={(m.total_profit >= 0 ? '+$' : '-$') + Math.abs(m.total_profit ?? 0).toFixed(2)}
            color={m.total_profit >= 0 ? 'text-green' : 'text-red'}
          />
          <StatPill
            label="Win Rate"
            value={`${m.win_rate ?? 0}%`}
            color={(m.win_rate ?? 0) >= 50 ? 'text-green' : 'text-red'}
          />
          <StatPill label="PF" value={m.pf_display ?? '—'} color="text-accent" />
        </div>
      </div>

      {/* Full trade log */}
      <div className="card p-0 overflow-hidden">
        <div className="p-3 border-b border-border">
          <TradeLog rows={allRows} pageSize={30} />
        </div>
      </div>
    </div>
  )
}
