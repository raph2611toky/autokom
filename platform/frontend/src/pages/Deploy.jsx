import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, Check, CloudUpload, Code2, Download, GitBranch, Info, Layers3, LoaderCircle, Plus, Save, Settings2, ShieldCheck, Server, Trash2 } from 'lucide-react'
import GitHubConnector from '../components/GitHubConnector.jsx'
import ActionDialog from '../components/ActionDialog.jsx'
import FrontendEnvEditor from '../components/FrontendEnvEditor.jsx'
import ServiceCatalog from '../components/ServiceCatalog.jsx'
import { DEFAULT_SERVICES } from '../config/serviceCatalog.js'
import { getSampleDirectory, getSamplePackage, createPipelinePreview } from '../services/simulationData.js'
import { storePreview } from '../services/projectWorkspace.js'
import { API_AVAILABLE } from '../config/runtime.js'
import { deploymentsApi } from '../services/deploymentsApi.js'
import { stackApi } from '../services/stackApi.js'
import { gitApi } from '../services/projectsApi.js'
import { errorText, saveRemoteProject, prepareRemoteDeployment, getRemoteProjectDetail, remoteProjectToForm, verifiedDeploymentForm } from '../services/projectWorkflow.js'
import { detectSource, recommendFrontendCommands, frontendProfile, availableFrontendProfiles, supportedFrontend, BACKEND_PROFILES } from '../services/sourceAnalysis.js'
import { publicEnvForFramework } from '../services/frontendEnv.js'
import { companionSuggestion } from '../services/serviceComposition.js'
import { FREE_PRESET, validateProjectSelection } from '../config/deploymentProfiles.js'
import { PRICING_PLANS, findPricingPlan, getPreferredPlan, getBillingPeriod, inferPricingPlan, planToDeployment, rememberPlan, formatAr } from '../config/pricing.js'

const KEY = 'autokom.projects.drafts.v12'
const INITIAL = { id: '', projectName: '', repository: '', repositorySlug: '', githubRepositoryId: '', githubInstallationId: '', organizationId: '', backendProjectId: '', backendEnvironmentId: '', branch: '', workdir: './',
  pricingPlan: 'free', billingPeriod: 'monthly', resourcePlan: 'free', highAvailability: false, pipelineMode: 'devsecops', environment: 'production',
  domainMode: 'autokom', domain: '', autoDeploy: true, advancedBuild: {}, frontendEnv: [], frontendEnvTouched: false, services: { ...DEFAULT_SERVICES }, updatedAt: '' }
