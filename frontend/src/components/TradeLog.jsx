import { useMemo, useState } from 'react'
import clsx from 'clsx'
import { ChevronLeft, ChevronRight, Search } from 'lucide-react'

// ── Helpers ────────────────────────────────────────────────────────────────────
function fP(v) {
  if (v == null) return '—'
  const abs = Math.abs(v).toFixed(2)
  return (v >= 0 ? '+$' : '-$') + abs
}
function fC(v) {
  if (v == null) return '—'
  return '$' + Number(v).toLocaleString('en-US', { minimumFractionDigits: 2 })
}

const DIR_BADGE = {
  LONG:  'badge-long',
  SHORT: 'badge-short',
}
const CLOSE_BADGE = {
  'R:R':     'badge-rr',
  TP:        'badge-tp',
  SL:        'badge-sl',
  ST:        'badge-st',
  SESSION:   'badge-session',
  REV:       'badge-session',
}

function Badge({ label, cls }) {
  return <span className={clsx('badge', cls ?? 'badge-sl')}>{label}</span>
}

// ── Main component ─────────────────────────────────────────────────────────────
export default function TradeLog({ rows = [], pageSize = 25, compact = false }) {
  const [page, setPage]       = useState(1)
  const [search, setSearch]   = useState('')
  const [symFilter, setSym]   = useState('all')
  const [stFilter,  setSt]    = useState('all')
  const [dirFilter, setDir]   = useState('all')

  const symbols    = useMemo(() => ['all', ...new Set(rows.map((r) => r.symbol).filter(Boolean))], [rows])
  const strategies = useMemo(() => ['all', ...new Set(rows.map((r) => r.strategy).filter(Boolean))], [rows])

  const filtered = useMemo(() => {
    let r = rows
    if (symFilter !== 'all') r = r.filter((x) => x.symbol   === symFilter)
    if (stFilter  !== 'all') r = r.filter((x) => x.strategy === stFilter)
    if (dirFilter !== 'all') r = r.filter((x) => x.dir      === dirFilter)
    if (search.trim()) {
      const q = search.toLowerCase()
      r = r.filter((x) =>
        (x.symbol   ?? '').toLowerCase().includes(q) ||
        (x.strategy ?? '').toLowerCase().includes(q) ||
        String(x.entry).includes(q) || String(x.exit).includes(q)
      )
    }
    return r
  }, [rows, symFilter, stFilter, dirFilter, search])

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize))
  const safePage   = Math.min(page, totalPages)
  const pageRows   = filtered.slice((safePage - 1) * pageSize, safePage * pageSize)

  const selStyle = {
    background: '#161b22', color: '#8b949e', border: '1px solid #21262d',
    borderRadius: '2px', padding: '3px 7px', fontSize: '10px',
    cursor: 'pointer', outline: 'none', fontFamily: "'JetBrains Mono', monospace",
  }

  return (
    <div className="flex flex-col gap-2">
      {/* Filters */}
      {!compact && (
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1.5 border border-border rounded-sm px-2 bg-bg-tertiary flex-1 min-w-32 max-w-52">
            <Search size={11} className="text-txt-muted shrink-0" />
            <input
              value={search}
              onChange={(e) => { setSearch(e.target.value); setPage(1) }}
              placeholder="Search…"
              className="bg-transparent text-[11px] font-mono text-txt-secondary outline-none w-full placeholder:text-txt-muted"
            />
          </div>
          <select value={symFilter} onChange={(e) => { setSym(e.target.value); setPage(1) }} style={selStyle}>
            {symbols.map((s) => <option key={s} value={s}>{s === 'all' ? 'All Symbols' : s}</option>)}
          </select>
          <select value={stFilter} onChange={(e) => { setSt(e.target.value); setPage(1) }} style={selStyle}>
            {strategies.map((s) => <option key={s} value={s}>{s === 'all' ? 'All Strategies' : s}</option>)}
          </select>
          <select value={dirFilter} onChange={(e) => { setDir(e.target.value); setPage(1) }} style={selStyle}>
            <option value="all">All Dirs</option>
            <option value="LONG">LONG</option>
            <option value="SHORT">SHORT</option>
          </select>
          <span className="text-[10px] font-mono text-txt-muted ml-auto">
            {filtered.length.toLocaleString()} trades
          </span>
        </div>
      )}

      {/* Table */}
      <div className="overflow-x-auto rounded-sm border border-border">
        <table className="w-full min-w-[780px]">
          <thead>
            <tr>
              <th className="tbl-th w-8">#</th>
              <th className="tbl-th">Entry Time</th>
              <th className="tbl-th">Exit Time</th>
              <th className="tbl-th">Symbol</th>
              <th className="tbl-th">Strategy</th>
              <th className="tbl-th">Dir</th>
              <th className="tbl-th text-right">Entry</th>
              <th className="tbl-th text-right">SL</th>
              <th className="tbl-th text-right">Target</th>
              <th className="tbl-th text-right">Exit</th>
              <th className="tbl-th">Closed By</th>
              <th className="tbl-th text-right">Lot</th>
              <th className="tbl-th text-right">P&amp;L</th>
              <th className="tbl-th text-right">Capital</th>
            </tr>
          </thead>
          <tbody>
            {pageRows.length === 0 && (
              <tr>
                <td colSpan={14} className="tbl-td text-center text-txt-muted py-8">
                  No trades match the current filters
                </td>
              </tr>
            )}
            {pageRows.map((r, i) => (
              <tr
                key={r.num ?? i}
                className="hover:bg-bg-elevated transition-colors duration-100 group"
              >
                <td className="tbl-td text-txt-muted">{r.num}</td>
                <td className="tbl-td text-txt-muted whitespace-nowrap">{r.entry_time ?? '—'}</td>
                <td className="tbl-td text-txt-muted whitespace-nowrap">{r.time}</td>
                <td className="tbl-td">
                  <span className="text-accent font-mono text-[11px]">{r.symbol}</span>
                </td>
                <td className="tbl-td text-[10px] text-txt-muted whitespace-nowrap max-w-[140px] overflow-hidden text-ellipsis">
                  {r.strategy || '—'}
                </td>
                <td className="tbl-td">
                  <Badge label={r.dir} cls={DIR_BADGE[r.dir] ?? 'badge-sl'} />
                </td>
                <td className="tbl-td text-right text-txt-primary">{r.entry}</td>
                <td className="tbl-td text-right text-red">{r.sl}</td>
                <td className="tbl-td text-right text-purple">{r.target}</td>
                <td className="tbl-td text-right text-txt-primary">{r.exit}</td>
                <td className="tbl-td">
                  <Badge label={r.label || 'SL'} cls={CLOSE_BADGE[r.label] ?? 'badge-sl'} />
                </td>
                <td className="tbl-td text-right text-txt-muted">{r.lot}</td>
                <td className={clsx('tbl-td text-right font-mono', r.profit >= 0 ? 'text-green' : 'text-red')}>
                  {fP(r.profit)}
                </td>
                <td className="tbl-td text-right text-accent">{fC(r.cap)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-1 mt-1">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={safePage === 1}
            className="btn p-1.5 disabled:opacity-30 disabled:cursor-not-allowed"
          >
            <ChevronLeft size={12} />
          </button>

          {Array.from({ length: Math.min(7, totalPages) }, (_, i) => {
            let p
            if (totalPages <= 7) { p = i + 1 }
            else if (safePage <= 4) { p = i + 1 }
            else if (safePage >= totalPages - 3) { p = totalPages - 6 + i }
            else { p = safePage - 3 + i }
            return (
              <button
                key={p}
                onClick={() => setPage(p)}
                className={clsx('btn min-w-[28px] justify-center',
                  p === safePage && 'bg-accent/20 border-accent/30 text-accent')}
              >
                {p}
              </button>
            )
          })}

          <button
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={safePage === totalPages}
            className="btn p-1.5 disabled:opacity-30 disabled:cursor-not-allowed"
          >
            <ChevronRight size={12} />
          </button>
        </div>
      )}
    </div>
  )
}
