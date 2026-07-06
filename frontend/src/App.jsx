import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Sidebar     from './components/Sidebar'
import Header      from './components/Header'
import Dashboard   from './pages/Dashboard'
import Trades      from './pages/Trades'
import NseBacktest from './pages/NseBacktest'
import Alerts      from './pages/Alerts'

export default function App() {
  return (
    <BrowserRouter>
      <div className="flex h-screen w-screen overflow-hidden bg-bg-primary">
        <Sidebar />
        <div className="flex flex-col flex-1 min-w-0">
          <Header />
          <main className="flex-1 overflow-y-auto overflow-x-hidden bg-bg-primary">
            <Routes>
              <Route path="/"     element={<Dashboard />}   />
              <Route path="/trades" element={<Trades />}    />
              <Route path="/nse"    element={<NseBacktest />} />
              <Route path="/alerts" element={<Alerts />}      />
              <Route path="*"     element={<Navigate to="/" replace />} />
            </Routes>
          </main>
        </div>
      </div>
    </BrowserRouter>
  )
}
