import { Globe2, LogOut, Rocket } from 'lucide-react'

export default function Header({ route, navigate }) {
  return (
    <header className="site-header">
      <div className="container nav-inner">
        <div className="brand-wrap">
          <img className="brand-logo" src="/autokom-logo.png" alt="" />
          <span className="brand-name">AUTOKOM</span>
        </div>
        <nav className="top-tabs" aria-label="Navigation principale">
          <button type="button" className={`tab-button ${route === '/deploy' ? 'active' : ''}`} aria-current={route === '/deploy' ? 'page' : undefined} onClick={() => navigate('/deploy')}>
            <Rocket size={16} /> Configurer un projet
          </button>
          <button type="button" className={`tab-button ${route === '/sites' ? 'active' : ''}`} aria-current={route === '/sites' ? 'page' : undefined} onClick={() => navigate('/sites')}>
            <Globe2 size={16} /> Sites & domaines
          </button>
        </nav>
        <button type="button" className="icon-button" title="Retour à la connexion" aria-label="Retour à la connexion" onClick={() => navigate('/login')}><LogOut size={17} /></button>
      </div>
      <div className="header-hairline" aria-hidden="true" />
    </header>
  )
}
