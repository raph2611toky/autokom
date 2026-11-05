import { useEffect, useState } from 'react'
import Header from './components/Header.jsx'
import Landing from './pages/Landing.jsx'
import Login from './pages/Login.jsx'
import Register from './pages/Register.jsx'
import VerifyEmail from './pages/VerifyEmail.jsx'
import ForgotPassword from './pages/ForgotPassword.jsx'
import ResetPassword from './pages/ResetPassword.jsx'
import Deploy from './pages/Deploy.jsx'
import Sites from './pages/Sites.jsx'
import Projects from './pages/Projects.jsx'
import Pricing from './pages/Pricing.jsx'
import { API_AVAILABLE } from './config/runtime.js'
import { useAuth } from './context/AuthContext.jsx'

const ROUTES = ['/', '/login', '/register', '/deploy', '/sites', '/projects', '/pricing', '/verify-email', '/forgot-password', '/reset-password', '/integrations/github']

function validRoute(path) {
  return ROUTES.includes(path) || /^\/(?:sites|projects)\/[^/]+$/.test(path)
}

function projectFromPath(path) {
  try { return decodeURIComponent(path.split('/')[2] || '') } catch { return '' }
}
const THEME_KEY = 'autokom-theme'

function locationRoute() {
  return validRoute(window.location.pathname) ? window.location.pathname : '/'
}

function initialTheme() {
  try {
    const saved = localStorage.getItem(THEME_KEY) || localStorage.getItem('autokom-landing-theme')
    if (saved === 'light' || saved === 'dark') return saved
  } catch (_) { /* Navigateur sans stockage persistant. */ }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export default function App() {
  const [route, setRoute] = useState(locationRoute)
  const { status } = useAuth()
  const [theme, setTheme] = useState(initialTheme)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    document.documentElement.style.colorScheme = theme
    try { localStorage.setItem(THEME_KEY, theme) } catch (_) { /* Sans stockage : le thème reste actif. */ }
  }, [theme])

  useEffect(() => {
    const onPopState = () => setRoute(locationRoute())
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  function navigate(path) {
    if (!validRoute(path)) return
    if (window.location.pathname !== path) window.history.pushState(null, '', path)
    setRoute(path)
    window.scrollTo({ top: 0, behavior: 'instant' })
  }

  useEffect(() => {
    if (API_AVAILABLE && status === 'anonymous' && /^\/(?:deploy|projects|sites)(?:\/|$)/.test(route)) {
      navigate('/login')
    }
  }, [route, status])

  useEffect(() => {
    if (route !== '/integrations/github' || status === 'loading') return
    const result = new URLSearchParams(window.location.search).get('status')
    if (['connected', 'denied', 'error'].includes(result)) {
      try { sessionStorage.setItem('autokom.githubConnectionNotice', result) } catch { /* navigation possible sans stockage */ }
    }
    if (status === 'authenticated') navigate('/deploy')
    else navigate('/login')
  }, [route, status])

  const toggleTheme = () => setTheme(current => current === 'dark' ? 'light' : 'dark')
  const commonProps = { navigate, theme, onToggleTheme: toggleTheme }

  if (API_AVAILABLE && /^\/(?:deploy|projects|sites)(?:\/|$)/.test(route) && status !== 'authenticated') {
    return <div className="app-shell"><main className="container workspace" role="status">Vérification de votre session…</main></div>
  }

  if (route === '/') return <Landing {...commonProps} />
  if (route === '/login') return <Login {...commonProps} />
  if (route === '/integrations/github') return <div className="app-shell"><main className="container workspace" role="status">Vérification de la connexion GitHub…</main></div>
  if (route === '/register') return <Register {...commonProps} />
  if (route === '/verify-email') return <VerifyEmail {...commonProps} />
  if (route === '/forgot-password') return <ForgotPassword {...commonProps} />
  if (route === '/reset-password') return <ResetPassword {...commonProps} />

  return (
    <div className="app-shell">
      <Header route={route} {...commonProps} />
      <main className="container workspace" id="main-content">
        {route === '/pricing' ? <Pricing navigate={navigate} /> : route.startsWith('/sites') ? <Sites navigate={navigate} projectId={route === '/sites' ? null : projectFromPath(route)} /> : route.startsWith('/projects') ? <Projects navigate={navigate} projectId={route === '/projects' ? null : projectFromPath(route)} /> : <Deploy navigate={navigate} />}
      </main>
      <footer className="container footer">
        <span>AUTOKOM · Déploiement et gestion des applications</span>
      </footer>
    </div>
  )
}
