import { useEffect, useMemo, useState } from 'react'
import { ArrowLeft, ArrowRight, LoaderCircle, FolderKanban, GitBranch, Plus, Search, Settings2, Trash2, Workflow } from 'lucide-react'
import { projectsApi } from '../services/projectsApi.js'
import { deploymentsApi } from '../services/deploymentsApi.js'
import { errorText, listRemoteProjectRecords, getRemoteProjectDetail } from '../services/projectWorkflow.js'

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
          <div><dt>Mode du pipeline</dt><dd>{cfg?.mode || 'Non configuré'}</dd></div>
          <div><dt>Statut</dt><dd>{project.status}</dd></div>
          <div><dt>Dernière modification</dt><dd>{formatDate(project.updated_at)}</dd></div>
          <div><dt>Publication</dt><dd>{runs[0]?.status || 'Aucun déploiement réel'}</dd></div>
        </dl>
        <div className="ak-project-detail-actions">
          <button className="button-outline" type="button" onClick={() => editProject(project)} disabled={busy || project.status === 'ARCHIVED'}><Settings2 size={16}/> Modifier la configuration</button>
          {project.status !== 'ARCHIVED' && <button className="button-quiet ak-danger-action" type="button" disabled={busy} onClick={() => archiveProject(project)}><Trash2 size={16}/> Archiver</button>}
        </div>
      </section>
      <section className="ak-workspace-sheet">
        <div className="ak-workspace-sheet-heading"><h2>Historique des publications</h2><span className="ak-project-state">{runs.length} exécution(s)</span></div>
        {runs.length === 0 && <p className="gh-muted">Aucune publication pour le moment.</p>}
        <div className="ak-project-list">{runs.map(run => <div key={run.id} className="ak-project-row" style={{ flexWrap: 'wrap' }}>
          <div className="ak-project-row-symbol"><Workflow size={20}/></div>
          <div className="ak-project-row-main"><strong>Déploiement {String(run.id).slice(0, 8)}</strong><small>{formatDate(run.created_at)} · {run.status}</small></div>
          <button type="button" className="button-outline" disabled={busy} onClick={() => loadLogs(run)}>{openedRun === run.id ? 'Masquer les logs' : 'Suivre la publication'}</button>
          {openedRun === run.id && <div className="ak-run-observability" role="region" aria-label="Progression de la publication">
            <div className="ak-run-stages">{(run.stages || []).map(stage => <div key={stage.stage_key} className="ak-run-stage">
              <span>{{source:'Récupération du projet',sast:'Analyse du code',sca:'Vérification de sécurité',build:'Préparation du site',release:'Mise en ligne',health:'Vérification du site'}[stage.stage_key] || 'Vérification'}</span><strong>{stage.status === 'RUNNING' ? 'En cours' : stage.status === 'SUCCESS' ? 'Terminé' : stage.status === 'FAILED' ? 'Échec' : stage.status === 'SKIPPED' ? 'Ignoré' : 'En attente'}</strong>
            </div>)}</div>

            {['QUEUED','RUNNING'].includes(run.status) && <small className="gh-muted">Statut actualisé automatiquement toutes les 15 s, journaux toutes les 12 s.</small>}
            {run.error_message && <p className="feedback error" role="alert">{run.error_message}</p>}
          </div>}
        </div>)}</div>
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
