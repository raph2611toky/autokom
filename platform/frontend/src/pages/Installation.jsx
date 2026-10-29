import { useState } from 'react'
import { ArrowRight, Check, CircleHelp, Download, PackageCheck, SlidersHorizontal } from 'lucide-react'
import { useOutletContext } from 'react-router-dom'

const modules = [
  {id:'docker', name:'Docker', description:'Moteur de conteneurs', category:'Exécution'},
  {id:'nginx', name:'Nginx', description:'Reverse proxy HTTPS', category:'Réseau'},
  {id:'jenkins', name:'Jenkins', description:'Orchestration CI/CD', category:'Automatisation'},
  {id:'prometheus', name:'Prometheus', description:'Collecte de métriques', category:'Observabilité'},
  {id:'grafana', name:'Grafana', description:'Dashboards & alertes', category:'Observabilité'},
  {id:'loki', name:'Loki + Alloy', description:'Logs et collecte', category:'Logs'},
  {id:'opensearch', name:'OpenSearch', description:'Recherche et analyse', category:'Logs'},
  {id:'wazuh', name:'Wazuh', description:'Supervision sécurité', category:'Sécurité'},
]

export default function Installation() {
  const [selected, setSelected] = useState([])
  const [category, setCategory] = useState('Tous')
  const { notify } = useOutletContext()
  const visible = category === 'Tous' ? modules : modules.filter(m => m.category === category)
  const toggle = (id) => setSelected((old) => old.includes(id) ? old.filter(x => x !== id) : [...old, id])
  return <>
    <div className="page-heading"><div><p className="eyebrow">INSTALLATION & CONFIGURATION</p><h1>Installer vos composants</h1><p className="page-subtitle">Préparez les modules à installer. Aucune opération système n'est lancée par ce frontend.</p></div><div className="heading-icon"><Download size={25}/></div></div>
    <div className="content-two-columns installation-layout">
      <section className="panel panel-space"><div className="section-heading"><div><h2>Catalogue AUTOKOM</h2><p>Sélectionnez uniquement les outils nécessaires.</p></div><div className="filter-select"><SlidersHorizontal size={16}/><select aria-label="Filtrer les composants" value={category} onChange={e => setCategory(e.target.value)}><option>Tous</option><option>Exécution</option><option>Réseau</option><option>Automatisation</option><option>Observabilité</option><option>Logs</option><option>Sécurité</option></select></div></div>
        <div className="module-grid">{visible.map(module => <label key={module.id} className={`module-option ${selected.includes(module.id) ? 'module-option--selected' : ''}`}><input type="checkbox" checked={selected.includes(module.id)} onChange={() => toggle(module.id)}/><span className="module-checkbox"><Check size={15}/></span><span><strong>{module.name}</strong><small>{module.description}</small><em>{module.category}</em></span></label>)}</div>
      </section>
      <aside className="stacked-sections"><section className="panel panel-space panel-accent"><p className="eyebrow">PRÉPARATION</p><h2>Plan d'installation</h2><p className="subdued-paragraph">La sélection reste dans votre navigateur. Le backend validera ultérieurement les dépendances, la configuration et les versions réellement installées.</p><div className="selection-count"><PackageCheck size={29}/><strong>{selected.length}</strong><span>module{selected.length > 1 ? 's' : ''} sélectionné{selected.length > 1 ? 's' : ''}</span></div><div className="selection-tags">{selected.length === 0 ? <span className="muted">Aucun outil sélectionné</span> : selected.map(id => <span key={id} className="tag tag--purple">{modules.find(m => m.id === id)?.name}</span>)}</div><button className="action-button action-button--solid full-width" type="button" disabled={!selected.length} onClick={() => notify('Plan prêt côté interface. L’installation réelle sera connectée à la CLI AUTOKOM via une API sécurisée.')}>Préparer l'installation <ArrowRight size={16}/></button></section><div className="panel panel-space"><div className="section-heading"><h2>Installation sûre</h2><CircleHelp size={19}/></div><p className="subdued-paragraph">L'interface ne doit jamais exécuter une commande arbitraire ni conserver des privilèges administrateur. Les actions sensibles seront contrôlées et auditées par le backend.</p></div></aside>
    </div>
  </>
}
