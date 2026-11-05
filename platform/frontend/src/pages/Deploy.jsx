import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, Check, CloudUpload, Code2, Download, GitBranch, Info, Layers3, LoaderCircle, Plus, Save, Settings2, ShieldCheck, Server, Trash2 } from 'lucide-react'
import GitHubConnector from '../components/GitHubConnector.jsx'
import ServiceCatalog from '../components/ServiceCatalog.jsx'
import { DEFAULT_SERVICES } from '../config/serviceCatalog.js'
import { getSampleDirectory, getSamplePackage, createPipelinePreview } from '../services/simulationData.js'
import { storePreview } from '../services/projectWorkspace.js'
import { API_AVAILABLE } from '../config/runtime.js'
import { deploymentsApi } from '../services/deploymentsApi.js'
import { gitApi } from '../services/projectsApi.js'
import { errorText, saveRemoteProject, prepareRemoteDeployment, getRemoteProjectDetail, remoteProjectToForm } from '../services/projectWorkflow.js'
import { detectSource } from '../services/sourceAnalysis.js'
import { FREE_PRESET, validateProjectSelection } from '../config/deploymentProfiles.js'
import { PRICING_PLANS, findPricingPlan, getPreferredPlan, getBillingPeriod, inferPricingPlan, planToDeployment, rememberPlan, formatAr } from '../config/pricing.js'

const KEY = 'autokom.projects.drafts.v12'
const INITIAL = { id: '', projectName: '', repository: '', repositorySlug: '', githubRepositoryId: '', githubInstallationId: '', organizationId: '', backendProjectId: '', backendEnvironmentId: '', branch: '', workdir: './',
  pricingPlan: 'free', billingPeriod: 'monthly', resourcePlan: 'free', highAvailability: false, pipelineMode: 'devsecops', environment: 'production',
  domainMode: 'autokom', domain: '', autoDeploy: true, advancedBuild: {}, services: { ...DEFAULT_SERVICES }, updatedAt: '' }
