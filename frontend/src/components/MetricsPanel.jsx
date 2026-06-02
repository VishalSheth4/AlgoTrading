import clsx from 'clsx'
import { TrendingUp, TrendingDown, Target, Activity, BarChart2, Zap } from 'lucide-react'

function MetricCard({ label, value, sub, icon: Icon, color = 'text-txt-primary', highlight }) {
  return (
    <div className={clsx(
      'card flex flex-col gap-1 min-w-0',
      highlight && 'border-accent/20 bg-accent/5'
    )}>
      <div className="flex items-center justify-between">
        <span className="metric-label">{label}</span>
        {Icon && <Icon size={13} className={clsx('opacity-50', color)} />}
      </div>
      <span className={clsx('metric-value', color)}>{value}</span>
      {sub && <span className="text-[10px] font-mono text-txt-muted">{sub}</span>}
    </div>
  )
}

function fDollar(v) {
  if (v == null || isNaN(v)) return '—'
  const abs = Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return (v >= 0 ? '+$' : '-$') + abs
}

function fPct(v) {
  if (v == null || isNaN(v)) return '—'
  return (v >= 0 ? '+' : '') + v.toFixed(2) + '%'
}

export default function MetricsPanel({ analytics }) {
  if (!analytics?.metrics) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-2">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="card animate-pulse">
            <div className="h-3 bg-bg-elevated rounded w-16 mb-2" />
            <div className="h-6 bg-bg-elevated rounded w-20" />
          </div>
        ))}
      </div>
    )
  }

  const m = analytics.metrics
  const pnl      = m.total_profit ?? 0
  const pnlPct   = m.profit_pct   ?? 0
  const winRate  = m.win_rate      ?? 0
  const maxDD    = m.max_dd        ?? 0
  const pf       = m.pf_display    ?? '—'
  const trades   = (analytics.meta?.n) ?? 0
  const avgWin   = m.avg_win       ?? 0
  const avgLoss  = m.avg_loss      ?? 0
  const curStreak = m.cur_s        ?? 0
  const curType   = m.cur_t        ?? 'none'

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-2">
      <MetricCard
        label="Total P&L"
        value={fDollar(pnl)}
        sub={fPct(pnlPct)}
        icon={pnl >= 0 ? TrendingUp : TrendingDown}
        color={pnl >= 0 ? 'text-green' : 'text-red'}
      />
      <MetricCard
        label="Win Rate"
        value={`${winRate}%`}
        sub={`${m.nw ?? 0}W / ${m.nl ?? 0}L`}
        icon={Target}
        color={winRate >= 50 ? 'text-green' : 'text-red'}
      />
      <MetricCard
        label="Max Drawdown"
        value={`${maxDD.toFixed(2)}%`}
        icon={TrendingDown}
        color="text-red"
      />
      <MetricCard
        label="Profit Factor"
        value={pf}
        sub={`${m.nw ?? 0} wins / ${m.nl ?? 0} losses`}
        icon={BarChart2}
        color={parseFloat(pf) >= 1.5 ? 'text-green' : parseFloat(pf) >= 1 ? 'text-yellow' : 'text-red'}
      />
      <MetricCard
        label="Avg Win / Loss"
        value={`$${Math.abs(avgWin).toFixed(0)}`}
        sub={`/ -$${Math.abs(avgLoss).toFixed(0)}`}
        icon={Activity}
        color="text-txt-primary"
      />
      <MetricCard
        label="Current Streak"
        value={curStreak === 0 ? '—' : `${curStreak}×`}
        sub={curType === 'win' ? 'Wins' : curType === 'loss' ? 'Losses' : ''}
        icon={Zap}
        color={curType === 'win' ? 'text-green' : curType === 'loss' ? 'text-red' : 'text-txt-muted'}
      />
    </div>
  )
}
