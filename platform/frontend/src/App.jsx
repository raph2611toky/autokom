import { useEffect, useState } from 'react'
import Header from './components/Header.jsx'
import Login from './pages/Login.jsx'
import Deploy from './pages/Deploy.jsx'
import Sites from './pages/Sites.jsx'

function locationRoute() {
  return ['/deploy', '/sites'].includes(window.location.pathname)
    ? window.location.pathname
    : '/login'
}

export default function App() {
  const [route, setRoute] = useState(locationRoute)

  useEffect(() => {
    const onPopState = () => setRoute(locationRoute())
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  function navigate(path) {
    if (!['/login', '/deploy', '/sites'].includes(path)) return
    window.history.pushState(null, '', path)
    setRoute(path)
    window.scrollTo(0, 0)
  }

  // Frontend seul : la navigation ne constitue PAS une authentification.
  // Le contrôle d'accès réel sera implémenté plus tard côté serveur.
  if (route === '/login') return <Login />

  return (
    <div className="app-shell">
      <Header route={route} navigate={navigate} />
      <main className="container workspace" id="main-content">
        {route === '/sites' ? <Sites /> : <Deploy />}
      </main>
      <footer className="container footer">
        <span>AUTOKOM / Configuration des déploiements</span>
        <span>Frontend uniquement</span>
      </footer>
    </div>
  )
}
