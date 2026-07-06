/**
 * Alerts — notification channel configuration.
 * Configure Telegram, ntfy.sh, and Email alerts.
 * Test each channel with a single click.
 */
import { useState, useEffect } from 'react'
import {
  Send, CheckCircle, AlertCircle, RefreshCw,
  Bell, BellOff, MessageCircle, Mail, Smartphone,
} from 'lucide-react'
import clsx from 'clsx'

const inp = 'bg-bg-tertiary border border-border rounded-sm px-3 py-2 text-[12px] font-mono text-txt-primary outline-none focus:border-accent/50 w-full placeholder:text-txt-muted'
const tog = (on) => clsx(
  'relative w-10 h-5 rounded-full border transition-colors cursor-pointer shrink-0',
  on ? 'bg-green/20 border-green/40' : 'bg-bg-elevated border-border'
)
const dot = (on) => clsx(
  'absolute top-0.5 w-4 h-4 rounded-full border transition-all',
  on ? 'left-5 bg-green border-green' : 'left-0.5 bg-txt-muted border-txt-muted'
)

function Toggle({ value, onChange }) {
  return (
    <button className={tog(value)} onClick={() => onChange(!value)} type="button">
      <span className={dot(value)} />
    </button>
  )
}

function Section({ icon: Icon, title, color, children }) {
  return (
    <div className="card flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <Icon size={16} className={color} />
        <span className="text-sm font-bold text-txt-primary">{title}</span>
      </div>
      {children}
    </div>
  )
}

function TestBtn({ channel, label }) {
  const [state, setState] = useState('idle')
  const [msg,   setMsg]   = useState('')

  const test = async () => {
    setState('loading')
    setMsg('')
    try {
      const r = await fetch(`/api/alerts/test?channel=${channel}`, { method: 'POST' })
      const d = await r.json()
      if (d.ok) {
        const res = d.results?.[channel] ?? Object.values(d.results ?? {})[0]
        if (res?.ok) { setState('ok'); setMsg('Alert sent!') }
        else          { setState('err'); setMsg(res?.error || 'Failed') }
      } else {
        setState('err'); setMsg(d.error || 'Request failed')
      }
    } catch (e) {
      setState('err'); setMsg(String(e))
    }
    setTimeout(() => setState('idle'), 5000)
  }

  return (
    <div className="flex items-center gap-2">
      <button
        onClick={test}
        disabled={state === 'loading'}
        className={clsx(
          'flex items-center gap-1.5 px-3 py-1.5 rounded-sm text-[11px] font-bold font-mono border transition-colors',
          state === 'loading' ? 'border-yellow/30 bg-yellow/10 text-yellow cursor-wait' :
          state === 'ok'      ? 'border-green/30  bg-green/10  text-green' :
          state === 'err'     ? 'border-red/30    bg-red/10    text-red' :
                                'border-border bg-bg-elevated text-txt-secondary hover:text-txt-primary'
        )}
      >
        {state === 'loading' ? <RefreshCw size={11} className="animate-spin" /> :
         state === 'ok'      ? <CheckCircle size={11} /> :
         state === 'err'     ? <AlertCircle size={11} /> :
                               <Send size={11} />}
        Test {label}
      </button>
      {msg && (
        <span className={clsx('text-[10px] font-mono', state === 'ok' ? 'text-green' : 'text-red')}>
          {msg}
        </span>
      )}
    </div>
  )
}

