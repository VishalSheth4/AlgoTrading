import { useEffect, useState } from 'react'
import { Wifi, WifiOff, Clock } from 'lucide-react'
import clsx from 'clsx'
import useStore from '../store/useStore'
import { useWebSocket } from '../hooks/useWebSocket'

export default function Header() {
  const { symbol, tick, wsStatus, setTick, setWsStatus } = useStore()
  const [flash, setFlash]   = useState(null)   // 'up' | 'down' | null
  const [clock, setClock]   = useState('')
  const prevPrice = useState(null)

  // Clock
  useEffect(() => {
    const tick = () => setClock(new Date().toLocaleTimeString('en-GB', {
      hour: '2-digit', minute: '2-digit', second: '2-digit',
      timeZone: 'Asia/Kolkata',
    }) + ' IST')
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])

  // Price WebSocket
  useWebSocket(
    `ws://localhost:8000/ws/price/${symbol}/`,
    {
      onOpen:    () => setWsStatus('connected'),
      onClose:   () => setWsStatus('disconnected'),
      onError:   () => setWsStatus('disconnected'),
      onMessage: (msg) => {
        if (msg.type === 'tick') {
          setFlash(msg.change >= 0 ? 'up' : 'down')
          setTimeout(() => setFlash(null), 600)
          setTick(msg)
        }
      },
    }
  )

  const price      = tick?.price ?? '—'
  const change     = tick?.change ?? 0
  const changePct  = tick?.change_pct ?? 0
  const isUp       = change >= 0
  const connected  = wsStatus === 'connected'

  return (
    <header className="h-11 flex items-center px-4 gap-6 border-b border-border bg-bg-secondary shrink-0">
      {/* Symbol */}
      <div className="flex items-center gap-2">
        <span className="text-xs font-bold font-mono text-txt-primary tracking-wider">{symbol}</span>
        <span className="text-[10px] text-txt-muted font-mono">M1</span>
      </div>

      {/* Live Price */}
      <div className={clsx(
        'flex items-baseline gap-2 px-2 py-0.5 rounded-sm transition-colors duration-300',
        flash === 'up'   && 'flash-up',
        flash === 'down' && 'flash-down',
      )}>
        <span className={clsx(
          'text-xl font-bold font-mono tabular-nums',
          isUp ? 'text-green' : 'text-red'
        )}>
          {typeof price === 'number' ? price.toFixed(2) : price}
        </span>
        <span className={clsx('text-xs font-mono', isUp ? 'text-green' : 'text-red')}>
          {isUp ? '+' : ''}{change.toFixed(2)} ({isUp ? '+' : ''}{changePct.toFixed(3)}%)
        </span>
      </div>

      {/* Bid / Ask */}
      {tick && (
        <div className="flex items-center gap-3 text-[10px] font-mono">
          <span className="text-red">{tick.bid.toFixed(2)}</span>
          <span className="text-txt-muted">/</span>
          <span className="text-green">{tick.ask.toFixed(2)}</span>
        </div>
      )}

      {/* Spacer */}
      <div className="flex-1" />

      {/* Clock */}
      <div className="flex items-center gap-1.5 text-[10px] font-mono text-txt-muted">
        <Clock size={11} />
        {clock}
      </div>

      {/* WS Status */}
      <div className={clsx(
        'flex items-center gap-1.5 text-[10px] font-mono',
        connected ? 'text-green' : 'text-red'
      )}>
        {connected
          ? <><span className="w-1.5 h-1.5 rounded-full bg-green live-dot" /> LIVE</>
          : <><WifiOff size={11} /> OFFLINE</>
        }
      </div>
    </header>
  )
}
