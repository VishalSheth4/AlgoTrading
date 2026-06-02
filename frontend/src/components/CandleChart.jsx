/**
 * CandleChart — real-time candlestick chart powered by LightweightCharts v4.
 *
 * Receives historical bars + supertrend via the /ws/price/<symbol>/ WebSocket,
 * then updates the "forming" M1 candle every second from subsequent tick messages.
 */
import { useEffect, useRef, useState, useCallback } from 'react'
import { createChart, CrosshairMode } from 'lightweight-charts'
import { Maximize2, TrendingUp, Minus, RefreshCw } from 'lucide-react'
import clsx from 'clsx'
import useStore from '../store/useStore'
import { useWebSocket } from '../hooks/useWebSocket'

const CHART_THEME = {
  layout:     { background: { color: '#0d1117' }, textColor: '#8b949e' },
  grid:       { vertLines: { color: '#161b22' }, horzLines: { color: '#161b22' } },
  crosshair:  { mode: CrosshairMode.Normal },
  rightPriceScale: { borderColor: '#21262d' },
  timeScale:       { borderColor: '#21262d', timeVisible: true, secondsVisible: false },
}

const TF_OPTIONS = ['M1', 'M5', 'M15', 'H1', 'H4']

export default function CandleChart() {
  const symbol      = useStore((s) => s.symbol)
  const containerRef = useRef(null)
  const chartRef     = useRef(null)
  const candleRef    = useRef(null)   // candlestick series
  const stRef        = useRef(null)   // supertrend series
  const histLoaded   = useRef(false)

  const [tf, setTf]         = useState('M1')
  const [stVisible, setStV] = useState(true)
  const [barCount, setBarCount] = useState(0)
  const [loading, setLoading]   = useState(true)
  const [ohlcInfo, setOhlcInfo] = useState(null)  // crosshair bar info

  // ── Init chart ─────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      ...CHART_THEME,
      width:  containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
      handleScroll: true,
      handleScale:  true,
    })

    const candles = chart.addCandlestickSeries({
      upColor:        '#26a69a',
      downColor:      '#ef5350',
      borderUpColor:  '#26a69a',
      borderDownColor:'#ef5350',
      wickUpColor:    '#26a69a',
      wickDownColor:  '#ef5350',
    })

    const stLine = chart.addLineSeries({
      lineWidth:             2,
      priceLineVisible:      false,
      lastValueVisible:      false,
      crosshairMarkerVisible:false,
    })

    chart.subscribeCrosshairMove((param) => {
      if (!param.time || !param.seriesData) return
      const bar = param.seriesData.get(candles)
      if (bar) setOhlcInfo(bar)
    })

    // Resize observer
    const ro = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth })
      }
    })
    ro.observe(containerRef.current)

    chartRef.current  = chart
    candleRef.current = candles
    stRef.current     = stLine

    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current = null
    }
  }, [])

  // ── WebSocket ──────────────────────────────────────────────────────────────
  const handleMessage = useCallback((msg) => {
    if (!candleRef.current) return

    if (msg.type === 'history') {
      const bars = msg.bars ?? []
      if (!bars.length) return

      candleRef.current.setData(bars)
      if (msg.markers?.length) {
        candleRef.current.setMarkers(msg.markers)
      }
      if (msg.supertrend?.length && stRef.current) {
        stRef.current.setData(msg.supertrend)
      }

      chartRef.current?.timeScale().fitContent()
      histLoaded.current = true
      setBarCount(bars.length)
      setLoading(false)
    }

    if (msg.type === 'tick' && histLoaded.current && msg.bar) {
      candleRef.current.update(msg.bar)
    }
  }, [])

  useWebSocket(
    `ws://localhost:8000/ws/price/${symbol}/`,
    {
      onOpen:    () => { histLoaded.current = false; setLoading(true) },
      onMessage: handleMessage,
    }
  )

  // ── Supertrend toggle ──────────────────────────────────────────────────────
  const toggleST = () => {
    const next = !stVisible
    stRef.current?.applyOptions({ visible: next })
    setStV(next)
  }

  const fitAll = () => chartRef.current?.timeScale().fitContent()

  return (
    <div className="flex flex-col w-full h-full bg-bg-secondary border border-border rounded-sm overflow-hidden">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border shrink-0">
        <span className="text-[10px] font-bold font-mono text-txt-primary tracking-wider mr-1">{symbol}</span>

        {/* TF selector */}
        <div className="flex gap-0.5">
          {TF_OPTIONS.map((t) => (
            <button
              key={t}
              onClick={() => setTf(t)}
              className={clsx(
                'px-2 py-0.5 text-[10px] font-mono rounded-sm transition-colors',
                tf === t
                  ? 'bg-accent/20 text-accent border border-accent/30'
                  : 'text-txt-muted hover:text-txt-secondary border border-transparent'
              )}
            >
              {t}
            </button>
          ))}
        </div>

        <div className="w-px h-4 bg-border mx-1" />

        {/* ST toggle */}
        <button
          onClick={toggleST}
          className={clsx(
            'flex items-center gap-1 px-2 py-0.5 text-[10px] font-mono rounded-sm border transition-colors',
            stVisible
              ? 'border-purple/30 bg-purple/10 text-purple'
              : 'border-border text-txt-muted hover:text-txt-secondary'
          )}
        >
          <TrendingUp size={11} />
          ST
        </button>

        <div className="flex-1" />

        {/* Bar count */}
        {barCount > 0 && (
          <span className="text-[10px] font-mono text-txt-muted">
            {barCount.toLocaleString()} bars
          </span>
        )}

        {/* Fit All */}
        <button onClick={fitAll} className="btn" title="Fit all">
          <Maximize2 size={11} />
        </button>

        {/* Loading spinner */}
        {loading && <RefreshCw size={12} className="text-accent animate-spin" />}
      </div>

      {/* OHLC info bar */}
      {ohlcInfo && (
        <div className="flex gap-4 px-3 py-1 text-[10px] font-mono border-b border-border/40 bg-bg-primary/40 shrink-0">
          <span className="text-txt-muted">O <span className="text-txt-primary">{ohlcInfo.open?.toFixed(2)}</span></span>
          <span className="text-txt-muted">H <span className="text-green">{ohlcInfo.high?.toFixed(2)}</span></span>
          <span className="text-txt-muted">L <span className="text-red">{ohlcInfo.low?.toFixed(2)}</span></span>
          <span className="text-txt-muted">C <span className="text-txt-primary">{ohlcInfo.close?.toFixed(2)}</span></span>
        </div>
      )}

      {/* Chart */}
      <div ref={containerRef} className="flex-1 min-h-0" />
    </div>
  )
}