const STEPS = ['Source GitHub', 'Configuration', 'Publication']
const SAFE_SOURCE_PATH = /^(?:\.\/)?(?:[A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.-]+)*)?\/?$/
export function validGitSourcePath(value) {
  if (typeof value !== 'string' || value.length > 300 || !SAFE_SOURCE_PATH.test(value)) return false
  return !value.split('/').some(part => part === '..') && !value.includes('\\')
}
const BUILD_FIELDS = [['installCommand','Installation'],['buildCommand','Build'],['testCommand','Tests'],['outputDir','Sortie'],['startCommand','Démarrage'],['dockerfile','Dockerfile']]
function readDrafts() { try {
  const current = JSON.parse(localStorage.getItem(KEY) || '[]')
  if (Array.isArray(current) && current.length) return current.map(d => ({ ...d, pricingPlan: inferPricingPlan(d), billingPeriod: d.billingPeriod || 'monthly' }))
  const previous = JSON.parse(localStorage.getItem('autokom.deploy.drafts.v9') || '[]')
  if (!Array.isArray(previous)) return []
  return previous.map(d => ({ ...INITIAL, ...d, pricingPlan: inferPricingPlan(d), billingPeriod: d.billingPeriod || 'monthly', repositorySlug: d.repositorySlug || String(d.repository || '').replace(/^https:\/\/github\.com\//, '').replace(/\.git$/, '') }))
} catch { return [] } }
function downloadJson(obj, name) { const file = new Blob([JSON.stringify(obj,null,2)],{type:'application/json'}); const url = URL.createObjectURL(file); const a = document.createElement('a'); a.href = url; a.download = name; a.click(); window.setTimeout(()=>URL.revokeObjectURL(url),500) }

export default function Deploy({ navigate, editProjectId = '' }) {
  const [form, setForm] = useState(() => ({ ...INITIAL, ...planToDeployment(getPreferredPlan()), billingPeriod: getBillingPeriod(), ...(API_AVAILABLE ? { pipelineMode: 'devops' } : {}) }))
  const [step, setStep] = useState(editProjectId ? 1 : 0)
  const [editingProjectId, setEditingProjectId] = useState(editProjectId)
  const [editLoading, setEditLoading] = useState(Boolean(editProjectId))
  const [editError, setEditError] = useState('')
  // Un projet déjà associé à un backend ne doit jamais repasser par
  // le formulaire monolithique : il se modifie via les deux configurations.
  useEffect(() => {
    if (!API_AVAILABLE || !editProjectId) return
    let active = true
    stackApi.detail(editProjectId).then(result => {
      if (active && result?.stack) navigate(`/projects/${editProjectId}/stack`)
    }).catch(() => { /* L'édition classique reste accessible si aucun stack n'est présent. */ })
    return () => { active = false }
  }, [editProjectId])
  const [branches, setBranches] = useState([])
  const [notice, setNotice] = useState(null)
  // Notifications temporaires ; les erreurs permanentes restent dans les formulaires.
  useEffect(() => {
    if (!notice) return
    const timer = window.setTimeout(() => setNotice(null), 5000)
    return () => window.clearTimeout(timer)
  }, [notice])
  const [drafts, setDrafts] = useState(() => API_AVAILABLE ? [] : readDrafts())
  const [busy, setBusy] = useState(false)
  const [launchStage, setLaunchStage] = useState('')
  const [githubConnected, setGithubConnected] = useState(false)
  const [remoteIds, setRemoteIds] = useState(null)
  const [, setServerPreview] = useState(null)
  const launchKey = useRef(null)
  const companionLock = useRef(false)
  const preparedLaunch = useRef(null)
  useEffect(() => {
    try {
      if (API_AVAILABLE) {
        const ghResult = sessionStorage.getItem('autokom.githubConnectionNotice')
        if (ghResult) {
          sessionStorage.removeItem('autokom.githubConnectionNotice')
          setNotice({ type: ghResult === 'connected' ? 'success' : 'error',
            text: ghResult === 'connected' ? 'GitHub connecté. Choisissez votre installation et votre dépôt.' : ghResult === 'denied' ? 'Autorisation GitHub annulée ou refusée.' : 'Connexion momentanément indisponible. Réessayez.' })
        }
        // Le lien /projects/:id/configure est stable après rechargement navigateur.
        // Compatibilité avec les anciennes navigations par sessionStorage.
        const legacyId = !editProjectId && sessionStorage.getItem('autokom.ui.editRemoteProject')
        if (legacyId) sessionStorage.removeItem('autokom.ui.editRemoteProject')
        const remoteId = editProjectId || legacyId
        if (remoteId) {
          setEditingProjectId(remoteId)
          setEditLoading(true); setEditError('')
          getRemoteProjectDetail(remoteId).then(result => {
            if (String(result.project?.id) !== String(remoteId)) throw new Error('Identité du projet modifié invalide.')
            if (result.project?.status === 'ARCHIVED') throw new Error('Un projet archivé doit être restauré avant de modifier sa configuration.')
            setForm(current => ({ ...current, ...remoteProjectToForm(result) }))
            setRemoteIds({ backendProjectId: remoteId, backendEnvironmentId: result.environment.id })
            setStep(1)
          }).catch(error => setEditError(errorText(error)))
            .finally(() => setEditLoading(false))
        }
        return
      }
      const chosenId = localStorage.getItem('autokom.ui.editDraft')
      if (!chosenId) return
      localStorage.removeItem('autokom.ui.editDraft')
      const selected = readDrafts().find(d => d.id === chosenId)
      if (selected) { setForm({ ...INITIAL, ...selected }); setStep(0) }
    } catch { /* Brouillons facultatifs */ }
  }, [])
  const [analysis, setAnalysis] = useState(null)
  const [analysisStatus, setAnalysisStatus] = useState('idle')
  const [analysisError, setAnalysisError] = useState('')
  const [sourceInvalid, setSourceInvalid] = useState(false)
  const [sqliteConfirmOpen, setSqliteConfirmOpen] = useState(false)
  const [draftToRemove, setDraftToRemove] = useState(null)
  const sourceInvalidTimer = useRef(null)
  useEffect(() => () => window.clearTimeout(sourceInvalidTimer.current), [])
  const flashInvalidSource = () => {
    window.clearTimeout(sourceInvalidTimer.current)
    setSourceInvalid(true)
    sourceInvalidTimer.current = window.setTimeout(() => setSourceInvalid(false), 3000)
  }
  const update = data => { setServerPreview(null); preparedLaunch.current = null; launchKey.current = null; setForm(old => {
    const safeData = editingProjectId ? Object.fromEntries(Object.entries(data).filter(([key]) => !['repository', 'repositorySlug', 'githubRepositoryId', 'githubInstallationId', 'backendProjectId', 'organizationId'].includes(key))) : data
    const sourceChanged = ['repositorySlug', 'branch', 'workdir'].some(key => safeData[key] !== undefined && safeData[key] !== old[key])
    return { ...old, ...safeData, ...(sourceChanged ? { advancedBuild: { ...old.advancedBuild, profile:'', buildCommand:'', outputDir:'' } } : {}) }
  }) }
  const choosePlan = id => { rememberPlan(id, undefined, form.billingPeriod); update(planToDeployment(id)) }
  const updateBuild = (key,val) => { setServerPreview(null); preparedLaunch.current = null; launchKey.current = null; setForm(old => ({...old,advancedBuild:{...old.advancedBuild,[key]:val}})) }
  const projectError = validateProjectSelection(form)
  const build = { ...(analysis?.build || {}), ...form.advancedBuild }
  const [sourceFiles, setSourceFiles] = useState([])
  useEffect(() => {
    if (!API_AVAILABLE || !editingProjectId || !form.githubInstallationId || !form.githubRepositoryId) return
    let active = true
    gitApi.branches(form.githubInstallationId, form.githubRepositoryId)
      .then(result => { if (active) setBranches(result.branches || []) })
      .catch(() => { if (active) setBranches([]) })
    return () => { active = false }
  }, [editingProjectId, form.githubInstallationId, form.githubRepositoryId])
  const currentProfile = availableFrontendProfiles(analysis).includes(form.advancedBuild?.profile) ? form.advancedBuild.profile : frontendProfile(analysis)
  const suggested = useMemo(() => recommendFrontendCommands(analysis, sourceFiles, currentProfile), [analysis, sourceFiles, currentProfile])
  const resources = form.resourcePlan === 'free' ? FREE_PRESET : (analysis?.resources || FREE_PRESET)
  const sourceKey = [form.repositorySlug, form.branch, form.workdir].join('|')
  const [stableSourceKey, setStableSourceKey] = useState(sourceKey)
  useEffect(() => {
    if (sourceKey !== stableSourceKey && API_AVAILABLE) { setAnalysis(null); setAnalysisStatus('loading') }
    const timer = window.setTimeout(() => setStableSourceKey(sourceKey), 400)
    return () => window.clearTimeout(timer)
  }, [sourceKey, stableSourceKey])
  const staticReady = analysisStatus === 'ready' && supportedFrontend(analysis, currentProfile) && validGitSourcePath(form.workdir)
  const companionKind = API_AVAILABLE && staticReady ? companionSuggestion({analysis, selectedProfile:currentProfile, sourcePath:form.workdir, isEditing:Boolean(editingProjectId), status:analysisStatus}) : null

  useEffect(() => { if (!API_AVAILABLE) try { localStorage.setItem(KEY,JSON.stringify(drafts)) } catch {} }, [drafts])

  useEffect(() => {
    if (API_AVAILABLE || step === 0 || !form.githubRepositoryId || !form.branch) {
      setAnalysis(null)
      setAnalysisStatus('idle')
      return
    }
    const files = getSampleDirectory(form.githubRepositoryId, form.workdir)
    const pkg = getSamplePackage(form.githubRepositoryId, form.workdir)
    setSourceFiles(files)
    setAnalysis(detectSource(files, pkg))
    setAnalysisStatus('ready')
    setAnalysisError('')
  }, [step > 0, sourceKey, form.githubRepositoryId])

  useEffect(() => {
    if (!API_AVAILABLE || step === 0 || !form.githubInstallationId || !form.githubRepositoryId || !form.branch || sourceKey !== stableSourceKey) return
    let active = true
    setAnalysis(null); setSourceFiles([])
    setAnalysisStatus('loading'); setAnalysisError('')
    if (!validGitSourcePath(form.workdir)) {
      setAnalysisStatus('error'); setAnalysisError('Chemin de dossier invalide.'); flashInvalidSource()
      return
    }
    const sourceFolder = form.workdir === './' ? '' : form.workdir.replace(/^\.\//, '').replace(/\/$/, '')
    gitApi.directories(form.githubInstallationId, form.githubRepositoryId, form.branch, sourceFolder)
      .then(result => {
        if (!active) return
        setSourceInvalid(false)
        setSourceFiles(result.files || [])
        const detected = { ...detectSource(result.files || [], result.package || null), storageGuard: result.storage_guard || null }
        setAnalysis(detected); setAnalysisStatus('ready')
        setForm(current => current.detectedKind === detected.applicationType && current.detectedFramework === detected.framework && current.detectedVite === detected.isVite && current.hasNpmLockfile === Boolean((result.files || []).some(f => f.name === 'package-lock.json')) &&
          current.hasDockerfile === Boolean((result.files || []).some(f => f.name.toLowerCase() === 'dockerfile')) ? current :
          { ...current, detectedKind: detected.applicationType, detectedFramework: detected.framework, detectedVite: detected.isVite, hasNpmLockfile: Boolean((result.files || []).some(f => f.name === 'package-lock.json')),
            hasDockerfile: Boolean((result.files || []).some(f => f.name.toLowerCase() === 'dockerfile')) })
      })
      .catch(error => { if (active) { setAnalysis(null); setAnalysisStatus('error'); setAnalysisError(errorText(error)); flashInvalidSource() } })
    return () => { active = false }
  }, [step > 0, sourceKey, stableSourceKey, form.githubInstallationId, form.githubRepositoryId])

  function next() { if (step === 1 && API_AVAILABLE && !staticReady) { flashInvalidSource(); setNotice({type:'error',text: analysisError || 'Le dossier source doit exister et contenir un profil compatible.'}); return } if (editingProjectId && step === 0) { setStep(1); return } if (step === 0 && projectError) { setNotice({type:'error',text:projectError}); return } setNotice(null);setStep(s=>Math.min(2,s+1));window.scrollTo(0,0) }
  async function addCompanion() {
    if (companionLock.current || busy || !staticReady || !companionKind) return
    companionLock.current = true
    try {
      if (editingProjectId) { navigate(`/projects/${editingProjectId}/stack`); return }
      // Creation: persist the selected, validated service first; never start Jenkins here.
      // The second service remains optional and uses the same immutable GitHub repository.
      const saved = await save()
      if (saved?.projectId) navigate(`/projects/${saved.projectId}/stack`)
    } finally { companionLock.current = false }
  }
  async function save() {
    if (API_AVAILABLE) {
      if (busy || projectError || analysisStatus !== 'ready') { setNotice({ type: 'error', text: projectError || 'Ouvrez Configuration et attendez la détection réelle du projet avant d’enregistrer.' }); return }
      setBusy(true); setNotice(null)
      try {
        const checked = verifiedDeploymentForm(form, analysis)
        if (form.frontendEnvTouched && !BACKEND_PROFILES.includes(currentProfile)) publicEnvForFramework(form.frontendEnv, analysis, currentProfile)
        const saved = await saveRemoteProject({ ...checked, ...remoteIds }, {
          onCreated: ids => { setRemoteIds(current => ({ ...current, ...ids })); setForm(current => ({ ...current, ...ids })) },
        })
        setRemoteIds({ backendProjectId: saved.projectId, backendEnvironmentId: saved.environmentId, configurationId: saved.configurationId })
        setForm(current => ({ ...current, backendProjectId: saved.projectId, backendEnvironmentId: saved.environmentId }))
        setServerPreview(null)
        setNotice({ type: 'success', text: 'Votre projet a été enregistré.' })
        return saved
      } catch (error) { setNotice({ type: 'error', text: errorText(error) }); return null }
      finally { setBusy(false) }
    }

    if (!form.repositorySlug) { setNotice({type:'error',text:'Choisissez d’abord un dépôt GitHub.'}); return }
    const entry = {...form,frontendEnv: [],frontendEnvTouched:false,id:form.id || crypto.randomUUID(),updatedAt:new Date().toISOString()}
    const nextItems = [entry,...drafts.filter(d=>d.id!==entry.id)].slice(0,40)
    try { localStorage.setItem(KEY, JSON.stringify(nextItems)); setDrafts(nextItems); setForm(entry); setNotice({type:'success',text:'Projet enregistré dans ce navigateur.'}) }
    catch { setNotice({type:'error',text:'Impossible d’enregistrer ce projet.'}) }
  }
  const configuration = useMemo(() => ({ version:1, project:{name:form.projectName,environment:form.environment},source:{provider:'github',repository_id:form.githubRepositoryId,repository:form.repositorySlug,branch:form.branch,workdir:form.workdir},build,hosting:{offer:form.resourcePlan,pricing_plan:form.pricingPlan,billing_period:form.billingPeriod,resources,high_availability_requested:form.highAvailability,domain_mode:form.domainMode,domain:form.domain},pipeline:{mode:form.pipelineMode,auto_deploy_requested:form.autoDeploy,services:form.services},source_detection:analysis?{framework:analysis.framework,runtime:analysis.runtime,confidence:analysis.confidence}:null }),[form,analysis])
  function exportProject() { if (projectError) { setNotice({type:'error',text:projectError}); return } downloadJson(configuration,`${form.projectName.replace(/[^a-z0-9-_]/gi,'-') || 'autokom'}-config.json`);setNotice({type:'success',text:'Configuration téléchargée.'}) }
  function reset(){setForm({...INITIAL, ...planToDeployment(getPreferredPlan()), billingPeriod:getBillingPeriod(), ...(API_AVAILABLE ? {pipelineMode:'devops'} : {})});setStep(0);setAnalysis(null);setNotice(null);setRemoteIds(null);setServerPreview(null);setLaunchStage('');launchKey.current=null;preparedLaunch.current=null}
  async function previewPipeline(sqliteApproved = false) {
    if (API_AVAILABLE) {
      if (busy || editLoading || editError) return
      // Confirm before creating a configuration or starting a container.
      if (!sqliteApproved && analysisStatus === 'ready' && analysis?.storageGuard?.detected) {
        setSqliteConfirmOpen(true)
        return
      }
      if (editingProjectId && String(form.backendProjectId) !== String(editingProjectId)) {
        setNotice({type:'error', text:'La configuration ne correspond pas au projet sélectionné.'}); return
      }
      if (busy || projectError) { if (projectError) setNotice({ type: 'error', text: projectError }); return }
      setBusy(true); setNotice(null); setLaunchStage('Vérification du dossier source…')
      try {
        if (analysisStatus !== 'ready') throw new Error('La détection GitHub n’est pas terminée : réessayez après l’analyse.')
        const checked = verifiedDeploymentForm(form, analysis)
        if (form.frontendEnvTouched && !BACKEND_PROFILES.includes(currentProfile)) publicEnvForFramework(form.frontendEnv, analysis, currentProfile)
        // Si la requête de lancement précédente a perdu sa réponse, réutiliser la
        // même configuration déjà finalisée et la même clé d'idempotence.
        let ids = preparedLaunch.current
        if (!ids) {
          setLaunchStage('Enregistrement de la configuration…')
          const saved = await saveRemoteProject({ ...checked, ...remoteIds }, {
            onCreated: created => { setRemoteIds(current => ({ ...current, ...created })); setForm(current => ({ ...current, ...created })) },
          })
          ids = { projectId: saved.projectId, environmentId: saved.environmentId, configurationId: saved.configurationId }
          setRemoteIds({ backendProjectId: ids.projectId, backendEnvironmentId: ids.environmentId, configurationId: ids.configurationId })
          setForm(current => ({ ...current, backendProjectId: ids.projectId, backendEnvironmentId: ids.environmentId }))
          setLaunchStage('Validation et préparation du déploiement…')
          const preview = await prepareRemoteDeployment(ids)
          setServerPreview(preview)
          preparedLaunch.current = ids
        }
        // Le clic « Enregistrer » (édition) ou « Déployer » (création)
        // autorise explicitement le lancement, après validation serveur.
        const key = launchKey.current || crypto.randomUUID()
        launchKey.current = key
        setLaunchStage('Démarrage de la publication…')
        const response = await deploymentsApi.launch(ids.projectId, ids.environmentId, key)
        if (!response?.deployment?.id) throw new Error('Le serveur n’a pas confirmé le lancement de la publication.')
        launchKey.current = null
        preparedLaunch.current = null
        navigate(editingProjectId ? `/projects/${ids.projectId}/deployments/${response.deployment.id}` : `/projects/${ids.projectId}`)
      } catch (error) { setNotice({ type: 'error', text: errorText(error) }) }
      finally { setBusy(false); setLaunchStage('') }
      return
    }

    if (projectError) { setNotice({type:'error',text:projectError}); return }
    try {
      const entry = { ...form,frontendEnv: [],frontendEnvTouched:false, id:form.id || crypto.randomUUID(), updatedAt:new Date().toISOString() }
      const nextDrafts = [entry, ...drafts.filter(d => d.id !== entry.id)].slice(0,40)
      localStorage.setItem(KEY, JSON.stringify(nextDrafts))
      setDrafts(nextDrafts)
      setForm(entry)
      storePreview(createPipelinePreview({
        ...configuration, project:{ ...configuration.project, id:entry.id }, createdAt:entry.updatedAt,
      }))
      navigate('/sites')
    } catch { setNotice({type:'error',text:'Le parcours n’a pas pu être enregistré.'}) }
  }


  if (API_AVAILABLE && editLoading) return <section className="ak-workspace-empty" role="status"><LoaderCircle className="ak-spin" size={20}/> Chargement de la configuration du projet…</section>
  if (API_AVAILABLE && editError) return <section className="ak-workspace-empty"><p role="alert">{editError}</p><button type="button" className="button-outline" onClick={() => navigate(`/projects/${editingProjectId}`)}>Retour au projet</button></section>

  return <>
    <section className="page-hero ak-deploy-hero"><div><h1>{editingProjectId ? 'Modifier la configuration' : 'Déployer un projet'}</h1>{editingProjectId && <p>{form.projectName} · dépôt GitHub conservé</p>}</div>{editingProjectId ? <button type="button" className="button-quiet new-project" onClick={() => navigate(`/projects/${editingProjectId}`)}><ArrowLeft size={17}/> Retour au projet</button> : <button className="button-quiet new-project" onClick={reset} type="button"><Plus size={17}/> Nouveau projet</button>}</section>
    {!API_AVAILABLE && notice && <div className={`feedback ${notice.type} ak-deploy-notice`} role="status" aria-live="polite">{notice.text}</div>}

    <div className={`deploy-layout ak-quick-deploy${editingProjectId ? ' ak-edit-mode' : ''}`}><div className="deploy-column">
      <div className="step-navigation ak-simple-steps" role="group" aria-label="Étapes">{(editingProjectId ? STEPS.slice(1) : STEPS).map((s,i) => { const index = editingProjectId ? i + 1 : i; return <button type="button" key={s} className={`step-tab ${index===step?'current':''} ${index<step?'completed':''}`} onClick={()=>{if(index<=step)setStep(index)}}><span className="step-number">{index<step?<Check size={15}/>:i+1}</span><span>{editingProjectId && index===2 ? 'Validation' : s}</span></button> })}</div>
      <section className="cut-frame main-panel"><div className="cut-body">
        {step===0 && <><div className="panel-heading"><h2>Choisissez votre dépôt</h2></div><GitHubConnector form={form} update={data => {update(data);setAnalysis(null);setServerPreview(null)}} onNotice={setNotice} onConnectionChange={setGithubConnected}/>{form.githubRepositoryId && <label className="field ak-project-name"><span>Nom du projet</span><input value={form.projectName} onChange={e=>update({projectName:e.target.value})} placeholder="Nom de votre application"/></label>}</>}
        {step===1 && <><div className="panel-heading"><h2>Configuration</h2></div>
          {editingProjectId && <div className="ak-edit-repository" role="group" aria-label="Dépôt GitHub verrouillé"><GitBranch size={19}/><div><span>Dépôt GitHub du projet (non modifiable)</span><strong>{form.repositorySlug}</strong></div></div>}
          <div className="ak-source-edit">{editingProjectId && <label className="field ak-edit-branch"><span>Branche à déployer</span>{branches.length ? <select aria-label="Branche à déployer" value={form.branch} onChange={e => update({branch:e.target.value})}>{!branches.some(item => item.name === form.branch) && <option value={form.branch}>{form.branch}</option>}{branches.map(item => <option key={item.name} value={item.name}>{item.name}</option>)}</select> : <input aria-label="Branche à déployer" value={form.branch} onChange={e => update({branch:e.target.value})}/>}</label>}<label className="field"><span>Dossier source dans le dépôt</span><input aria-label="Dossier source" aria-invalid={sourceInvalid || (analysisStatus === 'error' && !validGitSourcePath(form.workdir))} className={sourceInvalid ? 'ak-source-invalid' : ''} placeholder="./ ou frontend" value={form.workdir} onChange={e => update({workdir:e.target.value})}/></label><small>Exemples : <code>./</code> ou <code>frontend</code>. Une nouvelle analyse est lancée automatiquement après modification. {editingProjectId ? ' Le dépôt GitHub reste lié au projet.' : ' Vous pouvez modifier la branche depuis l’étape Source GitHub.'}</small></div><div className={`ak-analysis-state state-${analysisStatus}`} role="status">{analysisStatus==='loading'?<LoaderCircle size={19} className="ak-spin"/>:<Info size={19}/>}<div><strong>{analysisStatus==='loading'?'Analyse du dépôt…':analysis ? `${analysis.framework} · ${analysis.runtime}` : API_AVAILABLE ? 'Configuration du dépôt réel' : 'Analyse du projet'}</strong><small>{analysis ? `Source : ${form.workdir} · Détection ${analysis.confidence === 'high' ? 'élevée' : 'indicative'}` : API_AVAILABLE ? (analysisError || 'Analyse GitHub du dossier source sélectionné, sans build préalable.') : analysisError || 'Lecture des fichiers GitHub.'}</small></div></div>

          {companionKind && <section className="ak-stack-upgrade ak-stack-contextual" aria-label={`Ajouter un ${companionKind}`}>
            <div className="ak-stack-upgrade-icon"><Plus size={19}/></div>
            <div><strong>Un autre service dans ce dépôt ?</strong><p>Votre source {companionKind === 'backend' ? 'frontend' : 'backend'} est reconnue. Vous pouvez ajouter un {companionKind} avec sa propre branche et son dossier, sans deuxième offre d’hébergement. Cette étape est facultative.</p></div>
            <button type="button" className="button-outline" disabled={busy} onClick={addCompanion}>{busy ? <LoaderCircle className="ak-spin" size={16}/> : <Plus size={16}/>} Ajouter un {companionKind}</button>
          </section>}
          {API_AVAILABLE && analysis?.applicationType === 'static' && analysis?.isVite && !analysis?.hasNpmLockfile && <p className="feedback error" role="alert">Ajoutez package-lock.json à votre dossier source pour activer la compilation reproductible gratuite.</p>}
          {suggested && <div className="ak-framework-advice" role="region" aria-label="Commandes de déploiement suggérées">
            <div className="ak-framework-advice-title"><Code2 size={18}/><strong>Détection & déploiement</strong><span className={suggested.supported ? 'ak-framework-ready' : 'ak-framework-preview'}>{suggested.supported ? 'Recette compatible' : 'À configurer'}</span></div>
            <p>{suggested.description}</p>
            {availableFrontendProfiles(analysis).length > 1 && <label className="field ak-profile-select"><span>Mode de publication</span><select value={currentProfile} onChange={e => updateBuild('profile', e.target.value)}>{availableFrontendProfiles(analysis).map(profile => <option key={profile} value={profile}>{({next_server:'Next.js serveur (SSR)',next_export:'Next.js statique (export)',python_fastapi:'FastAPI (ASGI)',python_flask:'Flask (WSGI)',python_wsgi:'Python WSGI'})[profile] || profile}</option>)}</select></label>}
            <div className="ak-command-grid"><div><small>Installation</small><code>{suggested.install}</code></div><div><small>Démarrage</small><code>{suggested.start}</code></div></div>
            {currentProfile !== 'html' && !BACKEND_PROFILES.includes(currentProfile) && <div className="ak-command-edit"><label className="field"><span>Script de compilation</span><input aria-label="Commande de compilation" value={form.advancedBuild?.buildCommand || suggested.build} placeholder="npm run build" onChange={e => updateBuild('buildCommand', e.target.value)}/></label><label className="field"><span>Répertoire de sortie</span><input aria-label="Dossier de sortie" value={currentProfile === 'npm_static' ? (form.advancedBuild?.outputDir || suggested.output) : suggested.output} disabled={currentProfile !== 'npm_static'} onChange={e => updateBuild('outputDir', e.target.value)}/></label></div>}
            <small>{BACKEND_PROFILES.includes(currentProfile) ? 'Recette serveur autorisée : dépendances et point d’entrée vérifiés pendant le build.' : 'Commandes limitées aux scripts npm du dépôt. Les installations et l’exécution restent isolées par AUTOKOM.'}</small>
          </div>}
          {currentProfile === 'next_server' && <label className="field ak-healthcheck-edit"><span>URL de santé (interne)</span><input aria-label="Chemin du healthcheck" value={form.advancedBuild?.healthcheckPath || '/'} placeholder="/" onChange={e => updateBuild('healthcheckPath', e.target.value)}/></label>}
          {!BACKEND_PROFILES.includes(currentProfile) ? <FrontendEnvEditor rows={form.frontendEnv || []} analysis={analysis} onChange={rows => update({frontendEnv: rows, frontendEnvTouched: true})}/> : <p className="ak-backend-env-notice">Les variables privées d’un service backend ne sont pas injectées via l’éditeur de variables frontend publiques.</p>}
          {analysis?.warnings?.length>0 && <div className="ak-analysis-warnings">{analysis.warnings.map(w=><p key={w}><Info size={15}/>{w}</p>)}</div>}
          <div className="ak-offer-header"><h3>Offre d’hébergement</h3><button className="ak-see-prices" type="button" onClick={() => navigate('/pricing')}>Comparer les tarifs <ArrowRight size={15}/></button></div>
          <div className="ak-offers ak-offers-three">
            {PRICING_PLANS.map(plan => <button key={plan.id} type="button" aria-pressed={inferPricingPlan(form) === plan.id} onClick={() => choosePlan(plan.id)} title={API_AVAILABLE && plan.id !== 'free' ? 'Demande soumise aux droits attribués par un administrateur (aucune activation automatique).' : ''} className={`ak-offer-card ${(inferPricingPlan(form) === plan.id) ? 'selected' : ''}`}>
              <span className="ak-offer-top"><span className="ak-offer-name">{plan.name}</span><span className="ak-offer-tag">{plan.id === 'free' ? 'Gratuite' : 'Droit requis'}</span></span>
              <strong>{plan.tagline}</strong>
              <small>{plan.servers}</small>
              <span className="ak-offer-price">{formatAr(form.billingPeriod === 'annual' ? plan.annual : plan.monthly)} / {form.billingPeriod === 'annual' ? 'an' : 'mois'}</span>
            </button>)}
          </div>
          {form.pricingPlan === 'pro' && <label className="ak-ha-choice"><span><strong>Haute disponibilité</strong><small>Demande à enregistrer ; nécessite une option HA active et un runtime GCP réellement prêt. Pas de mise en ligne locale de substitution.</small></span><input type="checkbox" checked={form.highAvailability} onChange={e => update({highAvailability:e.target.checked})} aria-label="Activer la haute disponibilité"/></label>}
          <div className="ak-essentials"><label className="field"><span>Type de publication</span><select value={form.pipelineMode} onChange={e=>update({pipelineMode:e.target.value})}><option value="devsecops">Protection renforcée</option><option value="devops">Standard</option></select></label><label className="field"><span>Environnement</span><select value={form.environment} onChange={e=>update({environment:e.target.value})}><option value="development">Développement</option><option value="staging">Préproduction</option><option value="production">Production</option></select></label><label className="field"><span>Domaine</span><select value={form.domainMode} onChange={e=>update({domainMode:e.target.value})}><option value="autokom">Sous-domaine AUTOKOM</option><option value="custom">Domaine personnalisé</option></select></label>{form.domainMode==='custom'&&<label className="field"><span>Nom de domaine</span><input value={form.domain} onChange={e=>update({domain:e.target.value})} placeholder="app.exemple.com"/></label>}</div>
          <details className="ak-advanced-settings"><summary><Settings2 size={17}/> Paramètres avancés <span>facultatif</span></summary><div className="ak-advanced-grid">{(!API_AVAILABLE ? BUILD_FIELDS : []).map(([key,label])=><label className="field" key={key}><span>{label}</span><input value={form.advancedBuild[key]??''} placeholder={analysis?.build?.[key] || 'Détection automatique'} onChange={e=>updateBuild(key,e.target.value)}/></label>)}</div><label className="ak-simple-checkbox"><input type="checkbox" checked={form.autoDeploy} onChange={e=>update({autoDeploy:e.target.checked})}/> Déploiement automatique sur push</label>{!API_AVAILABLE && <ServiceCatalog services={form.services || DEFAULT_SERVICES} capabilities={[]} capabilitiesLoaded={false} onChange={(group, choice)=>update({services:{...(form.services || DEFAULT_SERVICES),[group]:choice}})}/>}</details>
        </>}
        {step===2 && <><div className="panel-heading"><h2>Votre projet</h2></div><div className="ak-review-list"><div><GitBranch size={17}/><span>Dépôt</span><strong>{form.repositorySlug}</strong></div><div><Code2 size={17}/><span>Branche et dossier</span><strong>{form.branch} · {form.workdir}</strong></div><div><Code2 size={17}/><span>Framework</span><strong>{analysis?.framework || (API_AVAILABLE ? 'Analyse nécessaire' : 'Non déterminé')}</strong></div><div><Layers3 size={17}/><span>Offre</span><strong>{findPricingPlan(inferPricingPlan(form)).name}</strong></div><div><Code2 size={17}/><span>Variables frontend publiques</span><strong>{(form.frontendEnv || []).length} champ(s)</strong></div><div><ShieldCheck size={17}/><span>Publication</span><strong>{form.pipelineMode==='devsecops'?'Protection renforcée':'Standard'}</strong></div><div><Server size={17}/><span>Disponibilité</span><strong>{form.highAvailability?'HA demandée (activation contrôlée)':'Standard (serveur actuel tant que MIG non raccordé)'}</strong></div></div>{analysis?.storageGuard?.detected && <div className="ak-sqlite-review-note" role="status"><Info size={18}/><span><strong>SQLite détectée — données non persistantes</strong>Cette application peut perdre les données enregistrées dans le conteneur lors d’un redéploiement. Une confirmation sera demandée avant de continuer.</span></div>}<div className="ak-need-analysis"><Info size={18}/><span>{API_AVAILABLE ? (editingProjectId ? 'Les paramètres seront enregistrés comme une nouvelle version, puis une nouvelle publication sera lancée. La version en ligne reste conservée pendant la vérification.' : 'Votre projet sera préparé et mis en ligne automatiquement. Vous pourrez suivre sa progression dans Mes projets.') : 'Vérifiez votre configuration, puis consultez l’aperçu de votre projet.'}</span></div></>}
        {API_AVAILABLE && busy && launchStage && <div className="ak-deploy-loading" role="status" aria-live="polite"><LoaderCircle className="ak-spin" size={22}/><div><strong>Déploiement en cours</strong><span>{launchStage}</span></div></div>}
        {API_AVAILABLE && notice?.type === 'error' && <div className="feedback error ak-inline-error" role="alert">{notice.text}</div>}
        <div className="form-actions ak-form-actions">{!editingProjectId && <button type="button" className="button-quiet" onClick={save} disabled={busy}>{busy ? <LoaderCircle size={17} className="ak-spin"/> : <Save size={17}/>} {busy ? 'En cours…' : 'Enregistrer'}</button>}<div className="actions-right">{step>(editingProjectId ? 1 : 0)&&<button className="button-outline" type="button" onClick={()=>setStep(s=>s-1)}><ArrowLeft size={16}/> Retour</button>}{step<2?<button className="button-primary" type="button" onClick={next} disabled={busy || (API_AVAILABLE && ((step === 0 && (!githubConnected || Boolean(projectError))) || (step === 1 && !staticReady)))}>Continuer <ArrowRight size={16}/></button>:<><button type="button" className="button-outline" onClick={exportProject}><Download size={17}/> Exporter</button><button type="button" className="button-primary" onClick={() => previewPipeline()} disabled={busy || (API_AVAILABLE && (analysisStatus !== 'ready' || !staticReady))}>{busy ? <LoaderCircle size={17} className="ak-spin"/> : (editingProjectId ? <Save size={17}/> : <CloudUpload size={17}/>)} {busy ? 'Enregistrement en cours…' : (editingProjectId ? 'Enregistrer' : 'Déployer')} <ArrowRight size={16}/></button></>}</div></div>
      </div></section>
    <ActionDialog open={sqliteConfirmOpen} title="SQLite détectée — risque de perte de données"
      description="Votre projet utilise une base SQLite sous forme de fichier local. AUTOKOM autorise sa publication, mais les données stockées uniquement dans le conteneur risquent de disparaître lors de chaque redéploiement ou remplacement."
      confirmLabel={editingProjectId ? 'Enregistrer et redéployer' : 'Continuer le déploiement'} variant="warning" busy={busy}
      onClose={() => setSqliteConfirmOpen(false)}
      onConfirm={() => { setSqliteConfirmOpen(false); void previewPipeline(true) }}>
      <div className="ak-dialog-data-risk"><strong>Avant de continuer</strong><span>Sauvegardez les données existantes et privilégiez PostgreSQL ou MySQL/MariaDB avec stockage persistant pour la production.</span>{analysis?.storageGuard?.file && <small>Détecté dans : {analysis.storageGuard.file}</small>}</div>
    </ActionDialog>
    <ActionDialog open={Boolean(draftToRemove)} title="Supprimer ce brouillon ?" variant="danger"
      description={draftToRemove ? `Le brouillon « ${draftToRemove.projectName} » sera retiré de ce navigateur.` : ''}
      confirmLabel="Supprimer" onClose={() => setDraftToRemove(null)}
      onConfirm={() => { setDrafts(old => old.filter(v => v.id !== draftToRemove?.id)); setDraftToRemove(null) }}/>
    </div><aside className="deploy-aside"><div className="cut-frame side-panel"><div className="cut-body"><h3>{API_AVAILABLE ? 'Espace AUTOKOM' : 'Projets enregistrés'}</h3>{API_AVAILABLE && <p className="gh-muted">Retrouvez vos applications et leur progression dans Mes projets.</p>}<div className="ak-drafts-list">{!API_AVAILABLE && (drafts.length?drafts.map(d=><div className="ak-draft-item" key={d.id}><button type="button" onClick={()=>{setForm({...INITIAL,...d});setStep(0);setNotice(null)}}><strong>{d.projectName}</strong><small>{d.repositorySlug} · {d.branch}</small></button><button type="button" aria-label="Supprimer" onClick={() => setDraftToRemove(d)}><Trash2 size={15}/></button></div>):<p className="gh-muted">Aucun projet enregistré.</p>)}</div></div></div></aside></div>
  </>
}
