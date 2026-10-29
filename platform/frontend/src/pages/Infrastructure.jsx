import { ArrowRight, Boxes, CircleHelp, CloudCog, Database, Network, RefreshCw, Server, ShieldCheck } from 'lucide-react'
import { useOutletContext } from 'react-router-dom'

const groups = [
  { title: 'Exécution & réseau', caption: 'Conteneurs et exposition des services.', items: [{name:'Docker',mark:'D',type:'docker',role:'Conteneurisation'}, {name:'Nginx',mark:'N',type:'nginx',role:'Reverse proxy'}] },
  { title: 'Automatisation', caption: 'Intégration et livraison continues.', items: [{name:'Jenkins',mark:'J',type:'jenkins',role:'CI/CD'}] },
  { title: 'Observabilité & sécurité', caption: 'Métriques, logs et détection.', items: [{name:'Prometheus',mark:'P',type:'prom',role:'Collecte de métriques'}, {name:'Grafana',mark:'G',type:'grafana',role:'Dashboards & alertes'}, {name:'Loki + Alloy',mark:'L',type:'loki',role:'Collecte de logs'}, {name:'OpenSearch',mark:'OS',type:'opensearch',role:'Analyse des logs'}, {name:'Wazuh',mark:'W',type:'wazuh',role:'Détection des menaces'}] },
]

export default function Infrastructure() {
  const { notify } = useOutletContext()
  return (
    <>
      <div className="page-heading"><div><p className="eyebrow">INFRASTRUCTURE</p><h1>Infrastructure</h1><p className="page-subtitle">Découvrir, gérer et vérifier les composants de votre environnement.</p></div><button className="action-button action-button--solid" type="button" onClick={() => notify('La découverte des services sera disponible après connexion de l’API AUTOKOM.')}><RefreshCw size={17}/> Lancer la découverte</button></div>
      <div className="content-two-columns infrastructure-layout">
        <div className="stacked-sections">
          {groups.map((group) => <section className="panel panel-space" key={group.title}><div className="section-heading"><div><h2>{group.title}</h2><p>{group.caption}</p></div><span className="mini-count">{group.items.length} composant{group.items.length > 1 ? 's' : ''}</span></div>
            <div className="infra-service-grid">{group.items.map((s) => <div className="service-card" key={s.name}><div className={`service-mark ${s.type}`}>{s.mark}</div><div className="service-card-text"><strong>{s.name}</strong><small>{s.role}</small><span className="tag tag--muted">État non vérifié</span></div><button type="button" className="small-outline-button" onClick={() => notify(`Gestion de ${s.name} : à connecter à l’API AUTOKOM.`)}>Gérer <ArrowRight size={15}/></button></div>)}</div>
          </section>)}
        </div>
        <aside className="stacked-sections">
          <section className="panel panel-space panel-accent"><div className="section-heading"><div><p className="eyebrow">DÉCOUVERTE RÉELLE</p><h2>Topologie de l'infrastructure</h2></div><Network size={25}/></div><p className="subdued-paragraph">Aucune relation entre services n'est supposée. La cartographie sera calculée à partir des données réelles de l'inventaire et du réseau.</p><div className="topology-illustration"><span><Server size={24}/></span><i/><span><Boxes size={24}/></span><i/><span><Database size={24}/></span></div><div className="inline-note"><CircleHelp size={17}/> Découverte non encore synchronisée.</div><button type="button" className="action-button action-button--solid full-width" onClick={() => notify('Le backend devra exposer une route de découverte de la topologie.')}>Découvrir les services <ArrowRight size={16}/></button></section>
          <section className="panel panel-space"><div className="section-heading"><div><h2>Bonnes pratiques</h2><p>État actuel du frontend.</p></div><ShieldCheck size={19}/></div><div className="info-list"><div><CloudCog size={19}/><span>Installation idempotente depuis la CLI</span></div><div><RefreshCw size={19}/><span>Vérification avant toute modification</span></div><div><ShieldCheck size={19}/><span>Privilèges et secrets gérés côté serveur</span></div></div></section>
        </aside>
      </div>
    </>
  )
}
