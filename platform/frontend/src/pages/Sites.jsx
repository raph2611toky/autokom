import { useState } from 'react'
import { CheckCircle2, Compass, Globe2, Info, Layers3, Search, ShieldCheck } from 'lucide-react'

export default function Sites() {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')

  return (
    <>
      <section className="page-hero sites-hero">
        <div>
          <span className="eyebrow"><span className="short-stroke"/> SITES & DOMAINES</span>
          <h1>Vos sites, <em>en un regard.</em></h1>
          <p>Un inventaire des applications et domaines présents sur votre infrastructure.</p>
        </div>
        <button type="button" className="button-primary" disabled title="La découverte n'est pas encore reliée à l'infrastructure">
          <Compass size={17}/> Détecter les sites
        </button>
      </section>
      <section className="site-stats" aria-label="Statistiques de l’inventaire">
        <div className="cut-frame stat"><div className="cut-body"><div className="stat-icon"><Globe2 size={21}/></div><div><span>Sites détectés</span><strong>—</strong></div></div></div>
        <div className="cut-frame stat"><div className="cut-body"><div className="stat-icon"><ShieldCheck size={21}/></div><div><span>Gérés par AUTOKOM</span><strong>—</strong></div></div></div>
        <div className="cut-frame stat"><div className="cut-body"><div className="stat-icon"><Layers3 size={21}/></div><div><span>Sites externes</span><strong>—</strong></div></div></div>
        <div className="cut-frame stat"><div className="cut-body"><div className="stat-icon"><CheckCircle2 size={21}/></div><div><span>Vérifiés en ligne</span><strong>—</strong></div></div></div>
      </section>
      <section className="cut-frame inventory-panel">
        <div className="cut-body">
          <div className="inventory-heading">
            <div><span className="eyebrow">INVENTAIRE</span><h2>Sites & domaines</h2><p>Domaines, configurations et origines des services.</p></div>
          </div>
          <div className="inventory-tools">
            <div className="search-field"><Search size={18}/><input type="search" aria-label="Rechercher un site" placeholder="Rechercher un site ou un domaine…" value={query} onChange={(event) => setQuery(event.target.value)}/></div>
            <div className="filter-bar" role="group" aria-label="Filtrer les sites">
              <button type="button" className={filter === 'all' ? 'current' : ''} onClick={() => setFilter('all')}>Tous</button>
              <button type="button" className={filter === 'managed' ? 'current' : ''} onClick={() => setFilter('managed')}>Gérés par AUTOKOM</button>
              <button type="button" className={filter === 'external' ? 'current' : ''} onClick={() => setFilter('external')}>Externes</button>
            </div>
          </div>
          <div className="inventory-empty" role="status">
            <div className="empty-visual"><Globe2 size={38}/><i/><i/></div>
            <h3>Aucun site synchronisé.</h3>
            <p>Les sites apparaîtront ici lorsque la découverte de l’infrastructure sera disponible.</p>
          </div>
        </div>
      </section>
      <div className="sites-bottom-note"><Info size={18}/><p>Aucun site ni état de santé n’est simulé.</p></div>
    </>
  )
}
