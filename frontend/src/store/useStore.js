import { create } from 'zustand'

const useStore = create((set, get) => ({
  // ── Symbol / timeframe ─────────────────────────────────────────
  symbol:    'XAUUSD',
  timeframe: 'M1',
  setSymbol:    (s) => set({ symbol: s }),
  setTimeframe: (tf) => set({ timeframe: tf }),

  // ── Live tick data (from WebSocket) ────────────────────────────
  tick: null,          // { price, bid, ask, change, change_pct, bar, time }
  wsStatus: 'disconnected',   // 'connected' | 'disconnected' | 'reconnecting'
  setTick:     (t) => set({ tick: t }),
  setWsStatus: (s) => set({ wsStatus: s }),

  // ── Trade analytics (from WebSocket / REST) ───────────────────
  analytics: null,
  setAnalytics: (a) => set({ analytics: a }),

  // ── UI state ──────────────────────────────────────────────────
  sidebarOpen: true,
  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),

  // Trade log filters
  filters: { symbol: 'all', strategy: 'all', dir: 'all', search: '' },
  setFilter: (key, val) =>
    set((s) => ({ filters: { ...s.filters, [key]: val } })),

  // Trade log pagination
  tradePage: 1,
  tradePageSize: 25,
  setTradePage: (p) => set({ tradePage: p }),
}))

export default useStore
