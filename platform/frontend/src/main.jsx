import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import AppErrorBoundary from './components/AppErrorBoundary.jsx'
import { AuthProvider } from './context/AuthContext.jsx'
import './styles/main.css'
import './styles/auth.css'
import './styles/deploy-integrations.css'
import './styles/responsive.css'
import './styles/deploy-simple.css'
import './styles/sites.css'

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <AppErrorBoundary><AuthProvider><App /></AuthProvider></AppErrorBoundary>
  </React.StrictMode>,
)
