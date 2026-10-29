import { useCallback, useState } from 'react'
import { Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom'
import { Info, X } from 'lucide-react'
import Sidebar from './components/Sidebar.jsx'
import Navbar from './components/Navbar.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Infrastructure from './pages/Infrastructure.jsx'
import Installation from './pages/Installation.jsx'
import Pipelines from './pages/Pipelines.jsx'
import Deployments from './pages/Deployments.jsx'
import Observability from './pages/Observability.jsx'
import Logs from './pages/Logs.jsx'
import CyberResilience from './pages/CyberResilience.jsx'
import Settings from './pages/Settings.jsx'

function AppShell() {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [notice, setNotice] = useState('')
  const location = useLocation()
  const notify = useCallback((message) => setNotice(message), [])

  return (
    <div className="app-shell">
      <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />
      <div className="app-body">
        <Navbar onOpenSidebar={() => setSidebarOpen(true)} />
        <main className="main-content" id="main-content" key={location.pathname}>
          <Outlet context={{ notify }} />
        </main>
      </div>
      {notice && (
        <div className="toast" role="status" aria-live="polite">
          <Info size={19} aria-hidden="true" />
          <span>{notice}</span>
          <button className="icon-button" type="button" aria-label="Fermer le message" onClick={() => setNotice('')}><X size={18}/></button>
        </div>
      )}
    </div>
  )
}

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="dashboard" element={<Dashboard />} />
        <Route path="infrastructure" element={<Infrastructure />} />
        <Route path="installation" element={<Installation />} />
        <Route path="pipelines" element={<Pipelines />} />
        <Route path="deployments" element={<Deployments />} />
        <Route path="observability" element={<Observability />} />
        <Route path="logs" element={<Logs />} />
        <Route path="cyber-resilience" element={<CyberResilience />} />
        <Route path="settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Route>
    </Routes>
  )
}
