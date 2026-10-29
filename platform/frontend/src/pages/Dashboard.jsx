import { ArrowRight, Boxes, ChartNoAxesCombined, CircleAlert, GitBranch, Layers, PlugZap, Rocket, ShieldCheck } from 'lucide-react'
import { Link } from 'react-router-dom'

export default function Dashboard() {
  return (
    <>
      <div className="page-heading">
        <div><p className="eyebrow">VUE D'ENSEMBLE</p><h1>Tableau de bord</h1><p className="page-subtitle">Pilotez votre plateforme DevSecOps depuis un seul espace.</p></div>
        <Link to="/deployments" className="action-button action-button--solid"><Rocket size={17}/> Nouveau déploiement <ArrowRight size={16}/></Link>
      </div>
      <div className="stats-grid">
        <div className="panel metric-card"><div className="metric-icon"><Boxes size={22}/></div><div><span className="muted">Services opérationnels</span><strong>—</strong><span className="detail-muted">En attente de découverte</span></div></div>
        <div className="panel metric-card"><div className="metric-icon"><GitBranch size={22}/></div><div><span className="muted">Pipelines exécutés</span><strong>—</strong><span className="detail-muted">Jenkins non synchronisé</span></div></div>
        <div className="panel metric-card"><div className="metric-icon"><CircleAlert size={22}/></div><div><span className="muted">Alertes actives</span><strong>—</strong><span className="detail-muted">Source non connectée</span></div></div>
      </div>
      <div className="dashboard-columns">
        <section className="panel panel-space">
          <div className="section-heading"><div><h2>Composants de l'infrastructure</h2><p>Les services connus de votre environnement AUTOKOM.</p></div><Link to="/infrastructure" className="text-link">Explorer <ArrowRight size={16}/></Link></div>
          <div className="component-grid">
            <div className="service-tile"><span className="service-mark docker">D</span><div><strong>Docker</strong><small>Conteneurisation</small></div><span className="tag tag--muted">À vérifier</span></div>
            <div className="service-tile"><span className="service-mark jenkins">J</span><div><strong>Jenkins</strong><small>CI/CD</small></div><span className="tag tag--muted">À vérifier</span></div>
            <div className="service-tile"><span className="service-mark nginx">N</span><div><strong>Nginx</strong><small>Reverse proxy</small></div><span className="tag tag--muted">À vérifier</span></div>
            <div className="service-tile"><span className="service-mark prom">P</span><div><strong>Prometheus</strong><small>Métriques</small></div><span className="tag tag--muted">À vérifier</span></div>
            <div className="service-tile"><span className="service-mark grafana">G</span><div><strong>Grafana</strong><small>Visualisation</small></div><span className="tag tag--muted">À vérifier</span></div>
            <div className="service-tile"><span className="service-mark github">GH</span><div><strong>GitHub</strong><small>Code & intégration</small></div><span className="tag tag--amber">À connecter</span></div>
          </div>
          <div className="inline-note"><PlugZap size={17}/><span>L'inventaire réel, les versions et les états seront fournis par l'API de la CLI AUTOKOM.</span></div>
        </section>
        <section className="panel panel-space welcome-panel">
          <p className="eyebrow">POUR COMMENCER</p><h2>Une plateforme.<br/>Tous vos outils.</h2>
          <p>Configurez l'infrastructure, connectez vos dépôts GitHub puis activez vos premiers pipelines sécurisés.</p>
          <div className="onboarding-steps">
            <div><span>01</span><div><strong>Découvrir l'infrastructure</strong><small>Identifier les services et leurs configurations</small></div></div>
            <div><span>02</span><div><strong>Connecter GitHub</strong><small>Préparer l'accès aux dépôts autorisés</small></div></div>
            <div><span>03</span><div><strong>Configurer le CI/CD</strong><small>Définir le build et les politiques DevSecOps</small></div></div>
          </div>
          <Link className="action-button action-button--solid full-width" to="/infrastructure">Explorer mon infrastructure <ArrowRight size={17}/></Link>
        </section>
      </div>
      <div className="dashboard-bottom">
        <section className="panel panel-space"><div className="section-heading"><div><h2>Activité CI/CD</h2><p>Exécutions et résultats sur vos projets.</p></div><GitBranch size={18}/></div><div className="empty-chart"><div className="chart-grid"/><div className="empty-chart-content"><Layers size={25}/><strong>Aucune activité récupérée</strong><span>Connectez Jenkins pour afficher les résultats des pipelines.</span></div></div></section>
        <section className="panel panel-space"><div className="section-heading"><div><h2>Observabilité</h2><p>Un aperçu léger, Grafana pour le détail.</p></div><ChartNoAxesCombined size={18}/></div><div className="empty-chart small"><div className="chart-grid"/><div className="empty-chart-content"><ShieldCheck size={25}/><strong>Métriques indisponibles</strong><span>La synchronisation Prometheus sera activée avec l'API.</span></div></div><Link className="text-link bottom-link" to="/observability">Accéder à l'observabilité <ArrowRight size={16}/></Link></section>
      </div>
    </>
  )
}
