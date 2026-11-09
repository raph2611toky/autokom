import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowRight, CheckCircle2, Clock3, FileText, LoaderCircle, ListChecks, RefreshCw, TerminalSquare, X } from 'lucide-react'
import { stackApi } from '../services/stackApi.js'
import { deploymentsApi } from '../services/deploymentsApi.js'
import { errorText } from '../services/projectWorkflow.js'
import { cleanCustomerLog, phaseOrder, phaseTitles, publicationLabel } from '../services/deploymentPresentation.js'
import { technicalTimeline, formatTechnicalFileLine } from '../services/deploymentLogTimeline.js'
import '../styles/stack-publication.css'

const SIDES = ['backend', 'frontend']
const ACTIVE = new Set(['QUEUED', 'PREPARING', 'ACTIVATING'])
const emptyModes = () => ({ simple: [], technical: [] })
const emptyLogs = () => ({ backend: emptyModes(), frontend: emptyModes() })
const emptyCursors = () => ({ backend: {simple:0,technical:0}, frontend: {simple:0,technical:0} })
function formatDate(raw) {
  if (!raw) return '—'
  const date = new Date(raw)
  return Number.isNaN(date.getTime()) ? '—' : new Intl.DateTimeFormat('fr-FR',{dateStyle:'short',timeStyle:'medium'}).format(date)
}
function statusOf(status) {
  const [label,tone] = publicationLabel(status)
  return <span className={`ak-publication-pill state-${tone}`} role="status">{label}</span>
}
function mergeBySequence(previous, batch) {
  const rows = new Map(previous.map(item => [item.sequence,item]))
  for (const item of batch) rows.set(item.sequence,item)
  return [...rows.values()].sort((a,b) => a.sequence-b.sequence)
}

