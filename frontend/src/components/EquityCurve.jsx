import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis,
  CartesianGrid, Tooltip, ReferenceLine,
} from 'recharts'

function fmt(v) {
  return '$' + v.toLocaleString('en-US', { maximumFractionDigits: 0 })
}

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  const val = payload[0].value
  return (
    <div className="bg-bg-elevated border border-border rounded-sm px-3 py-2 text-[11px] font-mono shadow-xl">
      <div className="text-txt-muted mb-0.5">{label}</div>
      <div className={val >= 0 ? 'text-green' : 'text-red'}>{fmt(val)}</div>
    </div>
  )
}

export default function EquityCurve({ analytics }) {
  if (!analytics?.eq_labels || !analytics?.eq_data) {
    return (
      <div className="card h-52 flex items-center justify-center">
        <span className="text-[10px] text-txt-muted">No equity data</span>
      </div>
    )
  }

  const startCap = analytics.eq_data[0] ?? 0
  const data = analytics.eq_labels.map((label, i) => ({
    label,
    value: analytics.eq_data[i],
  }))

  // Downsample for performance: max 300 points
  const MAX_PTS = 300
  const step    = data.length > MAX_PTS ? Math.floor(data.length / MAX_PTS) : 1
  const sampled = data.filter((_, i) => i % step === 0 || i === data.length - 1)

  const minVal = Math.min(...sampled.map((d) => d.value))
  const maxVal = Math.max(...sampled.map((d) => d.value))
  const padding = (maxVal - minVal) * 0.05

  return (
    <div className="card overflow-hidden p-0">
      <div className="px-4 pt-3 pb-2 border-b border-border">
        <span className="section-label">Equity Curve</span>
      </div>
      <div className="h-52 px-1 py-2">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={sampled} margin={{ top: 4, right: 8, bottom: 0, left: 4 }}>
            <defs>
              <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor="#58a6ff" stopOpacity={0.15} />
                <stop offset="95%" stopColor="#58a6ff" stopOpacity={0.01} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#161b22" vertical={false} />
            <XAxis
              dataKey="label"
              tick={{ fill: '#3d4451', fontSize: 9, fontFamily: 'JetBrains Mono' }}
              tickLine={false}
              axisLine={{ stroke: '#21262d' }}
              interval="preserveStartEnd"
            />
            <YAxis
              tick={{ fill: '#3d4451', fontSize: 9, fontFamily: 'JetBrains Mono' }}
              tickLine={false}
              axisLine={false}
              tickFormatter={fmt}
              domain={[minVal - padding, maxVal + padding]}
              width={62}
            />
            <Tooltip content={<CustomTooltip />} />
            <ReferenceLine y={startCap} stroke="#21262d" strokeDasharray="4 4" />
            <Area
              type="monotone"
              dataKey="value"
              stroke="#58a6ff"
              strokeWidth={2}
              fill="url(#eqGrad)"
              dot={false}
              activeDot={{ r: 3, fill: '#58a6ff' }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
