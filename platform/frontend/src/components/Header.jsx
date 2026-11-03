import { BadgePercent, FolderKanban, Globe2, LogOut, Rocket } from 'lucide-react'
import ThemeToggle from './ThemeToggle.jsx'

const TABS = [
  { path: '/projects', label: 'Mes projets', icon: FolderKanban },
  { path: '/deploy', label: 'Déployer', icon: Rocket },
  { path: '/sites', label: 'Sites & domaines', icon: Globe2 },
  { path: '/pricing', label: 'Tarifs', icon: BadgePercent },
]

export default function Header({ route, navigate, theme, onToggleTheme }) {
  return (
    <header className="site-header">
      <div className="container nav-inner">
        <button type="button" className="brand-wrap ak-brand-button" onClick={() => navigate('/')} aria-label="Retour à l'accueil AUTOKOM">
          <img className="brand-logo" src="/autokom-logo.png" alt="" />
          <span className="brand-name">AUTOKOM</span>
        </button>
        <nav className="top-tabs" aria-label="Navigation principale">
          {TABS.map(({ path, label, icon: Icon }) => {
            const active = route === path || route.startsWith(`${path}/`)
            return <button key={path} type="button" className={`tab-button ${active ? 'active' : ''}`} aria-current={active ? 'page' : undefined} onClick={() => navigate(path)}><Icon size={16}/><span>{label}</span></button>
          })}
        </nav>
        <div className="ak-header-actions">
          <ThemeToggle theme={theme} onToggle={onToggleTheme} />
          <button type="button" className="icon-button" title="Retour à la connexion" aria-label="Retour à la connexion" onClick={() => navigate('/login')}><LogOut size={17} /></button>
        </div>
      </div>
      <div className="header-hairline" aria-hidden="true" />
    </header>
  )
}
