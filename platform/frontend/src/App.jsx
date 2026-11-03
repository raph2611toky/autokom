import { useEffect, useState } from 'react'
import Header from './components/Header.jsx'
import Landing from './pages/Landing.jsx'
import Login from './pages/Login.jsx'
import Register from './pages/Register.jsx'
import Deploy from './pages/Deploy.jsx'
import Sites from './pages/Sites.jsx'
import Projects from './pages/Projects.jsx'
import Pricing from './pages/Pricing.jsx'

const ROUTES = ['/', '/login', '/register', '/deploy', '/sites', '/projects', '/pricing']

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

  const toggleTheme = () => setTheme(current => current === 'dark' ? 'light' : 'dark')
  const commonProps = { navigate, theme, onToggleTheme: toggleTheme }

  if (route === '/') return <Landing {...commonProps} />
  if (route === '/login') return <Login {...commonProps} />
  if (route === '/register') return <Register {...commonProps} />

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
