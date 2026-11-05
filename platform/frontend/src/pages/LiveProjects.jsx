import { useEffect, useMemo, useState } from 'react'
import { ArrowLeft, ArrowRight, LoaderCircle, FolderKanban, GitBranch, Plus, Search, Settings2, Trash2, Workflow } from 'lucide-react'
import { projectsApi } from '../services/projectsApi.js'
import { deploymentsApi } from '../services/deploymentsApi.js'
import { errorText, listRemoteProjectRecords, getRemoteProjectDetail } from '../services/projectWorkflow.js'
import { publicationLabel, phaseTitles, phaseOrder, cleanCustomerLog } from '../services/deploymentPresentation.js'

function formatDate(value) {
  if (!value) return 'Non renseignée'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? 'Non renseignée' : new Intl.DateTimeFormat('fr-FR', { dateStyle: 'medium' }).format(date)
}

/** Même composants visuels que Projects.jsx, sources exclusivement AUTOKOM. */
export default function LiveProjects({ navigate, projectId = null }) {
  const [projects, setProjects] = useState([])
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const [busy, setBusy] = useState(false)
  const [logs, setLogs] = useState({})
  const [openedRun, setOpenedRun] = useState(null)

  useEffect(() => {
    let alive = true
    setLoading(true); setError(''); setDetail(null)
    const request = projectId ? getRemoteProjectDetail(projectId) : listRemoteProjectRecords()
    request.then(result => {
      if (!alive) return
      if (projectId) setDetail(result)
      else setProjects(result)
    }).catch(err => { if (alive) setError(errorText(err)) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [projectId])

  useEffect(() => {
    if (!projectId || !detail?.runs?.some(r => ['QUEUED', 'RUNNING'].includes(r.status))) return
    let alive = true
    const timer = window.setInterval(() => {
      getRemoteProjectDetail(projectId).then(next => { if (alive) setDetail(next) }).catch(() => { /* le chargement manuel garde l'erreur visible */ })
    }, 15000)
    return () => { alive = false; window.clearInterval(timer) }
  }, [projectId, detail?.runs?.map(r => r.status).join('|')])

  const visible = useMemo(() => projects.filter(p => {
    const status = (p.status || '').toUpperCase()
    const ok = filter === 'all' || (filter === 'active' ? status === 'ACTIVE' : status === 'ARCHIVED')
    return ok && `${p.name} ${p.repository} ${p.branch}`.toLowerCase().includes(query.toLowerCase().trim())
  }), [projects, query, filter])

  function editProject(p) {
    try { sessionStorage.setItem('autokom.ui.editRemoteProject', p.id) } catch { /* navigation sans stockage */ }
    navigate('/deploy')
  }

  async function archiveProject(project) {
    if (busy || !window.confirm(`Archiver « ${project.name} » dans AUTOKOM ? Le projet ne sera pas effacé.`)) return
    setBusy(true); setError('')
    try {
      await projectsApi.archive(project.id)
      if (projectId) navigate('/projects')
      else setProjects(current => current.map(p => p.id === project.id ? { ...p, status: 'ARCHIVED' } : p))
    } catch (err) { setError(errorText(err)) }
    finally { setBusy(false) }
  }

  async function loadLogs(run) {
    if (!detail || busy) return
    if (openedRun === run.id) { setOpenedRun(null); return }
    setOpenedRun(run.id); setBusy(true); setError('')
    try {
      const result = await deploymentsApi.logs(detail.project.id, detail.environment.id, run.id)
      setLogs(current => ({ ...current, [run.id]: result.logs || [] }))
    } catch (err) { setError(errorText(err)) }
    finally { setBusy(false) }
  }

  useEffect(() => {
    const run = detail?.runs?.find(item => item.id === openedRun)
    if (!detail || !run || !['QUEUED', 'RUNNING'].includes(run.status)) return
    let alive = true
    const timer = window.setInterval(() => {
      deploymentsApi.logs(detail.project.id, detail.environment.id, run.id)
        .then(result => { if (alive) setLogs(current => ({ ...current, [run.id]: result.logs || [] })) })
        .catch(() => { /* Les erreurs réseau n'effacent jamais les journaux déjà affichés. */ })
    }, 12000)
    return () => { alive = false; window.clearInterval(timer) }
  }, [detail?.project?.id, detail?.environment?.id, detail?.runs?.map(run => `${run.id}:${run.status}`).join('|'), openedRun])

  if (projectId) {
    if (loading) return <section className="ak-workspace-empty" role="status"><LoaderCircle size={19} className="ak-spin"/> Chargement du projet AUTOKOM…</section>
    if (error && !detail) return <section className="ak-workspace-empty"><p className="feedback error" role="alert">{error}</p><button className="button-outline" onClick={() => navigate('/projects')}>Mes projets</button></section>
    if (!detail) return <section className="ak-workspace-empty"><h1>Projet introuvable</h1></section>
    const { project, environment, configurations, runs } = detail
    const cfg = configurations[0]
    return <>
      <div className="ak-back-line"><button type="button" className="button-quiet" onClick={() => navigate('/projects')}><ArrowLeft size={17}/> Mes projets</button></div>
      <section className="page-hero ak-workspace-hero"><div><h1>{project.name}</h1><p>{project.repository_name} · {cfg?.branch || project.default_branch}</p></div>
        <button className="button-primary" type="button" onClick={() => editProject(project)} disabled={project.status === 'ARCHIVED'}><Settings2 size={17}/> Modifier</button></section>
      {error && <p className="feedback error" role="alert">{error}</p>}
      <section className="ak-workspace-sheet">
        <div className="ak-workspace-sheet-heading"><h2>Configuration du projet</h2><span className="ak-project-state">{cfg?.is_draft ? 'Brouillon' : cfg ? 'Configuration finalisée' : 'Sans configuration'}</span></div>
        <dl className="ak-site-info-grid ak-workspace-details">
          <div><dt>Dépôt GitHub</dt><dd>{project.repository_name}</dd></div>
          <div><dt>Branche</dt><dd>{cfg?.branch || project.default_branch}</dd></div>
          <div><dt>Répertoire source</dt><dd>{cfg?.root_directory || 'Racine'}</dd></div>
          <div><dt>Environnement</dt><dd>{environment.name}</dd></div>
          <div><dt>Publication</dt><dd>{cfg?.mode === 'DEVSECOPS' ? 'Contrôles renforcés' : cfg ? 'Standard' : 'Non configurée'}</dd></div>
          <div><dt>Statut</dt><dd>{project.status}</dd></div>
          <div><dt>Dernière modification</dt><dd>{formatDate(project.updated_at)}</dd></div>
          <div><dt>Publication</dt><dd><span className={`ak-publication-pill state-${publicationLabel(runs[0]?.status)[1]}`}>{runs.length ? publicationLabel(runs[0]?.status)[0] : 'Aucune publication'}</span></dd></div>
        </dl>
        <div className="ak-project-detail-actions">
          <button className="button-outline" type="button" onClick={() => editProject(project)} disabled={busy || project.status === 'ARCHIVED'}><Settings2 size={16}/> Modifier la configuration</button>
          {project.status !== 'ARCHIVED' && <button className="button-quiet ak-danger-action" type="button" disabled={busy} onClick={() => archiveProject(project)}><Trash2 size={16}/> Archiver</button>}
        </div>
      </section>
      <section className="ak-workspace-sheet">
        <div className="ak-workspace-sheet-heading"><h2>Historique des publications</h2><span className="ak-project-state">{runs.length} exécution(s)</span></div>
        {runs.length === 0 && <p className="gh-muted">Aucune publication pour le moment.</p>}
        <div className="ak-deployment-list">{runs.map(run => {
          const [name, tone] = publicationLabel(run.status)
          const expanded = openedRun === run.id
          const steps = [...(run.stages || [])].sort((a,b) => phaseOrder.indexOf(a.stage_key) - phaseOrder.indexOf(b.stage_key))
          const entries = (logs[run.id] || []).map(row => cleanCustomerLog(row.message)).filter(Boolean)
          return <article key={run.id} className="ak-deployment-card">
            <div className="ak-deployment-summary">
              <div className="ak-deployment-symbol"><Workflow size={19}/></div>
              <div className="ak-deployment-summary-text"><strong>Publication du {formatDate(run.created_at)}</strong><small>Référence {String(run.id).slice(0,8)}</small></div>
              <span className={`ak-publication-pill state-${tone}`} role="status">{name}</span>
              <button type="button" className="button-outline ak-deployment-toggle" disabled={busy} onClick={() => loadLogs(run)} aria-expanded={expanded}>{expanded ? 'Masquer les détails' : 'Voir la progression'}</button>
            </div>
            {expanded && <div className="ak-deployment-details">
              <h3>Progression de la mise en ligne</h3>
              <ol className="ak-publication-timeline">{(steps.length ? steps : [{stage_key:'source',status:'PENDING'}]).map(stage => {
                const [label, stateTone] = publicationLabel(stage.status)
                return <li key={stage.stage_key} className={`ak-publication-step state-${stateTone}`}>
                  <span className="ak-publication-dot" aria-hidden="true"/>
                  <span className="ak-publication-step-name">{phaseTitles[stage.stage_key] || 'Vérification'}</span>
                  <span className="ak-publication-step-state">{label}</span>
                </li>
              })}</ol>
              {['QUEUED','RUNNING'].includes(run.status) && <p className="ak-deployment-hint">Mise à jour automatique de la progression.</p>}
              {run.error_message && <p className="ak-deployment-error" role="alert">{cleanCustomerLog(run.error_message) || "Cette publication nécessite une intervention. Votre précédente version reste inchangée."}</p>}
              {!!entries.length && <details className="ak-deployment-messages"><summary>Historique des opérations</summary><ol>{entries.map((entry,index) => <li key={index}>{entry}</li>)}</ol></details>}
              {!entries.length && <p className="ak-deployment-hint">Les informations détaillées apparaîtront dès le démarrage de la préparation.</p>}
            </div>}
          </article>
        })}</div>
      </section>
    </>
  }

  return <>
    <section className="page-hero ak-workspace-hero"><div><h1>Mes projets</h1><p>Projets enregistrés sur votre compte AUTOKOM.</p></div><button className="button-primary" type="button" onClick={() => navigate('/deploy')}><Plus size={17}/> Nouveau projet</button></section>
    {error && <p className="feedback error" role="alert">{error}</p>}
    <section className="ak-workspace-sheet">
      <div className="ak-workspace-toolbar">
        <div className="search-field"><Search size={17}/><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Rechercher un projet" aria-label="Rechercher un projet"/></div>
        <div className="ak-workspace-filters" aria-label="Filtrer les projets">{[['all','Tous'], ['active','Actifs'], ['archived','Archivés']].map(([value,label]) => <button key={value} type="button" className={filter === value ? 'active' : ''} aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}</button>)}</div>
      </div>
      {loading ? <div className="ak-workspace-empty" role="status"><LoaderCircle size={19} className="ak-spin"/> Chargement des projets…</div> : visible.length ? <div className="ak-project-list">{visible.map(project => <div className="ak-project-row" key={project.id}>
        <div className="ak-project-row-symbol"><FolderKanban size={21}/></div>
        <button type="button" className="ak-project-row-main" onClick={() => navigate(`/projects/${project.id}`)}><strong>{project.name}</strong><small><GitBranch size={13}/>{project.repository} · {project.branch}</small></button>
        <span className="ak-project-state">{project.status === 'ARCHIVED' ? 'Archivé' : 'Actif'}</span>
        <button type="button" className="ak-project-open" onClick={() => navigate(`/projects/${project.id}`)} aria-label={`Ouvrir ${project.name}`}><ArrowRight size={19}/></button>
      </div>)}</div> : <div className="ak-workspace-empty"><FolderKanban size={29}/><h2>{error ? 'Chargement impossible' : projects.length ? 'Aucun résultat' : 'Aucun projet pour le moment'}</h2><p>{error ? 'Vérifiez votre session et l’API.' : projects.length ? 'Essayez une autre recherche ou un autre filtre.' : 'Créez votre premier projet pour le retrouver ici.'}</p>{!projects.length && !error && <button className="button-primary" type="button" onClick={() => navigate('/deploy')}>Créer un projet <ArrowRight size={16}/></button>}</div>}
    </section>
  </>
}
