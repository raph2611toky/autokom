import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, ChevronDown, Clock3, FolderKanban, GitBranch, LoaderCircle, Plus, Rocket, Search, Settings2, Workflow } from 'lucide-react'
import { projectsApi } from '../services/projectsApi.js'
import { deploymentsApi } from '../services/deploymentsApi.js'
import { errorText, listRemoteProjectRecords, getRemoteProjectDetail } from '../services/projectWorkflow.js'
import { publicationLabel } from '../services/deploymentPresentation.js'
import { publicSiteUrl } from '../services/sitePresentation.js'
import { sortRecentRuns, isPublishing, canPublish } from '../services/deploymentHistory.js'
import ProjectPublicSite from '../components/ProjectPublicSite.jsx'

function formatDate(value) {
  if (!value) return 'Non renseignée'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? 'Non renseignée' : new Intl.DateTimeFormat('fr-FR', {dateStyle:'medium', timeStyle:'short'}).format(date)
}

/** Mes projets réels : données exclusivement issues de Django, aucun lancement simulé. */
export default function LiveProjects({ navigate, projectId = null }) {
  const [projects, setProjects] = useState([])
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const [busy, setBusy] = useState(false)
  const [launchError, setLaunchError] = useState('')
  const launchLock = useRef(false)
  const idempotencyKey = useRef(null)

  useEffect(() => {
    let alive = true
    setLoading(true); setError(''); setLaunchError(''); setDetail(null)
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
    if (!projectId || !detail?.runs?.some(run => isPublishing(run.status))) return
    let alive = true
    const timer = window.setInterval(() => {
      deploymentsApi.list(projectId, detail.environment.id, 1, 10).then(next => {
        if (alive) setDetail(previous => previous ? {...previous, runs:next?.runs || [], runCount: next?.count ?? previous.runCount} : previous)
      }).catch(() => {})
    }, 12000)
    return () => { alive = false; window.clearInterval(timer) }
  }, [projectId, detail?.environment?.id, detail?.runs?.map(run => run.status).join('|')])

  // Re-read the authoritative HTTPS state while an old deployed site's DNS
  // is being reconciled. Refreshing does not launch Jenkins or change domains.
  useEffect(() => {
    if (!projectId || !detail?.project || publicSiteUrl(detail.project.public_site)) return
    let alive = true
    const timer = window.setInterval(() => {
      projectsApi.detail(projectId).then(response => {
        if (alive && response?.project) setDetail(old => old ? {...old, project:response.project} : old)
      }).catch(() => {})
    }, 20000)
    return () => {alive=false;window.clearInterval(timer)}
  }, [projectId,detail?.project?.public_site?.status,detail?.project?.public_site?.https])

  const visible = useMemo(() => projects.filter(p => {
    const status = (p.status || '').toUpperCase()
    const ok = filter === 'all' || (filter === 'active' ? status === 'ACTIVE' : status === 'ARCHIVED')
    return ok && `${p.name} ${p.repository} ${p.branch}`.toLowerCase().includes(query.toLowerCase().trim())
  }), [projects, query, filter])

  function editProject(p) {
    navigate(`/projects/${p.id}/configure`)
  }

  async function publishNow() {
    if (launchLock.current || busy || !detail || !canPublish(detail)) return
    launchLock.current = true
    setBusy(true); setLaunchError('')
    try {
      // Prévisualisation serveur obligatoire; cette requête ne déclenche rien.
      await deploymentsApi.preview(detail.project.id, detail.environment.id)
      // Même clé sur une reprise après échec réseau ambigu; ne jamais créer deux runs.
      const key = idempotencyKey.current || globalThis.crypto?.randomUUID?.()
      if (!key) throw new Error('La publication sécurisée nécessite un navigateur HTTPS récent.')
      idempotencyKey.current = key
      const answer = await deploymentsApi.launch(detail.project.id, detail.environment.id, key)
      if (!answer?.deployment?.id) throw new Error('AUTOKOM n’a pas confirmé la création de la publication.')
      idempotencyKey.current = null
      navigate(`/projects/${detail.project.id}/deployments/${answer.deployment.id}`)
    } catch (err) {
      setLaunchError(errorText(err))
      // En cas de réponse ambiguë, conserver la clé pour une nouvelle tentative idempotente.
    } finally { launchLock.current = false; setBusy(false) }
  }

  if (projectId) {
    if (loading) return <section className="ak-workspace-empty" role="status"><LoaderCircle size={19} className="ak-spin"/> Chargement du projet…</section>
    if (error && !detail) return <section className="ak-workspace-empty"><p className="feedback error" role="alert">{error}</p><button className="button-outline" onClick={() => navigate('/projects')}>Mes projets</button></section>
    if (!detail) return <section className="ak-workspace-empty"><h1>Projet introuvable</h1></section>
    const {project, environment, configurations, runs} = detail
    const cfg = configurations.find(item => !item.is_draft) || configurations[0]
    const latest = sortRecentRuns(runs).slice(0, 10)
    const active = latest.find(run => isPublishing(run.status))
    const [statusText, statusTone] = publicationLabel(latest[0]?.status)
    const ready = canPublish(detail)
    return <>
      <div className="ak-back-line"><button type="button" className="button-quiet" onClick={() => navigate('/projects')}><ArrowLeft size={17}/> Mes projets</button></div>
      <section className="page-hero ak-workspace-hero"><div><h1>{project.name}</h1><p>{project.repository_name} · {cfg?.branch || project.default_branch}</p></div>
        <div className="ak-project-hero-actions">
          <span className={`ak-publication-pill state-${latest.length ? statusTone : 'muted'}`} role="status">{latest.length ? statusText : 'Non publié'}</span>
          <button type="button" className="ak-project-settings-icon" onClick={() => navigate(`/projects/${project.id}/settings`)} title="Paramètres du projet" aria-label="Paramètres du projet"><Settings2 size={18}/></button>
          {active ? <button type="button" className="button-primary" onClick={() => navigate(`/projects/${project.id}/deployments/${active.id}`)}><Clock3 size={17}/> Suivre la publication</button> : <button type="button" className="button-primary" disabled={busy || !ready} onClick={publishNow}>{busy ? <LoaderCircle size={17} className="ak-spin"/> : <Rocket size={17}/>} {busy ? 'Préparation…' : 'Déployer maintenant'}</button>}
        </div>
      </section>
      {launchError && <p className="feedback error" role="alert">{launchError}</p>}
      <ProjectPublicSite site={project.public_site} onManage={() => navigate(`/sites/${project.id}`)}/>
      {!cfg && <p className="ak-deployment-error">Configurez votre projet avant sa première publication.</p>}
      <section className="ak-workspace-sheet ak-project-runs-first" aria-labelledby="recent-deployments">
        <div className="ak-workspace-sheet-heading"><div><h2 id="recent-deployments">Publications récentes</h2><p className="ak-section-subtitle">Les dix dernières activités de votre application.</p></div><span className="ak-project-state">{detail.runCount ?? runs.length} au total</span></div>
        {latest.length === 0 && <div className="ak-workspace-empty"><Workflow size={25}/><h3>Pas encore de publication</h3><p>Une fois votre projet configuré, vous pourrez le mettre en ligne ici.</p></div>}
        <div className="ak-deployment-list">{latest.map(run => {
          const [label,tone] = publicationLabel(run.status)
          return <article className="ak-deployment-card" key={run.id}>
            <div className="ak-deployment-summary">
              <div className="ak-deployment-symbol"><Workflow size={19}/></div>
              <div className="ak-deployment-summary-text"><strong>Publication du {formatDate(run.created_at)}</strong><small>{run.trigger_type === 'GIT_PUSH' ? 'Mise à jour automatique' : 'Lancement manuel'} · Réf. {String(run.id).slice(0,8)}</small></div>
              <span className={`ak-publication-pill state-${tone}`}>{label}</span>
              <button type="button" className="button-outline" onClick={() => navigate(`/projects/${project.id}/deployments/${run.id}`)}>Voir les détails <ArrowRight size={15}/></button>
            </div>
          </article>
        })}</div>
        {(detail.runCount ?? runs.length) > 0 && <div className="ak-run-all-link"><button type="button" className="button-outline" onClick={() => navigate(`/projects/${project.id}/history`)}>Voir tout l’historique <ArrowRight size={16}/></button></div>}
      </section>
      <details className="ak-workspace-sheet ak-project-config-fold">
        <summary className="ak-workspace-sheet-heading ak-project-config-summary"><h2>Configuration du projet</h2><span className="ak-project-config-suffix"><span className="ak-project-state">{cfg?.is_draft ? 'Brouillon' : cfg ? 'Configuration finalisée' : 'À configurer'}</span><ChevronDown size={20}/></span></summary>
        <dl className="ak-site-info-grid ak-workspace-details">
          <div><dt>Dépôt GitHub</dt><dd>{project.repository_name}</dd></div>
          <div><dt>Branche</dt><dd>{cfg?.branch || project.default_branch}</dd></div>
          <div><dt>Répertoire source</dt><dd>{cfg?.root_directory || 'Racine'}</dd></div>
          <div><dt>Environnement</dt><dd>{environment.name}</dd></div>
          <div><dt>Type de publication</dt><dd>{cfg?.mode === 'DEVSECOPS' ? 'Sécurité renforcée' : 'Standard'}</dd></div>
          <div><dt>État du projet</dt><dd>{project.status === 'ACTIVE' ? 'Actif' : 'Archivé'}</dd></div>
          <div><dt>Dernière modification</dt><dd>{formatDate(project.updated_at)}</dd></div>
          <div><dt>Dernière publication</dt><dd>{latest.length ? statusText : 'Aucune'}</dd></div>
        </dl>
        <div className="ak-project-detail-actions">
          <button className="button-outline" type="button" onClick={() => editProject(project)} disabled={busy || project.status === 'ARCHIVED'}><Settings2 size={16}/> Modifier la configuration</button>
          <button className="button-outline" type="button" onClick={() => navigate(`/projects/${project.id}/settings`)}><Settings2 size={16}/> Paramètres du projet</button>
        </div>
      </details>
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
        <span className={`ak-project-state ak-project-state--${project.status === 'ARCHIVED' ? 'archived' : 'active'}`} role="status">{project.status === 'ARCHIVED' ? 'Archivé' : 'Actif'}</span>
        <button type="button" className="ak-project-settings-icon" onClick={() => navigate(`/projects/${project.id}/settings`)} title={`Paramètres de ${project.name}`} aria-label={`Paramètres de ${project.name}`}><Settings2 size={18}/></button>
      </div>)}</div> : <div className="ak-workspace-empty"><FolderKanban size={29}/><h2>{error ? 'Chargement impossible' : projects.length ? 'Aucun résultat' : 'Aucun projet pour le moment'}</h2><p>{error ? 'Vérifiez votre session et l’API.' : projects.length ? 'Essayez une autre recherche ou un autre filtre.' : 'Créez votre premier projet pour le retrouver ici.'}</p>{!projects.length && !error && <button className="button-primary" type="button" onClick={() => navigate('/deploy')}>Créer un projet <ArrowRight size={16}/></button>}</div>}
    </section>
  </>
}
