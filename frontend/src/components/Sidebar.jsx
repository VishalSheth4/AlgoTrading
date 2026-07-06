import { useState, useEffect } from 'react'
import { NavLink } from 'react-router-dom'
import { LayoutDashboard, LineChart, ListOrdered, Settings, IndianRupee, Bell } from 'lucide-react'
import clsx from 'clsx'

const BASE_NAV = [
  { to: '/',        icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/trades',  icon: ListOrdered,     label: 'Trades'    },
  { to: '/alerts',  icon: Bell,            label: 'Alerts'    },
]

export default function Sidebar() {
  const [nseEnabled, setNseEnabled] = useState(false)

  useEffect(() => {
    fetch('/api/nse/config')
      .then(r => r.json())
      .then(d => { if (d.ok) setNseEnabled(!!d.config?.enabled) })
      .catch(() => {})

    // Re-check every 5s in case user toggles in UI
    const id = setInterval(() => {
      fetch('/api/nse/config')
        .then(r => r.json())
        .then(d => { if (d.ok) setNseEnabled(!!d.config?.enabled) })
        .catch(() => {})
    }, 5000)
    return () => clearInterval(id)
  }, [])

  const NAV = [
    ...BASE_NAV,
    ...(nseEnabled ? [{ to: '/nse', icon: IndianRupee, label: 'NSE / BSE' }] : []),
  ]

  return (
    <aside className="w-14 flex flex-col items-center py-3 gap-1 border-r border-border bg-bg-secondary shrink-0">
      {/* Logo */}
      <div className="w-8 h-8 mb-4 rounded-sm bg-accent/10 border border-accent/20 flex items-center justify-center">
        <LineChart size={16} className="text-accent" />
      </div>

      {NAV.map(({ to, icon: Icon, label }) => (
        <NavLink
          key={to}
          to={to}
          end={to === '/'}
          title={label}
          className={({ isActive }) =>
            clsx(
              'w-10 h-10 flex items-center justify-center rounded-sm transition-colors duration-150 group relative',
              isActive
                ? 'bg-accent/15 text-accent'
                : 'text-txt-muted hover:text-txt-secondary hover:bg-bg-elevated'
            )
          }
        >
          <Icon size={17} />
          {/* Tooltip */}
          <span className="absolute left-14 px-2 py-1 text-[10px] font-medium bg-bg-elevated border border-border rounded-sm
                           text-txt-primary whitespace-nowrap opacity-0 pointer-events-none group-hover:opacity-100
                           transition-opacity duration-150 z-50">
            {label}
          </span>
        </NavLink>
      ))}

      <div className="mt-auto">
        <button
          title="Settings"
          className="w-10 h-10 flex items-center justify-center rounded-sm text-txt-muted hover:text-txt-secondary hover:bg-bg-elevated transition-colors"
        >
          <Settings size={16} />
        </button>
      </div>
    </aside>
  )
}
