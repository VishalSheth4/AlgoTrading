import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Sidebar   from './components/Sidebar'
import Header    from './components/Header'
import Dashboard from './pages/Dashboard'
import Trades    from './pages/Trades'
import useStore  from './store/useStore'

export default function App() {
  const sidebarOpen = useStore((s) => s.sidebarOpen)

  return (
    <BrowserRouter>
      <div className="flex h-screen w-screen overflow-hidden bg-bg-primary">
        <Sidebar />

        <div className={`flex flex-col flex-1 min-w-0 transition-all duration-200`}>
          <Header />
          <main className="flex-1 overflow-y-auto overflow-x-hidden bg-bg-primary">
            <Routes>
              <Route path="/"        element={<Dashboard />} />
              <Route path="/trades"  element={<Trades />}    />
              <Route path="*"        element={<Navigate to="/" replace />} />
            </Routes>
          </main>
        </div>
      </div>
    </BrowserRouter>
  )
}