const STEPS = ['Source GitHub', 'Configuration', 'Vérification']
const BUILD_FIELDS = [['installCommand','Installation'],['buildCommand','Build'],['testCommand','Tests'],['outputDir','Sortie'],['startCommand','Démarrage'],['appPort','Port'],['dockerfile','Dockerfile']]
function readDrafts() { try {
  const current = JSON.parse(localStorage.getItem(KEY) || '[]')
  if (Array.isArray(current) && current.length) return current.map(d => ({ ...d, pricingPlan: inferPricingPlan(d), billingPeriod: d.billingPeriod || 'monthly' }))
  const previous = JSON.parse(localStorage.getItem('autokom.deploy.drafts.v9') || '[]')
  if (!Array.isArray(previous)) return []
  return previous.map(d => ({ ...INITIAL, ...d, pricingPlan: inferPricingPlan(d), billingPeriod: d.billingPeriod || 'monthly', repositorySlug: d.repositorySlug || String(d.repository || '').replace(/^https:\/\/github\.com\//, '').replace(/\.git$/, '') }))
} catch { return [] } }
function downloadJson(obj, name) { const file = new Blob([JSON.stringify(obj,null,2)],{type:'application/json'}); const url = URL.createObjectURL(file); const a = document.createElement('a'); a.href = url; a.download = name; a.click(); window.setTimeout(()=>URL.revokeObjectURL(url),500) }

export default function Deploy({ navigate }) {
  const [form, setForm] = useState(() => ({ ...INITIAL, ...planToDeployment(getPreferredPlan()), billingPeriod: getBillingPeriod(), ...(API_AVAILABLE ? { pipelineMode: 'devops' } : {}) }))
  const [step, setStep] = useState(0)
  const [notice, setNotice] = useState(null)
  const [drafts, setDrafts] = useState(() => API_AVAILABLE ? [] : readDrafts())
  const [busy, setBusy] = useState(false)
  const [githubConnected, setGithubConnected] = useState(false)
  const [remoteIds, setRemoteIds] = useState(null)
  const [serverPreview, setServerPreview] = useState(null)
  const launchKey = useRef(null)
  useEffect(() => {
    try {
      if (API_AVAILABLE) {
        const ghResult = sessionStorage.getItem('autokom.githubConnectionNotice')
        if (ghResult) {
          sessionStorage.removeItem('autokom.githubConnectionNotice')
          setNotice({ type: ghResult === 'connected' ? 'success' : 'error',
            text: ghResult === 'connected' ? 'GitHub connecté. Choisissez votre installation et votre dépôt.' : ghResult === 'denied' ? 'Autorisation GitHub annulée ou refusée.' : 'Échec technique de connexion GitHub. Consultez les journaux Django avec sudo autokom app backend logs --github.' })
        }
        const remoteId = sessionStorage.getItem('autokom.ui.editRemoteProject')
        if (remoteId) {
          sessionStorage.removeItem('autokom.ui.editRemoteProject')
          getRemoteProjectDetail(remoteId).then(result => {
            setForm(current => ({ ...current, ...remoteProjectToForm(result) }))
            setStep(0)
            setNotice({ type: 'success', text: 'Projet chargé depuis AUTOKOM.' })
          }).catch(error => setNotice({ type: 'error', text: errorText(error) }))
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
  const update = data => { setServerPreview(null); setForm(old => ({ ...old, ...data })) }
  const choosePlan = id => { rememberPlan(id, undefined, form.billingPeriod); update(planToDeployment(id)) }
  const updateBuild = (key,val) => { setServerPreview(null); setForm(old => ({...old,advancedBuild:{...old.advancedBuild,[key]:val}})) }
  const projectError = validateProjectSelection(form)
  const build = { ...(analysis?.build || {}), ...form.advancedBuild }
  const resources = form.resourcePlan === 'free' ? FREE_PRESET : (analysis?.resources || FREE_PRESET)
  const sourceKey = [form.repositorySlug, form.branch, form.workdir].join('|')

  useEffect(() => { if (!API_AVAILABLE) try { localStorage.setItem(KEY,JSON.stringify(drafts)) } catch {} }, [drafts])

  useEffect(() => {
    if (API_AVAILABLE || step === 0 || !form.githubRepositoryId || !form.branch) {
      setAnalysis(null)
      setAnalysisStatus('idle')
      return
    }
    const files = getSampleDirectory(form.githubRepositoryId, form.workdir)
    const pkg = getSamplePackage(form.githubRepositoryId, form.workdir)
    setAnalysis(detectSource(files, pkg))
    setAnalysisStatus('ready')
    setAnalysisError('')
  }, [step > 0, sourceKey, form.githubRepositoryId])

  useEffect(() => {
    if (!API_AVAILABLE || step === 0 || !form.githubInstallationId || !form.githubRepositoryId || !form.branch) return
    let active = true
    setAnalysisStatus('loading'); setAnalysisError('')
    const sourceFolder = form.workdir === './' ? '' : form.workdir
    gitApi.directories(form.githubInstallationId, form.githubRepositoryId, form.branch, sourceFolder)
      .then(result => {
        if (!active) return
        const detected = detectSource(result.files || [], result.package || null)
        setAnalysis(detected); setAnalysisStatus('ready')
        setForm(current => current.detectedKind === detected.applicationType && current.detectedFramework === detected.framework && current.detectedVite === detected.isVite && current.hasNpmLockfile === Boolean((result.files || []).some(f => f.name === 'package-lock.json')) &&
          current.hasDockerfile === Boolean((result.files || []).some(f => f.name.toLowerCase() === 'dockerfile')) ? current :
          { ...current, detectedKind: detected.applicationType, detectedFramework: detected.framework, detectedVite: detected.isVite, hasNpmLockfile: Boolean((result.files || []).some(f => f.name === 'package-lock.json')),
            hasDockerfile: Boolean((result.files || []).some(f => f.name.toLowerCase() === 'dockerfile')) })
      })
      .catch(error => { if (active) { setAnalysis(null); setAnalysisStatus('error'); setAnalysisError(errorText(error)) } })
    return () => { active = false }
  }, [step > 0, sourceKey, form.githubInstallationId, form.githubRepositoryId])

  function next() { if (step === 0 && projectError) { setNotice({type:'error',text:projectError}); return } setNotice(null);setStep(s=>Math.min(2,s+1));window.scrollTo(0,0) }
  async function save() {
    if (API_AVAILABLE) {
      if (busy || projectError || analysisStatus !== 'ready') { setNotice({ type: 'error', text: projectError || 'Ouvrez Configuration et attendez la détection réelle du projet avant d’enregistrer.' }); return }
      setBusy(true); setNotice(null)
      try {
        const saved = await saveRemoteProject({ ...form, ...remoteIds }, {
          onCreated: ids => { setRemoteIds(current => ({ ...current, ...ids })); setForm(current => ({ ...current, ...ids })) },
        })
        setRemoteIds({ backendProjectId: saved.projectId, backendEnvironmentId: saved.environmentId, configurationId: saved.configurationId })
        setForm(current => ({ ...current, backendProjectId: saved.projectId, backendEnvironmentId: saved.environmentId }))
        setServerPreview(null)
        setNotice({ type: 'success', text: 'Projet et configuration enregistrés sur AUTOKOM. Aucun déploiement lancé.' })
        return saved
      } catch (error) { setNotice({ type: 'error', text: errorText(error) }); return null }
      finally { setBusy(false) }
    }

    if (!form.repositorySlug) { setNotice({type:'error',text:'Choisissez d’abord un dépôt GitHub.'}); return }
    const entry = {...form,id:form.id || crypto.randomUUID(),updatedAt:new Date().toISOString()}
    const nextItems = [entry,...drafts.filter(d=>d.id!==entry.id)].slice(0,40)
    try { localStorage.setItem(KEY, JSON.stringify(nextItems)); setDrafts(nextItems); setForm(entry); setNotice({type:'success',text:'Projet enregistré dans ce navigateur.'}) }
    catch { setNotice({type:'error',text:'Impossible d’enregistrer ce projet.'}) }
  }
  const configuration = useMemo(() => ({ version:1, project:{name:form.projectName,environment:form.environment},source:{provider:'github',repository_id:form.githubRepositoryId,repository:form.repositorySlug,branch:form.branch,workdir:form.workdir},build,hosting:{offer:form.resourcePlan,pricing_plan:form.pricingPlan,billing_period:form.billingPeriod,resources,high_availability_requested:form.highAvailability,domain_mode:form.domainMode,domain:form.domain},pipeline:{mode:form.pipelineMode,auto_deploy_requested:form.autoDeploy,services:form.services},source_detection:analysis?{framework:analysis.framework,runtime:analysis.runtime,confidence:analysis.confidence}:null }),[form,analysis])
  function exportProject() { if (projectError) { setNotice({type:'error',text:projectError}); return } downloadJson(configuration,`${form.projectName.replace(/[^a-z0-9-_]/gi,'-') || 'autokom'}-config.json`);setNotice({type:'success',text:'Configuration téléchargée.'}) }
  function reset(){setForm({...INITIAL, ...planToDeployment(getPreferredPlan()), billingPeriod:getBillingPeriod(), ...(API_AVAILABLE ? {pipelineMode:'devops'} : {})});setStep(0);setAnalysis(null);setNotice(null);setRemoteIds(null);setServerPreview(null);launchKey.current=null}
  async function previewPipeline() {
    if (API_AVAILABLE) {
      if (serverPreview) { setNotice({ type: 'success', text: 'Prévisualisation déjà validée : vous pouvez lancer Jenkins.' }); return }
      if (busy || projectError) { if (projectError) setNotice({ type: 'error', text: projectError }); return }
      setBusy(true); setNotice(null)
      try {
        const saved = await saveRemoteProject({ ...form, ...remoteIds }, {
          onCreated: ids => { setRemoteIds(current => ({ ...current, ...ids })); setForm(current => ({ ...current, ...ids })) },
        })
        const ids = { projectId: saved.projectId, environmentId: saved.environmentId, configurationId: saved.configurationId }
        setRemoteIds({ backendProjectId: ids.projectId, backendEnvironmentId: ids.environmentId, configurationId: ids.configurationId })
        setForm(current => ({ ...current, backendProjectId: ids.projectId, backendEnvironmentId: ids.environmentId }))
        const preview = await prepareRemoteDeployment(ids)
        setServerPreview(preview)
        setNotice({ type: 'success', text: 'Configuration finalisée et prévisualisée par Django. Aucun déploiement lancé.' })
      } catch (error) { setNotice({ type: 'error', text: errorText(error) }) }
      finally { setBusy(false) }
      return
    }

    if (projectError) { setNotice({type:'error',text:projectError}); return }
    try {
      const entry = { ...form, id:form.id || crypto.randomUUID(), updatedAt:new Date().toISOString() }
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

  async function launchRemote() {
    if (!API_AVAILABLE || !serverPreview || busy || !remoteIds?.backendProjectId || !remoteIds?.backendEnvironmentId) return
    if (!window.confirm('Lancer réellement le pipeline Jenkins pour ce projet ?')) return
    const key = launchKey.current || crypto.randomUUID()
    launchKey.current = key
    setBusy(true); setNotice(null)
    try {
      const response = await deploymentsApi.launch(remoteIds.backendProjectId, remoteIds.backendEnvironmentId, key)
      setNotice({ type: 'success', text: `Déploiement ${response?.deployment?.id || ''} placé en file Jenkins.` })
      navigate(`/projects/${remoteIds.backendProjectId}`)
    } catch (error) { setNotice({ type: 'error', text: errorText(error) }) }
    finally { setBusy(false) }
  }

  return <>
    <section className="page-hero ak-deploy-hero"><div><h1>Déployer un projet</h1></div><button className="button-quiet new-project" onClick={reset} type="button"><Plus size={17}/> Nouveau projet</button></section>
    {notice && <div className={`feedback ${notice.type}`} role="status">{notice.text}</div>}
    {API_AVAILABLE && <p className="ak-simulation-disclosure">Mode connecté : les projets, configurations et déploiements sont gérés par Django. Les offres payantes et le mode DevSecOps nécessitent des droits actifs sur le backend.</p>}
    <div className="deploy-layout ak-quick-deploy"><div className="deploy-column">
      <div className="step-navigation ak-simple-steps" role="group" aria-label="Étapes">{STEPS.map((s,i)=><button type="button" key={s} className={`step-tab ${i===step?'current':''} ${i<step?'completed':''}`} onClick={()=>{if(i<=step)setStep(i)}}><span className="step-number">{i<step?<Check size={15}/>:i+1}</span><span>{s}</span></button>)}</div>
      <section className="cut-frame main-panel"><div className="cut-body">
        {step===0 && <><div className="panel-heading"><h2>Choisissez votre dépôt</h2></div><GitHubConnector form={form} update={data => {update(data);setAnalysis(null);setServerPreview(null)}} onNotice={setNotice} onConnectionChange={setGithubConnected}/>{form.githubRepositoryId && <label className="field ak-project-name"><span>Nom du projet</span><input value={form.projectName} onChange={e=>update({projectName:e.target.value})} placeholder="Nom de votre application"/></label>}</>}
        {step===1 && <><div className="panel-heading"><h2>Configuration</h2></div><div className={`ak-analysis-state state-${analysisStatus}`} role="status">{analysisStatus==='loading'?<LoaderCircle size={19} className="ak-spin"/>:<Info size={19}/>}<div><strong>{analysisStatus==='loading'?'Analyse du dépôt…':analysis ? `${analysis.framework} · ${analysis.runtime}` : API_AVAILABLE ? 'Configuration du dépôt réel' : 'Analyse du projet'}</strong><small>{analysis ? `Source : ${form.workdir} · Détection ${analysis.confidence === 'high' ? 'élevée' : 'indicative'}` : API_AVAILABLE ? (analysisError || 'Analyse GitHub du dossier source sélectionné, sans build préalable.') : analysisError || 'Lecture des fichiers GitHub.'}</small></div></div>
          {API_AVAILABLE && analysis && <p className="ak-simulation-disclosure">{analysis.applicationType === 'static' ? 'Projet frontend : sélectionnez le dossier contenant package.json et package-lock.json. Vite créera dist lors du build, sans le demander dans GitHub.' : 'Application serveur : un Dockerfile est actuellement nécessaire et les offres backend sont en préparation.'}</p>}
          {API_AVAILABLE && analysis?.applicationType === 'static' && analysis?.isVite && !form.hasNpmLockfile && <p className="feedback error" role="alert">Ajoutez package-lock.json à votre dossier source pour activer la compilation reproductible gratuite.</p>}
          {analysis?.warnings?.length>0 && <div className="ak-analysis-warnings">{analysis.warnings.map(w=><p key={w}><Info size={15}/>{w}</p>)}</div>}
          <div className="ak-offer-header"><h3>Offre d’hébergement</h3><button className="ak-see-prices" type="button" onClick={() => navigate('/pricing')}>Comparer les tarifs <ArrowRight size={15}/></button></div>
          <div className="ak-offers ak-offers-three">
            {PRICING_PLANS.map(plan => <button key={plan.id} type="button" aria-pressed={inferPricingPlan(form) === plan.id} onClick={() => choosePlan(plan.id)} disabled={API_AVAILABLE && plan.id !== 'free'} title={API_AVAILABLE && plan.id !== 'free' ? 'Offre non encore disponible dans le backend' : ''} className={`ak-offer-card ${(inferPricingPlan(form) === plan.id) ? 'selected' : ''}`}>
              <span className="ak-offer-top"><span className="ak-offer-name">{plan.name}</span><span className="ak-offer-tag">{plan.id === 'free' ? 'Gratuite' : 'Bientôt disponible'}</span></span>
              <strong>{plan.tagline}</strong>
              <small>{plan.servers}</small>
              <span className="ak-offer-price">{formatAr(form.billingPeriod === 'annual' ? plan.annual : plan.monthly)} / {form.billingPeriod === 'annual' ? 'an' : 'mois'}</span>
            </button>)}
          </div>
          {form.pricingPlan === 'pro' && <label className="ak-ha-choice"><span><strong>Haute disponibilité</strong><small>Deux serveurs distincts au maximum, sous réserve de disponibilité.</small></span><input type="checkbox" checked={form.highAvailability} onChange={e => update({highAvailability:e.target.checked})} aria-label="Activer la haute disponibilité"/></label>}
          <div className="ak-essentials"><label className="field"><span>Pipeline</span><select value={form.pipelineMode} onChange={e=>update({pipelineMode:e.target.value})}><option value="devsecops">DevSecOps</option><option value="devops">DevOps</option></select></label><label className="field"><span>Environnement</span><select value={form.environment} onChange={e=>update({environment:e.target.value})}><option value="development">Développement</option><option value="staging">Préproduction</option><option value="production">Production</option></select></label><label className="field"><span>Domaine</span><select value={form.domainMode} onChange={e=>update({domainMode:e.target.value})}><option value="autokom">Sous-domaine AUTOKOM</option><option value="custom">Domaine personnalisé</option></select></label>{form.domainMode==='custom'&&<label className="field"><span>Nom de domaine</span><input value={form.domain} onChange={e=>update({domain:e.target.value})} placeholder="app.exemple.com"/></label>}</div>
          <details className="ak-advanced-settings"><summary><Settings2 size={17}/> Paramètres avancés <span>facultatif</span></summary><div className="ak-advanced-grid">{BUILD_FIELDS.map(([key,label])=><label className="field" key={key}><span>{label}</span><input value={form.advancedBuild[key]??''} placeholder={analysis?.build?.[key] || 'Détection automatique'} onChange={e=>updateBuild(key,e.target.value)}/></label>)}</div><label className="ak-simple-checkbox"><input type="checkbox" checked={form.autoDeploy} onChange={e=>update({autoDeploy:e.target.checked})}/> Déploiement automatique sur push</label>{API_AVAILABLE && <p className="ak-simulation-disclosure">Les choix d’outils ci-dessous décrivent les préférences UI ; seuls les paramètres pris en charge par la configuration Django sont enregistrés. Le pipeline Jenkins applique ses contrôles de sécurité côté serveur.</p>}<ServiceCatalog services={form.services || DEFAULT_SERVICES} capabilities={[]} capabilitiesLoaded={false} onChange={(group, choice)=>update({services:{...(form.services || DEFAULT_SERVICES),[group]:choice}})}/></details>
        </>}
        {step===2 && <><div className="panel-heading"><h2>Votre projet</h2></div><div className="ak-review-list"><div><GitBranch size={17}/><span>Dépôt</span><strong>{form.repositorySlug}</strong></div><div><Code2 size={17}/><span>Branche et dossier</span><strong>{form.branch} · {form.workdir}</strong></div><div><Code2 size={17}/><span>Framework</span><strong>{analysis?.framework || 'Non déterminé'}</strong></div><div><Layers3 size={17}/><span>Offre</span><strong>{findPricingPlan(inferPricingPlan(form)).name}</strong></div><div><ShieldCheck size={17}/><span>Pipeline</span><strong>{form.pipelineMode==='devsecops'?'DevSecOps':'DevOps'}</strong></div><div><Server size={17}/><span>HA</span><strong>{form.highAvailability?'2 serveurs demandés':'1 serveur'}</strong></div></div><div className="ak-need-analysis"><Info size={18}/><span>{API_AVAILABLE ? 'Enregistrez puis prévisualisez avec Django. React/Vue/Svelte avec Vite et lockfile sont construits automatiquement sur l’offre gratuite ; les autres applications exigent un Dockerfile et les droits correspondants. Jenkins ne démarre que sur confirmation.' : 'Vérifiez votre configuration, puis parcourez les étapes du pipeline sans déployer de serveur.'}</span></div></>}
        <div className="form-actions ak-form-actions"><button type="button" className="button-quiet" onClick={save} disabled={busy}>{busy ? <LoaderCircle size={17} className="ak-spin"/> : <Save size={17}/>} {busy ? 'En cours…' : 'Enregistrer'}</button><div className="actions-right">{step>0&&<button className="button-outline" type="button" onClick={()=>setStep(s=>s-1)}><ArrowLeft size={16}/> Retour</button>}{step<2?<button className="button-primary" type="button" onClick={next} disabled={busy || (API_AVAILABLE && step === 0 && (!githubConnected || Boolean(projectError)))}>Continuer <ArrowRight size={16}/></button>:<><button type="button" className="button-outline" onClick={exportProject}><Download size={17}/> Exporter</button><button type="button" className="button-primary" onClick={previewPipeline} disabled={busy || (API_AVAILABLE && (analysisStatus !== 'ready' || !(analysis?.applicationType === 'static' && analysis.isVite && form.hasNpmLockfile && ['React','Vue','Svelte'].includes(analysis.framework))))}>{busy ? <LoaderCircle size={17} className="ak-spin"/> : <CloudUpload size={17}/>} {busy ? 'Vérification…' : 'Préparer le pipeline'} <ArrowRight size={16}/></button>{API_AVAILABLE && serverPreview && <button type="button" className="button-primary" onClick={launchRemote} disabled={busy}><CloudUpload size={17}/> Lancer réellement Jenkins <ArrowRight size={16}/></button>}</>}</div></div>
      </div></section>
    </div><aside className="deploy-aside"><div className="cut-frame side-panel"><div className="cut-body"><h3>{API_AVAILABLE ? 'Espace AUTOKOM' : 'Projets enregistrés'}</h3>{API_AVAILABLE && <p className="gh-muted">Les projets sont enregistrés dans Django/MySQL. Retrouvez-les dans Mes projets.</p>}<div className="ak-drafts-list">{!API_AVAILABLE && (drafts.length?drafts.map(d=><div className="ak-draft-item" key={d.id}><button type="button" onClick={()=>{setForm({...INITIAL,...d});setStep(0);setNotice(null)}}><strong>{d.projectName}</strong><small>{d.repositorySlug} · {d.branch}</small></button><button type="button" aria-label="Supprimer" onClick={()=>{if(confirm('Supprimer ce brouillon ?'))setDrafts(old=>old.filter(v=>v.id!==d.id))}}><Trash2 size={15}/></button></div>):<p className="gh-muted">Aucun projet enregistré.</p>)}</div></div></div></aside></div>
  </>
}
