import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, CheckCircle2, Clock3, FileText, GitBranch, LoaderCircle, RefreshCw, Workflow, TerminalSquare, ListChecks } from 'lucide-react'
import { getRemoteProjectDetail, errorText } from '../services/projectWorkflow.js'
import { deploymentsApi } from '../services/deploymentsApi.js'
import { cleanCustomerLog, phaseOrder, phaseTitles, publicationLabel } from '../services/deploymentPresentation.js'
import { isPublishing, sortRecentRuns } from '../services/deploymentHistory.js'
import { technicalTimeline, formatTechnicalFileLine } from '../services/deploymentLogTimeline.js'
import ProjectPublicSite from '../components/ProjectPublicSite.jsx'
import { stackApi } from '../services/stackApi.js'
import { publicationPath, publicationKind } from '../services/stackHistory.js'

const PAGE_SIZE = 20
function dateTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : new Intl.DateTimeFormat('fr-FR', { dateStyle:'medium', timeStyle:'short' }).format(date)
}
function statusPill(value) {
  const [name, tone] = publicationLabel(value)
  return <span className={`ak-publication-pill state-${tone}`} role="status">{name}</span>
}
function displayTrigger(value) {
  return value === 'GIT_PUSH' ? 'Mise à jour automatique' : value === 'MANUAL' ? 'Déploiement manuel' : 'Nouvelle publication'
}
function mergeLogEntries(current, incoming) {
  const bySequence = new Map(current.map(item => [item.sequence,item]))
  incoming.forEach(item => bySequence.set(item.sequence,item))
  return [...bySequence.values()].sort((a,b) => a.sequence-b.sequence)
}