/** One real group run, using the existing project-scoped run and log endpoints. */
export default function StackPublicationProgress({ projectId, runId, navigate }) {
  const [group,setGroup] = useState(null)
  const [runs,setRuns] = useState({backend:null,frontend:null})
  const [logs,setLogs] = useState(emptyLogs)
  const [mode,setMode] = useState('simple')
  const [tab,setTab] = useState('backend')
  const [filter,setFilter] = useState('all')
  const [error,setError] = useState('')
  const [loading,setLoading] = useState(true)
  const [refreshing,setRefreshing] = useState(false)
  const [more,setMore] = useState({backend:false,frontend:false})
  const [retryNonce,setRetryNonce] = useState(0)
  const queuedRefresh = useRef(false)
  const cursors = useRef(emptyCursors())
  const lastIds = useRef({backend:null,frontend:null})
  const inflight = useRef(false)
  const alive = useRef(true)
  const viewport = useRef(null)
  const following = useRef(true)

  const refresh = useCallback(async () => {
    if (inflight.current) {queuedRefresh.current = true;return}
    inflight.current = true
    setRefreshing(true)
    try {
      const response = await stackApi.publication(projectId,runId)
      const deployment = response?.deployment
      if (!deployment) throw new Error('Publication groupée introuvable.')
      if (!alive.current) return
      setGroup(deployment)
      // Fetch run details and logs only for child runs that have really been created.
      // Each cursor is independent per service and per log mode.
      const parts = await Promise.all(SIDES.map(async side => {
        try {
        const child = deployment[side]
        if (!child?.run_id || !child.environment_id) return {side,run:null,page:null}
        if (lastIds.current[side] !== child.run_id) {
          lastIds.current[side] = child.run_id
          cursors.current[side] = {simple:0,technical:0}
        }
        const [details,page] = await Promise.all([
          deploymentsApi.detail(projectId, child.environment_id, child.run_id),
          deploymentsApi.logs(projectId, child.environment_id, child.run_id, cursors.current[side][mode], mode),
        ])
        return {side,run:details?.deployment || null,page}
        } catch (err) {return {side,run:null,page:null,error:errorText(err)}}
      }))
      if (!alive.current) return
      setRuns(previous => {
        const next = {...previous}
        for (const part of parts) if (part.run) next[part.side] = part.run
        return next
      })
      setLogs(previous => {
        const next = {...previous}
        for (const part of parts) if (part.page) {
          next[part.side] = {...next[part.side], [mode]:mergeBySequence(
            lastIds.current[part.side] === part.run?.id ? previous[part.side][mode] : [],part.page.logs || [])}
        }
        return next
      })
      setMore(previous => {
        const next = {...previous}
        for (const part of parts) if (part.page) next[part.side] = (part.page.logs || []).length === 100
        return next
      })
      for (const part of parts) if (part.page) {
        cursors.current[part.side][mode] = part.page.next_after ?? cursors.current[part.side][mode]
      }
      setError(parts.filter(part=>part.error).map(part=>`${part.side} : ${part.error}`).join(' · '))
    } catch (err) { if (alive.current) setError(errorText(err)) }
    finally {
      if (alive.current) {setLoading(false);setRefreshing(false)}
      inflight.current = false
      if (alive.current && queuedRefresh.current) {
        queuedRefresh.current = false
        setRetryNonce(value=>value+1)
      }
    }
  },[projectId,runId,mode])

  useEffect(() => {
    alive.current = true
    setLoading(true); setGroup(null); setRuns({backend:null,frontend:null})
    setLogs(emptyLogs()); setMore({backend:false,frontend:false}); setError('')
    cursors.current = emptyCursors(); lastIds.current = {backend:null,frontend:null}; queuedRefresh.current = false
    refresh()
    return () => {alive.current = false}
  },[projectId,runId])

  // Re-fetch the selected mode on switch, and poll while the group is in progress.
  useEffect(() => {if (group) refresh()},[mode])
  useEffect(() => {if (retryNonce) refresh()},[retryNonce,refresh])
  useEffect(() => {
    if (!group || !ACTIVE.has(group.status)) return
    const timer = window.setInterval(refresh,5000)
    return () => window.clearInterval(timer)
  },[group?.status,refresh])
  useEffect(() => {
    if (mode==='technical' && following.current && viewport.current)
      viewport.current.scrollTop = viewport.current.scrollHeight
  },[logs,tab,mode,filter])

  const current = runs[tab]
  const child = group?.[tab]
  const selectedLogs = logs[tab]?.[mode] || []
  const lines = technicalTimeline(selectedLogs).filter(item => filter==='all' || item.stage_key===filter)
  // Keep the six approved pipeline phases visible even before a child run exists.
  // Only stages returned by Django show an execution status; others are marked 'À venir'.
  const storedStages = [...(current?.stages || [])].sort((a,b) => (a.position || 0)-(b.position || 0))
  const stageMap = new Map(storedStages.map(stage=>[stage.stage_key,stage]))
  const stages = [
    ...phaseOrder.map((key,index)=>stageMap.get(key) || {stage_key:key,position:index+1,status:'PENDING',notStarted:true}),
    ...storedStages.filter(stage=>!phaseOrder.includes(stage.stage_key)),
  ]
  const changeMode = next => {if (next!==mode){following.current=true;setFilter('all');setMode(next)}}
  const changeTab = next => {following.current=true;setFilter('all');setTab(next)}
  const downloadHref = child?.run_id && child?.environment_id
    ? `/api/projects/${projectId}/environments/${child.environment_id}/deployments/${child.run_id}/logs/download/` : null

  return <section className="ak-stack-publication" aria-label="Suivi des publications Backend et Frontend">
    <div className="ak-workspace-sheet ak-stack-publication-head">
      <div className="ak-workspace-sheet-heading">
        <div><h2>Publication groupée</h2><p className="ak-section-subtitle">Un déploiement, deux services et leurs journaux réels.</p></div>
        <div className="ak-stack-publication-actions">{group && statusOf(group.status)}
          <button className="button-outline" type="button" onClick={refresh} disabled={refreshing}><RefreshCw size={15} className={refreshing?'ak-spin':''}/> Actualiser</button></div>
      </div>
      {group && <p className="ak-stack-publication-summary">Démarrée le {formatDate(group.created_at)} · Réf. {String(group.id).slice(0,8)}
        {ACTIVE.has(group.status) && <span className="ak-run-updating"><LoaderCircle size={14} className="ak-spin"/> Actualisation automatique</span>}</p>}
      {loading && <p role="status"><LoaderCircle className="ak-spin" size={16}/> Chargement des étapes de publication…</p>}
      {error && <div className="feedback error ak-stack-dismissible" role="alert"><span>{error}</span><button className="ak-stack-dismiss" type="button" onClick={()=>setError('')} aria-label="Fermer l’erreur"><X size={16}/></button></div>}
      {group?.error_code && <p role="alert" className="ak-deployment-error">Publication interrompue : {group.error_code}. Les dernières versions saines sont conservées si la restauration a abouti.</p>}
    </div>
    <div className="ak-stack-service-tabs" role="tablist" aria-label="Service à consulter">
      {SIDES.map(side => <button key={side} role="tab" id={`ak-stack-tab-${side}`} aria-controls={`ak-stack-panel-${side}`} aria-selected={tab===side}
        className={tab===side?'active':''} type="button" onClick={()=>changeTab(side)}>
        <strong>{side==='backend'?'Backend':'Frontend'}</strong>
        {statusOf(group?.[side]?.status || 'PENDING')}
      </button>)}
    </div>
    <div role="tabpanel" id={`ak-stack-panel-${tab}`} aria-labelledby={`ak-stack-tab-${tab}`} className="ak-stack-publication-panel">
      <section className="ak-workspace-sheet ak-run-detail-sheet">
        <div className="ak-workspace-sheet-heading"><div><h2>Progression · {tab==='backend'?'Backend':'Frontend'}</h2><p className="ak-section-subtitle">Les étapes proviennent de l’exécution enregistrée par AUTOKOM.</p></div>{child && statusOf(child.status)}</div>
        <ol className="ak-run-detail-timeline">{stages.map(stage => {
          const [label,tone] = stage.notStarted ? ['À venir','waiting'] : publicationLabel(stage.status)
          return <li key={stage.stage_key} className={`ak-run-detail-stage state-${tone}`}>
            <span className="ak-run-detail-marker" aria-hidden="true">{tone==='success'?<CheckCircle2 size={20}/>:tone==='running'?<LoaderCircle className="ak-spin" size={20}/>:<Clock3 size={19}/>}</span>
            <span className="ak-run-detail-stage-main"><strong>{phaseTitles[stage.stage_key] || stage.name || 'Vérification'}</strong>{stage.started_at && <small>Début : {formatDate(stage.started_at)}</small>}</span>
            <span className="ak-run-detail-state">{label}</span>
          </li>
        })}</ol>
        {current?.jenkins_reference && <p className="ak-deployment-hint">{current.jenkins_reference} · Compilation isolée AUTOKOM</p>}
        {!child?.run_id && <p className="ak-deployment-hint">{tab==='frontend' ? 'Aucune exécution frontend créée pour cette ancienne publication.' : 'En attente de la mise en file.'}</p>}
        {tab==='frontend' && child?.run_id && group?.status==='PREPARING' && current?.status==='RUNNING' &&
          <p className="ak-deployment-hint">Compilation indépendante du backend. L’activation du conteneur frontend attend que le backend privé soit prêt.</p>}
        {current?.error_message && <p role="alert" className="ak-deployment-error">{cleanCustomerLog(current.error_message) || 'Cette exécution a échoué. Consultez le journal.'}</p>}
      </section>
      <section className="ak-workspace-sheet ak-run-detail-sheet">
        <div className="ak-workspace-sheet-heading ak-log-heading"><div><h2>Journal · {tab==='backend'?'Backend':'Frontend'}</h2><p className="ak-section-subtitle">Journal filtré par projet et sans secrets.</p></div>
          <div className="ak-log-mode-toggle" role="group" aria-label="Mode du journal">
            <button type="button" className={mode==='technical'?'active':''} aria-pressed={mode==='technical'} onClick={()=>changeMode('technical')}><TerminalSquare size={15}/> Technique</button>
            <button type="button" className={mode==='simple'?'active':''} aria-pressed={mode==='simple'} onClick={()=>changeMode('simple')}><ListChecks size={15}/> Synthèse</button>
          </div>
        </div>
        <div className="ak-log-filter"><label htmlFor="ak-stack-stage-filter">Filtrer par étape</label>
          <select id="ak-stack-stage-filter" value={filter} onChange={event=>setFilter(event.target.value)}><option value="all">Toutes les étapes</option>{phaseOrder.map(key=><option key={key} value={key}>{phaseTitles[key]}</option>)}</select>
          {downloadHref && <a href={downloadHref} download className="button-outline ak-log-download"><FileText size={15}/> Télécharger .log</a>}
        </div>
        <div ref={viewport} className={`ak-log-console ${mode==='technical'?'ak-log-console-detailed ak-log-file':''}`} role="log" aria-live="off" aria-label={`Journal ${tab}`} tabIndex={mode==='technical'?0:undefined}
          onScroll={event=>{if(mode==='technical'){const el=event.currentTarget;following.current=el.scrollHeight-el.scrollTop-el.clientHeight<70}}}>
          {lines.length ? lines.map(row=>mode==='technical'
            ? <div key={row.sequence} className={`ak-log-file-line level-${formatTechnicalFileLine(row).severity.toLowerCase()}`}><span className="ak-log-file-number" aria-hidden="true">{String(row.sequence).padStart(4,'0')}</span><code>{formatTechnicalFileLine(row).text}</code></div>
            : <div key={row.sequence} className={`ak-log-console-line ${row.isDetail?'ak-log-diagnostic':''}`}><time>{formatDate(row.created_at)}</time><span className="ak-log-stage-label">{phaseTitles[row.stage_key] || 'Préparation'}</span><code>{row.display}</code></div>)
            : <p className="ak-log-console-empty">{child?.run_id ? 'Aucune ligne de journal disponible pour cette sélection.' : 'Journal disponible dès le lancement du service.'}</p>}
        </div>
        {more[tab] && <p className="ak-deployment-hint">D’autres lignes sont en cours de récupération. Vous pouvez également télécharger le journal complet.</p>}
      </section>
    </div>
    <div className="ak-stack-footer"><button className="button-outline" type="button" onClick={()=>navigate(`/projects/${projectId}/history`)}>Historique des publications <ArrowRight size={16}/></button><button className="button-outline" type="button" onClick={()=>navigate(`/projects/${projectId}`)}>Voir le projet <ArrowRight size={16}/></button></div>
  </section>
}
