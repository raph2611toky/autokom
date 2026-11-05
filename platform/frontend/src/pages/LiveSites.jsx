import { useEffect, useMemo, useState } from 'react'
import { ArrowLeft, ArrowRight, GitBranch, Globe2, LoaderCircle, Rocket, Search } from 'lucide-react'
import { projectsApi } from '../services/projectsApi.js'
import { errorText, getRemoteProjectDetail, listRemoteProjectRecords } from '../services/projectWorkflow.js'

/** Le mode connecté ne doit pas afficher les domaines/pipelines simulés. */
export default function LiveSites({ navigate, projectId = null }) {
  const [projects, setProjects] = useState([])
  const [detail, setDetail] = useState(null)
  const [domains, setDomains] = useState([])
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    setBusy(true); setError(''); setDomains([])
    const query = projectId ? getRemoteProjectDetail(projectId) : listRemoteProjectRecords()
    query.then(async data => {
      if (!active) return
      if (!projectId) { setProjects(data); return }
      setDetail(data)
      const result = await projectsApi.domains(data.project.id, data.environment.id)
      if (active) setDomains(result?.domains || [])
    }).catch(err => { if (active) setError(errorText(err)) }).finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [projectId])
  const visible = useMemo(() => projects.filter(project => `${project.name} ${project.repository}`.toLowerCase().includes(query.toLowerCase().trim())), [projects,query])
  if (projectId) return <>
    <div className="ak-back-line"><button className="button-quiet" type="button" onClick={() => navigate('/sites')}><ArrowLeft size={17}/> Tous les sites</button></div>
    <section className="ak-workspace-sheet ak-site-detail">
      <div className="ak-workspace-sheet-heading"><h2>{detail?.project?.name || 'Domaine du projet'}</h2><span className="ak-project-state">Source Django</span></div>
      {busy && <p role="status"><LoaderCircle className="ak-spin" size={17}/> Chargement des domaines…</p>}
      {error && <p className="feedback error" role="alert">{error}</p>}
      {!busy && !error && (domains.length ? <dl className="ak-site-info-grid ak-workspace-details">{domains.map(domain => <div key={domain.id}>
        <dt>{domain.hostname}</dt><dd>DNS : {domain.status} · HTTPS : {domain.tls_status || 'Non configuré'}</dd>
      </div>)}</dl> : <p className="gh-muted">Aucun domaine enregistré sur cet environnement.</p>)}
      {detail && <button className="button-outline" onClick={() => navigate(`/projects/${detail.project.id}`)} type="button">Voir les déploiements et logs <ArrowRight size={16}/></button>}
    </section>
  </>
  return <>
    <section className="page-hero ak-sites-hero"><div><h1>Sites &amp; domaines</h1></div><button className="button-primary" onClick={() => navigate('/deploy')} type="button"><Rocket size={17}/> Nouveau projet</button></section>
    <section className="ak-workspace-sheet ak-sites-simple-list">
      <div className="ak-workspace-toolbar"><div className="search-field"><Search size={17}/><input value={query} onChange={event => setQuery(event.target.value)} placeholder="Rechercher un projet" aria-label="Rechercher un site ou projet"/></div></div>
      {error && <p role="alert" className="feedback error">{error}</p>}
      {busy ? <div className="ak-workspace-empty" role="status"><LoaderCircle className="ak-spin" size={19}/> Chargement des domaines AUTOKOM…</div> : visible.length ? <div className="ak-project-list">{visible.map(project => <button className="ak-site-list-row" type="button" onClick={() => navigate(`/sites/${project.id}`)} key={project.id}>
        <span className="ak-project-row-symbol"><Globe2 size={20}/></span><span className="ak-project-row-main"><strong>{project.name}</strong><small><GitBranch size={13}/> {project.repository} · {project.branch}</small></span><span className="ak-project-state">{project.status === 'ARCHIVED' ? 'Archivé' : 'Voir les domaines'}</span><ArrowRight className="ak-site-list-chevron" size={19}/>
      </button>)}</div> : <div className="ak-workspace-empty"><Globe2 size={29}/><h2>Aucun projet</h2><p>Les domaines réels apparaissent ici avec leurs statuts DNS et TLS.</p></div>}
    </section>
  </>
}