export default function Alerts() {
  const [cfg,     setCfg]    = useState(null)
  const [loading, setLoading]= useState(true)
  const [saved,   setSaved]  = useState('')

  useEffect(() => {
    fetch('/api/alerts/config')
      .then(r => r.json())
      .then(d => { if (d.ok) setCfg(d.config); setLoading(false) })
  }, [])

  const set = (path, val) => {
    const keys = path.split('.')
    setCfg(prev => {
      const next = { ...prev }
      let cur = next
      for (let i = 0; i < keys.length - 1; i++) {
        cur[keys[i]] = { ...cur[keys[i]] }
        cur = cur[keys[i]]
      }
      cur[keys[keys.length - 1]] = val
      return next
    })
  }

  const save = async () => {
    const r = await fetch('/api/alerts/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg),
    })
    const d = await r.json()
    setSaved(d.ok ? 'Saved!' : d.error)
    setTimeout(() => setSaved(''), 3000)
  }

  if (loading) return (
    <div className="flex items-center justify-center h-64 text-txt-muted text-sm">Loading…</div>
  )

  const enabled = cfg?.enabled ?? false

  return (
    <div className="flex flex-col gap-4 p-4 max-w-2xl">

      {/* ── Header ─────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-sm font-bold text-txt-primary">Alert Notifications</h1>
          <p className="text-[10px] font-mono text-txt-muted mt-0.5">
            Get instant alerts on your iPhone for every trade entry &amp; exit
          </p>
        </div>

        <div className="flex items-center gap-3">
          <span className="text-[11px] font-mono text-txt-muted">
            {enabled ? 'Alerts ON' : 'Alerts OFF'}
          </span>
          <Toggle value={enabled} onChange={v => set('enabled', v)} />
          {enabled
            ? <Bell size={16} className="text-green" />
            : <BellOff size={16} className="text-txt-muted" />}
        </div>
      </div>

      {!enabled && (
        <div className="card border-yellow/20 bg-yellow/5 text-yellow text-[11px] font-mono py-3 text-center">
          Master switch is OFF — toggle above to enable alerts
        </div>
      )}

      <div className={clsx('flex flex-col gap-4', !enabled && 'opacity-50 pointer-events-none')}>

        {/* ── Telegram ───────────────────────────────────────────── */}
        <Section icon={MessageCircle} title="Telegram (Recommended — Free, Instant)" color="text-accent">
          <div className="flex items-center gap-3">
            <Toggle value={!!cfg?.telegram?.enabled} onChange={v => set('telegram.enabled', v)} />
            <span className="text-[11px] font-mono text-txt-secondary">
              {cfg?.telegram?.enabled ? 'Enabled' : 'Disabled'}
            </span>
          </div>

          <div className="bg-bg-primary border border-border/50 rounded-sm p-3 text-[10px] font-mono text-txt-muted leading-relaxed">
            <span className="text-accent font-bold">Setup (2 minutes):</span><br/>
            1. Open Telegram → search <span className="text-txt-primary">@BotFather</span> → send <span className="text-txt-primary">/newbot</span><br/>
            2. Follow prompts → copy the <span className="text-txt-primary">bot token</span><br/>
            3. Search your new bot in Telegram → click Start<br/>
            4. Visit <span className="text-txt-primary">api.telegram.org/bot&lt;TOKEN&gt;/getUpdates</span><br/>
            5. Copy the <span className="text-txt-primary">"id"</span> from "chat" object → that is your Chat ID
          </div>

          <div className="grid grid-cols-1 gap-2">
            <div className="flex flex-col gap-1">
              <label className="section-label">Bot Token</label>
              <input className={inp} type="password"
                placeholder="1234567890:ABCdef..."
                value={cfg?.telegram?.bot_token ?? ''}
                onChange={e => set('telegram.bot_token', e.target.value)} />
            </div>
            <div className="flex flex-col gap-1">
              <label className="section-label">Chat ID</label>
              <input className={inp}
                placeholder="123456789"
                value={cfg?.telegram?.chat_id ?? ''}
                onChange={e => set('telegram.chat_id', e.target.value)} />
            </div>
          </div>

          <TestBtn channel="telegram" label="Telegram" />
        </Section>

        {/* ── ntfy.sh ─────────────────────────────────────────────── */}
        <Section icon={Smartphone} title="ntfy.sh (Free Push — No Account Needed)" color="text-purple">
          <div className="flex items-center gap-3">
            <Toggle value={!!cfg?.ntfy?.enabled} onChange={v => set('ntfy.enabled', v)} />
            <span className="text-[11px] font-mono text-txt-secondary">
              {cfg?.ntfy?.enabled ? 'Enabled' : 'Disabled'}
            </span>
          </div>

          <div className="bg-bg-primary border border-border/50 rounded-sm p-3 text-[10px] font-mono text-txt-muted leading-relaxed">
            <span className="text-purple font-bold">Setup:</span><br/>
            1. Install <span className="text-txt-primary">"ntfy"</span> from iPhone App Store (free)<br/>
            2. In the app, tap + and subscribe to your topic name below<br/>
            3. Pick a <span className="text-txt-primary">unique topic name</span> (anyone who knows it gets your alerts!)
          </div>

          <div className="grid grid-cols-1 gap-2">
            <div className="flex flex-col gap-1">
              <label className="section-label">Topic Name (unique, private)</label>
              <input className={inp}
                placeholder="my-trading-alerts-xyz123"
                value={cfg?.ntfy?.topic ?? ''}
                onChange={e => set('ntfy.topic', e.target.value)} />
            </div>
          </div>

          <TestBtn channel="ntfy" label="ntfy.sh" />
        </Section>

        {/* ── Email ──────────────────────────────────────────────── */}
        <Section icon={Mail} title="Email (Gmail → iPhone Mail App)" color="text-yellow">
          <div className="flex items-center gap-3">
            <Toggle value={!!cfg?.email?.enabled} onChange={v => set('email.enabled', v)} />
            <span className="text-[11px] font-mono text-txt-secondary">
              {cfg?.email?.enabled ? 'Enabled' : 'Disabled'}
            </span>
          </div>

          <div className="bg-bg-primary border border-border/50 rounded-sm p-3 text-[10px] font-mono text-txt-muted leading-relaxed">
            <span className="text-yellow font-bold">Gmail App Password:</span><br/>
            Google Account → Security → 2-Step Verification → App Passwords → Mail<br/>
            Use that 16-character password below, <span className="text-txt-primary">NOT your Gmail login password</span>
          </div>

          <div className="grid grid-cols-1 gap-2">
            <div className="flex flex-col gap-1">
              <label className="section-label">Gmail Address</label>
              <input className={inp} type="email"
                placeholder="yourname@gmail.com"
                value={cfg?.email?.username ?? ''}
                onChange={e => set('email.username', e.target.value)} />
            </div>
            <div className="flex flex-col gap-1">
              <label className="section-label">Gmail App Password</label>
              <input className={inp} type="password"
                placeholder="xxxx xxxx xxxx xxxx"
                value={cfg?.email?.password ?? ''}
                onChange={e => set('email.password', e.target.value)} />
            </div>
            <div className="flex flex-col gap-1">
              <label className="section-label">Send Alerts To (email)</label>
              <input className={inp} type="email"
                placeholder="Leave blank to use Gmail address above"
                value={cfg?.email?.to ?? ''}
                onChange={e => set('email.to', e.target.value)} />
            </div>
          </div>

          <TestBtn channel="email" label="Email" />
        </Section>

      </div>

      {/* ── Save ───────────────────────────────────────────────────── */}
      <div className="flex items-center gap-3">
        <button
          onClick={save}
          className="btn-primary px-6 py-2 text-[12px]"
        >
          Save All Settings
        </button>
        <button
          onClick={() => { fetch('/api/alerts/test?channel=all', { method: 'POST' }) }}
          className="btn px-4 py-2 text-[12px] flex items-center gap-1.5"
        >
          <Send size={12} /> Test All Channels
        </button>
        {saved && (
          <span className={clsx('text-[11px] font-mono', saved === 'Saved!' ? 'text-green' : 'text-red')}>
            {saved}
          </span>
        )}
      </div>

      {/* ── What triggers an alert ─────────────────────────────────── */}
      <div className="card border-border/40 bg-bg-secondary/50">
        <span className="section-label mb-3 block">What triggers an alert</span>
        <div className="grid grid-cols-1 gap-2 text-[11px] font-mono">
          <div className="flex gap-3"><span>🟢</span><span className="text-txt-secondary">BUY signal — entry alert with symbol, price, SL, TP</span></div>
          <div className="flex gap-3"><span>🔴</span><span className="text-txt-secondary">SHORT signal — entry alert with symbol, price, SL, TP</span></div>
          <div className="flex gap-3"><span>✅</span><span className="text-txt-secondary">Take-profit hit — exit alert with P&L</span></div>
          <div className="flex gap-3"><span>❌</span><span className="text-txt-secondary">Stop-loss hit — exit alert with P&L</span></div>
          <div className="flex gap-3"><span>⏱</span><span className="text-txt-secondary">Session close — forced exit alert</span></div>
        </div>
      </div>
    </div>
  )
}