function RunDetails({ navigate, projectId, runId }) {
  const [projectInfo,setProjectInfo] = useState(null)
  const [run,setRun] = useState(null)
  const [messages,setMessages] = useState([])
  const [logMode,setLogMode] = useState('simple')
  const [stageFilter,setStageFilter] = useState('all')
  const [more,setMore] = useState(false)
  const [loading,setLoading] = useState(true)
  const [busy,setBusy] = useState(false)
  const [error,setError] = useState('')
  const logViewport = useRef(null)
  const followLogTail = useRef(true)
  const cursor = useRef(0)
  const liveStatus = useRef('')

  // Le premier chargement doit obtenir l'environnement avant de demander le run.
  useEffect(() => {
    let alive = true
    cursor.current = 0; liveStatus.current = ''; setRun(null); setMessages([]); setError(''); setLoading(true)
    getRemoteProjectDetail(projectId).then(async info => {
      if (!alive) return
      setProjectInfo(info)
      const [result, logPage] = await Promise.all([
        deploymentsApi.detail(projectId,info.environment.id,runId),
        deploymentsApi.logs(projectId,info.environment.id,runId,0,logMode),
      ])
      if (!alive) return
      setRun(result?.deployment || null)
      liveStatus.current = result?.deployment?.status || ''
      const batch = logPage?.logs || []
      setMessages(batch)
      cursor.current = logPage?.next_after || 0
      setMore(batch.length === 100)
    }).catch(err => { if (alive) setError(errorText(err)) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [projectId,runId,logMode])

  // Open Technique at its newest line; do not force the viewport down if the
  // user deliberately scrolled up to inspect older messages.
  useEffect(() => {
    const viewport = logViewport.current
    if (logMode === 'technical' && viewport && followLogTail.current) viewport.scrollTop = viewport.scrollHeight
  }, [logMode, stageFilter, messages, loading])

  const refresh = useCallback(async () => {
    if (!projectInfo?.environment?.id) return
    try {
      const [result, logPage] = await Promise.all([
        deploymentsApi.detail(projectId,projectInfo.environment.id,runId),
        deploymentsApi.logs(projectId,projectInfo.environment.id,runId,cursor.current,logMode),
      ])
      if (result?.deployment) { setRun(result.deployment); liveStatus.current = result.deployment.status }
      const batch = logPage?.logs || []
      setMessages(current => mergeLogEntries(current,batch))
      cursor.current = logPage?.next_after ?? cursor.current
      setMore(batch.length === 100)
      setError('')
    } catch (err) { setError(errorText(err)) }
  }, [projectId,projectInfo?.environment?.id,runId,logMode])

  useEffect(() => {
    if (!run || !isPublishing(run.status)) return
    const timer = window.setInterval(() => { if (isPublishing(liveStatus.current)) refresh() },12000)
    return () => window.clearInterval(timer)
  }, [refresh,run?.status])

  async function loadMore() {
    if (busy || !projectInfo?.environment?.id) return
    setBusy(true)
    try {
      const batchPage = await deploymentsApi.logs(projectId,projectInfo.environment.id,runId,cursor.current,logMode)
      const batch = batchPage?.logs || []
      setMessages(current => mergeLogEntries(current,batch))
      cursor.current = batchPage?.next_after ?? cursor.current
      setMore(batch.length === 100)
    } catch (err) { setError(errorText(err)) }
    finally { setBusy(false) }
  }

  if (loading) return <section className="ak-workspace-empty" role="status"><LoaderCircle className="ak-spin" size={20}/> Chargement de la publication…</section>
  if (!run || !projectInfo) return <section className="ak-workspace-empty"><h1>Publication introuvable</h1>{error && <p role="alert">{error}</p>}<button className="button-outline" onClick={() => navigate(`/projects/${projectId}`)}>Revenir au projet</button></section>
  const sortedStages = [...(run.stages || [])].sort((a,b) => (a.position || phaseOrder.indexOf(a.stage_key)) - (b.position || phaseOrder.indexOf(b.stage_key)))
  const logLines = technicalTimeline(messages)
    .filter(item => stageFilter === 'all' || item.stage_key === stageFilter)
  const selectMode = next => { if (next !== logMode) { followLogTail.current = true; setStageFilter('all'); setLogMode(next) } }
  return <>
    <div className="ak-back-line"><button type="button" className="button-quiet" onClick={() => navigate(`/projects/${projectId}`)}><ArrowLeft size={16}/> {projectInfo.project.name}</button></div>
    <section className="page-hero ak-workspace-hero"><div><p className="ak-page-kicker">Détails de publication</p><h1>Publication {String(run.id).slice(0,8)}</h1><p>{dateTime(run.created_at)} · {displayTrigger(run.trigger_type)}</p></div><div className="ak-project-hero-actions">{statusPill(run.status)}<button className="button-outline" type="button" onClick={refresh}><RefreshCw size={16}/> Actualiser</button></div></section>
    {error && <p className="feedback error" role="alert">{error}</p>}
    <ProjectPublicSite site={projectInfo.project.public_site} onManage={() => navigate(`/sites/${projectId}`)}/>
    <section className="ak-workspace-sheet ak-run-detail-sheet" aria-labelledby="run-stages-title">
      <div className="ak-workspace-sheet-heading"><div><h2 id="run-stages-title">Progression</h2><p className="ak-section-subtitle">Suivez les étapes de mise en ligne de votre application.</p></div>{isPublishing(run.status) && <span className="ak-run-updating"><LoaderCircle size={15} className="ak-spin"/> Actualisation automatique</span>}</div>
      {sortedStages.length ? <ol className="ak-run-detail-timeline">{sortedStages.map(stage => {
        const [label,tone] = publicationLabel(stage.status)
        return <li key={stage.stage_key} className={`ak-run-detail-stage state-${tone}`}>
          <span className="ak-run-detail-marker" aria-hidden="true">{tone === 'success' ? <CheckCircle2 size={21}/> : tone === 'running' ? <LoaderCircle className="ak-spin" size={21}/> : <Clock3 size={19}/>}</span>
          <span className="ak-run-detail-stage-main"><strong>{phaseTitles[stage.stage_key] || 'Vérification'}</strong>{stage.started_at && <small>Début : {dateTime(stage.started_at)}</small>}</span>
          <span className="ak-run-detail-state">{label}</span>
        </li>
      })}</ol> : <p className="ak-deployment-hint">Les étapes apparaîtront dès le début de la préparation.</p>}
      {run.error_message && <p className="ak-deployment-error" role="alert">{cleanCustomerLog(run.error_message) || 'Une erreur a interrompu la publication.'}</p>}
    </section>
    <section className="ak-workspace-sheet ak-run-detail-sheet" aria-labelledby="run-logs-title">
      <div className="ak-workspace-sheet-heading ak-log-heading">
        <div><h2 id="run-logs-title">Journal de publication</h2><p className="ak-section-subtitle">Suivez les événements de chaque étape, sans afficher d’identifiants secrets.</p></div>
        <div className="ak-log-mode-toggle" role="group" aria-label="Mode des journaux">
          <button type="button" className={logMode==='technical'?'active':''} aria-pressed={logMode==='technical'} onClick={() => selectMode('technical')}><TerminalSquare size={15}/> Technique</button>
          <button type="button" className={logMode==='simple'?'active':''} aria-pressed={logMode==='simple'} onClick={() => selectMode('simple')}><ListChecks size={15}/> Synthèse</button>
        </div>
      </div>
      <div className="ak-log-filter">
        <label htmlFor="ak-stage-filter">Filtrer par étape</label>
        <select id="ak-stage-filter" value={stageFilter} onChange={event=>setStageFilter(event.target.value)}>
          <option value="all">Toutes les étapes</option>
          {phaseOrder.map(key => <option key={key} value={key}>{phaseTitles[key]}</option>)}
        </select>
        <span>{logMode === 'technical' ? 'Journal enrichi, filtré par projet et sans secrets.' : 'Même chronologie que l’ancien journal technique.'}</span>
        <a className="button-outline ak-log-download" download href={`/api/projects/${projectId}/environments/${projectInfo.environment.id}/deployments/${runId}/logs/download/`}><FileText size={15}/> Télécharger .log</a>
      </div>
      <div ref={logViewport} onScroll={event => { if (logMode==='technical') { const el=event.currentTarget; followLogTail.current = el.scrollHeight-el.scrollTop-el.clientHeight < 70 } }} className={`ak-log-console ${logMode==='technical'?'ak-log-console-detailed ak-log-file':''}`} tabIndex={logMode==='technical'?0:undefined} role="log" aria-label={logMode==='technical'?'Journal technique de la publication':'Synthèse de la publication'} aria-live="off">
        {logLines.length ? logLines.map(row => logMode === 'technical'
          ? <div key={row.sequence} className={`ak-log-file-line level-${formatTechnicalFileLine(row).severity.toLowerCase()}`}>
              <span className="ak-log-file-number" aria-hidden="true">{String(row.sequence).padStart(4,'0')}</span>
              <code>{formatTechnicalFileLine(row).text}</code>
            </div>
          : <div key={row.sequence} className={`ak-log-console-line ${row.isDetail?'ak-log-diagnostic':''}`}>
              <time>{dateTime(row.created_at)}</time>
              <span className="ak-log-stage-label">{row.channel ? `${phaseTitles[row.stage_key] || 'Préparation'} · ${row.channel}` : phaseTitles[row.stage_key] || 'Préparation'}</span>
              <code>{row.display}</code>
            </div>) : <p className="ak-log-console-empty">{run.status === 'FAILED' ? 'Cette exécution ne possède pas de diagnostic détaillé conservé. Les prochaines publications préserveront une analyse de démarrage.' : 'Aucune information disponible pour cette étape.'}</p>}
      </div>
      {more && <button className="button-outline ak-run-loadmore" disabled={busy} onClick={loadMore}>{busy ? <LoaderCircle size={16} className="ak-spin"/> : <ArrowRight size={16}/>} Charger davantage de lignes</button>}
    </section>
    <details className="ak-workspace-sheet ak-project-config-fold"><summary className="ak-workspace-sheet-heading ak-project-config-summary"><h2>Configuration utilisée</h2><span className="ak-project-config-suffix"><GitBranch size={18}/></span></summary>
      <dl className="ak-site-info-grid ak-workspace-details"><div><dt>Dépôt GitHub</dt><dd>{projectInfo.project.repository_name}</dd></div><div><dt>Branche</dt><dd>{run.branch || '—'}</dd></div><div><dt>Version</dt><dd>{run.commit_sha ? run.commit_sha.slice(0,12) : '—'}</dd></div><div><dt>Type de publication</dt><dd>{run.mode_snapshot === 'DEVSECOPS' ? 'Sécurité renforcée' : 'Standard'}</dd></div><div><dt>Début</dt><dd>{dateTime(run.started_at)}</dd></div><div><dt>Fin</dt><dd>{dateTime(run.finished_at)}</dd></div></dl>
    </details>
  </>
}

function RunHistory({ navigate, projectId }) {
  const [info,setInfo] = useState(null)
  const [stackEnabled,setStackEnabled] = useState(false)
  const [runs,setRuns] = useState([])
  const [page,setPage] = useState(1)
  const [count,setCount] = useState(0)
  const [loading,setLoading] = useState(true)
  const [error,setError] = useState('')
  useEffect(() => {
    let alive = true
    Promise.all([getRemoteProjectDetail(projectId), stackApi.detail(projectId)])
      .then(([result, stack]) => { if (alive) { setInfo(result); setStackEnabled(Boolean(stack?.stack)) } })
      .catch(err => { if (alive) { setError(errorText(err)); setLoading(false) } })
    return () => { alive = false }
  },[projectId])
  useEffect(() => {
    if (!info?.environment?.id) return
    let alive = true
    setLoading(true); setError('')
    (stackEnabled ? stackApi.history(projectId,page,PAGE_SIZE) : deploymentsApi.list(projectId,info.environment.id,page,PAGE_SIZE)).then(result => {
      if (!alive) return
      setRuns(sortRecentRuns(result?.runs || [])); setCount(result?.count || 0)
    }).catch(err => { if (alive) setError(errorText(err)) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  },[projectId,info?.environment?.id,page,stackEnabled])
  useEffect(() => {
    if (!info?.environment?.id || !runs.some(run=>isPublishing(run.status))) return
    let alive = true
    const timer = window.setInterval(() => {
      (stackEnabled ? stackApi.history(projectId,page,PAGE_SIZE) : deploymentsApi.list(projectId,info.environment.id,page,PAGE_SIZE))
        .then(result => {
          if (alive) {setRuns(sortRecentRuns(result?.runs || []));setCount(result?.count ?? 0)}
        }).catch(err=>{if(alive)setError(errorText(err))})
    },12000)
    return () => {alive=false;window.clearInterval(timer)}
  },[projectId,info?.environment?.id,stackEnabled,page,runs.map(run=>run.status).join('|')])
  const pages = Math.max(1,Math.ceil(count / PAGE_SIZE))
  return <>
    <div className="ak-back-line"><button className="button-quiet" onClick={() => navigate(`/projects/${projectId}`)}><ArrowLeft size={16}/> Retour au projet</button></div>
    <section className="page-hero ak-workspace-hero"><div><h1>Historique des publications</h1><p>{info?.project?.name || 'Votre projet'} · {count} publication(s)</p></div></section>
    {error && <p className="feedback error" role="alert">{error}</p>}
    {info?.project && <ProjectPublicSite site={info.project.public_site} onManage={() => navigate(`/sites/${projectId}`)}/>}
    <section className="ak-workspace-sheet"><div className="ak-workspace-sheet-heading"><h2>Toutes les publications</h2><span className="ak-project-state">Page {page} / {pages}</span></div>
      {loading ? <div className="ak-workspace-empty" role="status"><LoaderCircle className="ak-spin"/> Chargement…</div> : runs.length ? <div className="ak-deployment-list">{runs.map(run => <article className="ak-deployment-card" key={run.id}><div className="ak-deployment-summary"><div className="ak-deployment-symbol"><Workflow size={19}/></div><div className="ak-deployment-summary-text"><strong>{dateTime(run.created_at)}</strong><small>{publicationKind(run)} · {displayTrigger(run.trigger_type)} · Réf. {String(run.id).slice(0,8)}</small></div>{statusPill(run.status)}<button className="button-outline" type="button" onClick={() => navigate(publicationPath(projectId,run))}>Détails <ArrowRight size={16}/></button></div></article>)}</div> : <p className="ak-deployment-hint">Aucune publication enregistrée.</p>}
      {count > PAGE_SIZE && <nav className="ak-run-pager" aria-label="Pages des publications"><button type="button" className="button-outline" disabled={page<=1 || loading} onClick={() => setPage(x=>Math.max(1,x-1))}>Précédente</button><span>{page} / {pages}</span><button type="button" className="button-outline" disabled={page>=pages || loading} onClick={() => setPage(x=>Math.min(pages,x+1))}>Suivante <ArrowRight size={14}/></button></nav>}
    </section>
  </>
}

export default function LiveRuns({ navigate, projectId, runId }) {
  return runId ? <RunDetails key={`${projectId}:${runId}`} navigate={navigate} projectId={projectId} runId={runId}/> : <RunHistory key={projectId} navigate={navigate} projectId={projectId}/>
}
