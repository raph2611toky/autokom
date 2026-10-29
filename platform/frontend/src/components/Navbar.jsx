import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Bell, ChevronDown, Command, Menu, Search } from 'lucide-react'

const routes = [
  { name: 'Tableau de bord', path: '/dashboard' },
  { name: 'Infrastructure', path: '/infrastructure' },
  { name: 'Installation', path: '/installation' },
  { name: 'Pipelines', path: '/pipelines' },
  { name: 'Déploiements', path: '/deployments' },
  { name: 'Observabilité', path: '/observability' },
  { name: 'Logs', path: '/logs' },
  { name: 'Cyber-résilience', path: '/cyber-resilience' },
  { name: 'Paramètres', path: '/settings' },
]

export default function Navbar({ onOpenSidebar }) {
  const [query, setQuery] = useState('')
  const [showResults, setShowResults] = useState(false)
  const navigate = useNavigate()
  const searchRef = useRef(null)
  useEffect(() => {
    const onShortcut = (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        searchRef.current?.focus()
        setShowResults(true)
      }
    }
    window.addEventListener('keydown', onShortcut)
    return () => window.removeEventListener('keydown', onShortcut)
  }, [])
  const matches = query.trim() ? routes.filter((r) => r.name.toLocaleLowerCase('fr').includes(query.toLocaleLowerCase('fr'))).slice(0, 5) : []

  function go(path) {
    navigate(path)
    setQuery('')
    setShowResults(false)
  }

  return (
    <header className="topbar">
      <button className="icon-button menu-toggle" onClick={onOpenSidebar} type="button" aria-label="Ouvrir le menu"><Menu size={22} /></button>
      <div className="topbar-search">
        <Search size={18} aria-hidden="true" />
        <input ref={searchRef} aria-label="Rechercher une page" placeholder="Rechercher une page…" value={query} onChange={(e) => { setQuery(e.target.value); setShowResults(true) }} onFocus={() => setShowResults(true)} onKeyDown={(e) => { if (e.key === 'Enter' && matches.length) go(matches[0].path); if (e.key === 'Escape') setShowResults(false) }} />
        <span className="search-shortcut"><Command size={12} /> K</span>
        {showResults && matches.length > 0 && <div className="search-results" role="listbox" aria-label="Résultats">
          {matches.map((r) => <button type="button" key={r.path} onMouseDown={(e) => e.preventDefault()} onClick={() => go(r.path)}>{r.name}</button>)}
        </div>}
      </div>
      <div className="topbar-right">
        <span className="demo-label"><span className="status-dot status-dot--amber"/> Mode interface</span>
        <div className="topbar-divider"/>
        <span className="icon-button notification-icon" aria-label="Notifications non connectées" title="Notifications disponibles après connexion du backend"><Bell size={19}/></span>
        <div className="profile"><div className="avatar">A</div><span>Admin<small>Administrateur</small></span><ChevronDown size={16}/></div>
      </div>
    </header>
  )
}
