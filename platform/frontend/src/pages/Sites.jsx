import { useEffect, useMemo, useState } from 'react'
import { ArrowLeft, ArrowRight, CheckCircle2, Circle, FileText, GitBranch, Globe2, Layers3, Pause, Play, RotateCcw, Rocket, Search, Settings2, TerminalSquare } from 'lucide-react'
import { getProjectRecords } from '../services/projectWorkspace.js'
import { API_AVAILABLE } from '../config/runtime.js'
import LiveSites from './LiveSites.jsx'

const TABS = [ ['pipeline','Pipeline',Layers3], ['logs','Journaux',TerminalSquare], ['domains','Domaines',Globe2] ]

function configure(project, navigate) {
  if (project.draft) {
    try { localStorage.setItem('autokom.ui.editDraft', project.id) } catch { /* Stockage facultatif. */ }
  }
  navigate('/deploy')
}

function SiteDetail({ project, navigate }) {
  const [tab, setTab] = useState('pipeline')
  const [step, setStep] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [logQuery, setLogQuery] = useState('')
  const [logStage, setLogStage] = useState('all')
  const preview = project.preview
  const stages = preview?.stages || []
  useEffect(() => {
    if (!playing || !stages.length) return undefined
    const timer = window.setInterval(() => setStep(previous => Math.min(stages.length, previous + 1)), 950)
    return () => window.clearInterval(timer)
  }, [playing, stages.length])
  useEffect(() => { if (playing && step >= stages.length) setPlaying(false) }, [playing, step, stages.length])
  const logs = useMemo(() => stages.slice(0,step).map((title,index) => ({
    index, title,
    message: index === 0 ? `Source sélectionnée : ${project.repository} · ${project.branch}` : `Étape visualisée : ${title}`,
  })).filter(item => (logStage === 'all' || String(item.index) === logStage) && `${item.title} ${item.message}`.toLowerCase().includes(logQuery.trim().toLowerCase())), [stages,step,logQuery,logStage,project.repository,project.branch])

  return <>
    <div className="ak-back-line"><button type="button" className="button-quiet" onClick={() => navigate('/sites')}><ArrowLeft size={17}/> Tous les sites</button></div>
    <section className="page-hero ak-workspace-hero"><div><h1>{project.name}</h1><p>{project.repository} · {project.branch} · {project.workdir}</p></div><button className="button-outline" type="button" onClick={() => configure(project,navigate)}><Settings2 size={17}/> Configuration</button></section>
    <section className="ak-site-detail ak-site-detail--focused">
      <div className="ak-site-detail-head"><div><h2>Suivi du projet</h2><p className="ak-site-detail-subtitle">{preview ? 'Parcours préparé · aucun déploiement exécuté' : 'Pipeline non préparé'}</p></div><span className="ak-project-state">{preview?'Pipeline préparé':'Brouillon'}</span></div>
      <div role="tablist" className="ak-site-detail-tabs" aria-label="Détails du projet">
        {TABS.map(([id,label,Icon]) => <button key={id} role="tab" type="button" aria-selected={tab === id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}><Icon size={16}/> {label}</button>)}
      </div>
      <div className="ak-site-detail-body" role="tabpanel">
        {tab === 'pipeline' && (preview ? <>
          <div className="ak-preview-header ak-site-pipeline-controls"><div><h3>Pipeline {project.mode === 'devops' ? 'DevOps' : 'DevSecOps'}</h3><p>{step} sur {stages.length} étapes parcourues</p></div><div className="ak-preview-actions"><button className="button-outline" type="button" onClick={() => {setPlaying(false);setStep(0)}}><RotateCcw size={16}/> Recommencer</button><button className="button-primary" type="button" onClick={() => {if (step >= stages.length) setStep(0);setPlaying(current => !current)}}>{playing ? <Pause size={16}/> : <Play size={16}/>} {playing ? 'Pause' : step >= stages.length ? 'Rejouer' : 'Lire le pipeline'}</button></div></div>
          <div className="ak-preview-progress" role="progressbar" aria-label="Progression de l'aperçu" aria-valuenow={step} aria-valuemin={0} aria-valuemax={stages.length}><div style={{width: `${stages.length ? (step / stages.length) * 100 : 0}%`}}/></div>
          <ol className="ak-preview-stages ak-site-focused-stages">{stages.map((name,index) => <li key={`${index}-${name}`} className={`${index < step ? 'done' : index === step && playing ? 'current' : ''}`}><span className="ak-preview-stage-marker">{index < step ? <CheckCircle2 size={18}/> : <Circle size={16}/>}</span><span>{name}</span><small>{index < step ? 'Parcourue' : index === step && playing ? 'En cours' : 'À venir'}</small></li>)}</ol>
          <div className="ak-site-pipeline-bottom"><span>Visualisation du parcours uniquement</span><button type="button" className="button-outline" onClick={() => setTab('logs')}><TerminalSquare size={16}/> Consulter les journaux <ArrowRight size={15}/></button></div>
        </> : <div className="ak-workspace-empty"><Layers3 size={29}/><h3>Aucun pipeline préparé</h3><p>Finalisez la configuration du projet pour visualiser son pipeline.</p><button type="button" className="button-primary" onClick={() => configure(project,navigate)}>Configurer le projet</button></div>)}
        {tab === 'logs' && (preview ? <>
          <div className="ak-log-tools"><div className="search-field"><Search size={16}/><input value={logQuery} onChange={e => setLogQuery(e.target.value)} placeholder="Rechercher dans les journaux" aria-label="Rechercher dans les journaux" /></div><select value={logStage} onChange={e => setLogStage(e.target.value)} aria-label="Filtrer par étape"><option value="all">Toutes les étapes</option>{stages.map((name,index) => <option key={index} value={String(index)}>{name}</option>)}</select></div>
          <div className="ak-preview-console ak-site-console"><div className="ak-preview-console-head"><TerminalSquare size={15}/> Journal du parcours <span>APERÇU</span></div><div role="log" aria-live="polite">{logs.length ? logs.map(item => <p key={item.index}><span>{String(item.index+1).padStart(2,'0')}</span> {item.message}</p>) : <p>{step===0 ? 'Lancez la lecture du pipeline pour consulter les événements.' : 'Aucune ligne pour ce filtre.'}</p>}</div></div>
        </> : <div className="ak-workspace-empty"><FileText size={29}/><h3>Aucun journal</h3><p>Aucun pipeline n'a encore été préparé pour ce projet.</p></div>)}
        {tab === 'domains' && <div className="ak-site-domain-panel"><Globe2 size={28}/><div><h3>Publication et domaine</h3><p>{project.domainMode === 'custom' && project.domain ? `Domaine souhaité : ${project.domain}` : 'Un sous-domaine AUTOKOM sera attribué au moment de la publication.'}</p><p>Aucun domaine n'est actuellement publié ou vérifié.</p></div></div>}
      </div>
    </section>
  </>
}

export default function Sites(props) {
  return API_AVAILABLE ? <LiveSites {...props}/> : <DemoSites {...props}/>
}

function DemoSites({ navigate, projectId = null }) {
  const [projects] = useState(getProjectRecords)
  const [query, setQuery] = useState('')
  const selected = projects.find(project => project.id === projectId)
  const visible = useMemo(() => projects.filter(project => [project.name,project.repository,project.branch].join(' ').toLowerCase().includes(query.trim().toLowerCase())), [projects,query])

  if (projectId) {
    if (!selected) return <div className="ak-workspace-empty"><Globe2 size={30}/><h1>Projet introuvable</h1><button type="button" className="button-outline" onClick={() => navigate('/sites')}>Tous les sites</button></div>
    return <SiteDetail key={selected.id} project={selected} navigate={navigate}/>
  }
  return <>
    <section className="page-hero ak-sites-hero"><div><h1>Sites &amp; domaines</h1></div><button type="button" className="button-primary" onClick={() => navigate('/deploy')}><Rocket size={17}/> Nouveau projet</button></section>
    <section className="ak-workspace-sheet ak-sites-simple-list">
      <div className="ak-workspace-toolbar"><div className="search-field"><Search size={17}/><input value={query} onChange={event => setQuery(event.target.value)} placeholder="Rechercher un projet" aria-label="Rechercher un site ou projet" /></div></div>
      {visible.length ? <div className="ak-project-list">{visible.map(project => <button className="ak-site-list-row" type="button" onClick={() => navigate(`/sites/${encodeURIComponent(project.id)}`)} key={project.id}>
        <span className="ak-project-row-symbol"><Globe2 size={20}/></span>
        <span className="ak-project-row-main"><strong>{project.name}</strong><small><GitBranch size={13}/> {project.repository} · {project.branch}</small></span>
        <span className="ak-project-state">{project.preview ? 'Pipeline préparé' : 'Brouillon'}</span>
        <ArrowRight className="ak-site-list-chevron" size={19}/>
      </button>)}</div> : <div className="ak-workspace-empty"><Globe2 size={29}/><h2>{projects.length ? 'Aucun résultat' : 'Aucun projet enregistré'}</h2><p>{projects.length ? 'Modifiez votre recherche.' : 'Les projets créés apparaîtront ici. Cliquez sur un projet pour consulter son pipeline et ses journaux.'}</p>{!projects.length && <button type="button" className="button-primary" onClick={() => navigate('/deploy')}>Créer un projet <ArrowRight size={16}/></button>}</div>}
    </section>
  </>
}
