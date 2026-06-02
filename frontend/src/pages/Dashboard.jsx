/**
 * Dashboard — main trading view.
 *
 * Layout:
 *  ┌─────────────────────────────────┐
 *  │  CandleChart  (tall, real-time) │
 *  ├─────────────────────────────────┤
 *  │  MetricsPanel  (6 stat cards)   │
 *  ├─────────────────────────────────┤
 *  │  EquityCurve                    │
 *  ├─────────────────────────────────┤
 *  │  Recent Trades (last 15)        │
 *  └─────────────────────────────────┘
 */
import { useEffect } from 'react'
import CandleChart   from '../components/CandleChart'
import MetricsPanel  from '../components/MetricsPanel'
import EquityCurve   from '../components/EquityCurve'
import TradeLog      from '../components/TradeLog'
import useStore      from '../store/useStore'
import { useWebSocket } from '../hooks/useWebSocket'

export default function Dashboard() {
  const { analytics, setAnalytics } = useStore()

  // Trades WebSocket — also handles initial load + live refresh
  useWebSocket('ws://localhost:8000/ws/trades/', {
    onMessage: (msg) => {
      if (msg.type === 'trades' && msg.data) {
        setAnalytics(msg.data)
      }
    },
  })

  const rows = analytics?.rows ?? []
  const recentRows = [...rows].reverse().slice(0, 15)

  return (
    <div className="flex flex-col gap-3 p-3 min-h-full">
      {/* ── Candle Chart ───────────────────────────────────────────── */}
      <div style={{ height: '480px' }} className="shrink-0">
        <CandleChart />
      </div>

      {/* ── Metrics ────────────────────────────────────────────────── */}
      <MetricsPanel analytics={analytics} />

      {/* ── Equity + Recent Trades side-by-side on wide screens ────── */}
      <div className="grid grid-cols-1 xl:grid-cols-5 gap-3">
        <div className="xl:col-span-2">
          <EquityCurve analytics={analytics} />
        </div>

        <div className="xl:col-span-3 card p-0 overflow-hidden">
          <div className="px-4 py-3 border-b border-border">
            <span className="section-label">Recent Trades</span>
            <span className="text-[10px] font-mono text-txt-muted ml-2">last 15</span>
          </div>
          <div className="overflow-x-auto">
            <TradeLog rows={recentRows} compact />
          </div>
        </div>
      </div>
    </div>
  )
}
