import { NavLink } from 'react-router-dom'
import { Blocks, ChartNoAxesCombined, Cog, Database, GitBranch, House, Layers3, Rocket, ShieldCheck, X } from 'lucide-react'

export default function Sidebar({ open, onClose }) {
  return (
    <>
      {open && <button className="sidebar-backdrop" onClick={onClose} aria-label="Fermer le menu" type="button" />}
      <aside className={`sidebar ${open ? 'sidebar--open' : ''}`} aria-label="Navigation principale">
        <div className="brand-row">
          <NavLink to="/dashboard" onClick={onClose} className="brand" aria-label="AUTOKOM, accueil">
            <img src="/autokom-logo.png" alt="" /> <span>AUTOKOM</span>
          </NavLink>
          <button className="icon-button mobile-close" type="button" onClick={onClose} aria-label="Fermer le menu"><X size={20} /></button>
        </div>
        <div className="sidebar-label">PLATEFORME</div>
        <nav className="nav-links">
          <NavLink to="/dashboard" onClick={onClose} className={({isActive}) => `nav-link ${isActive ? 'nav-link--active' : ''}`}><House size={19} /> Tableau de bord</NavLink>
          <NavLink to="/infrastructure" onClick={onClose} className={({isActive}) => `nav-link ${isActive ? 'nav-link--active' : ''}`}><Database size={19} /> Infrastructure</NavLink>
          <NavLink to="/installation" onClick={onClose} className={({isActive}) => `nav-link ${isActive ? 'nav-link--active' : ''}`}><Layers3 size={19} /> Installation</NavLink>
          <NavLink to="/pipelines" onClick={onClose} className={({isActive}) => `nav-link ${isActive ? 'nav-link--active' : ''}`}><GitBranch size={19} /> Pipelines</NavLink>
          <NavLink to="/deployments" onClick={onClose} className={({isActive}) => `nav-link ${isActive ? 'nav-link--active' : ''}`}><Rocket size={19} /> Déploiements</NavLink>
          <NavLink to="/observability" onClick={onClose} className={({isActive}) => `nav-link ${isActive ? 'nav-link--active' : ''}`}><ChartNoAxesCombined size={19} /> Observabilité</NavLink>
          <NavLink to="/logs" onClick={onClose} className={({isActive}) => `nav-link ${isActive ? 'nav-link--active' : ''}`}><Blocks size={19} /> Logs</NavLink>
          <NavLink to="/cyber-resilience" onClick={onClose} className={({isActive}) => `nav-link ${isActive ? 'nav-link--active' : ''}`}><ShieldCheck size={19} /> Cyber-résilience</NavLink>
        </nav>
        <div className="sidebar-footer">
          <NavLink to="/settings" onClick={onClose} className={({isActive}) => `nav-link ${isActive ? 'nav-link--active' : ''}`}><Cog size={19} /> Paramètres</NavLink>
          <div className="sidebar-signature"><span className="violet-line" />AUTOKOM <small>ORCHESTRER · SÉCURISER · OBSERVER</small></div>
        </div>
      </aside>
    </>
  )
}
