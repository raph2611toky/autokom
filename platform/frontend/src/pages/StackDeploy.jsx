import { PLATFORM_PREPROD } from '../config/runtime.js'
import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, GitBranch, FolderGit2, Globe, Info, LoaderCircle, FileUp, Plus, Trash2, Save, CloudUpload, CheckCircle2, AlertTriangle, Layers3, X } from 'lucide-react'
import { getRemoteProjectDetail, errorText } from '../services/projectWorkflow.js'
import { gitApi } from '../services/projectsApi.js'
import { stackApi } from '../services/stackApi.js'
import { detectSource, frontendProfile, availableFrontendProfiles, BACKEND_PROFILES } from '../services/sourceAnalysis.js'
import { billingApi } from '../services/billingApi.js'
import { parseStackEnv, stackEnvPayload, publicEnvName, sensitiveEnvName, existingFrontendStackDefaults, existingServiceKind, stackOutputDirectory } from '../services/stackEnv.js'
import { stackHosts, frontendApiVariable, withoutManagedFields, MANAGED_BACKEND_ENV, MANAGED_FRONTEND_API, validFrontendApiKey, validBackendOriginKey } from '../services/stackDomains.js'
import '../styles/stack-deploy.css'
import StackPublicationProgress from '../components/StackPublicationProgress.jsx'

const INITIAL = { branch:'main', root_directory:'', profile:'', hostname:'', output_directory:'build', healthcheck:'/', api_env_key:'', origin_env_key:'', rows:[] }
const PROFILES = {
  frontend: [ ['vite','React/Vue/Svelte + Vite'], ['npm_static','React/Vue — build npm'], ['next_server','Next.js SSR'], ['next_export','Next.js statique'], ['html','HTML / CSS / JS'] ],
  backend: [ ['python_fastapi','FastAPI'], ['python_flask','Flask'], ['python_django','Django'], ['python_wsgi','Python WSGI'],
    ['node_express','Node Express'],['node_nest','Node NestJS'],['java_spring_maven','Spring Boot Maven'],
    ['java_spring_gradle','Spring Boot Gradle'],['php_plain','PHP'],['php_laravel','Laravel'],['php_symfony','Symfony'] ],
}
const SAFE_PATH = /^(?:[A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.-]+)*)?$/
function validPath(value) { return typeof value === 'string' && value.length <= 300 && SAFE_PATH.test(value) && !value.split('/').includes('..') }
function normalizeFolder(value) { return value.replace(/^\.\//,'').replace(/\/$/,'') }
function configLabel(profile) { return PROFILES.frontend.concat(PROFILES.backend).find(item => item[0] === profile)?.[1] || profile || 'Détection en attente' }

function Variables({kind, rows, onChange, managed, onManagedKeyChange}) {
  const inputRef = useRef(null)
  const [error,setError] = useState('')
  async function upload(event) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    try {
      if (file.size > 32768) throw new Error('Fichier .env trop volumineux.')
      const parsed = withoutManagedFields(parseStackEnv(await file.text(), kind),kind)
        .filter(row => !managed.some(field => field.key === row.key))
      const merged = new Map(rows.map(row => [row.key,row]))
      for (const row of parsed) merged.set(row.key,row)
      onChange([...merged.values()]); setError('')
    } catch (err) { setError(err.message || 'Import impossible.') }
  }
  function edit(index, data) {
    if (data.key && ([...(kind === 'backend' ? MANAGED_BACKEND_ENV : MANAGED_FRONTEND_API),...managed.map(field => field.key)]).includes(data.key)) {
      setError('Nom déjà réservé par la configuration automatique.')
      return
    }
    setError(''); onChange(rows.map((row,i) => index === i ? {...row,...data} : row))
  }
  return <section className="ak-stack-env">
    <div className="ak-stack-inline"><h3>Variables {kind === 'frontend' ? 'frontend publiques' : 'backend'}</h3>
      <input type="file" ref={inputRef} accept=".env,.txt,text/plain" hidden onChange={upload}/>
      <button type="button" className="button-outline" onClick={() => inputRef.current?.click()}><FileUp size={15}/> Importer .env</button>
      <button type="button" className="button-outline" onClick={() => onChange([...rows,{key:'',value:'',secret:kind === 'backend'}])}><Plus size={15}/> Ajouter</button>
    </div>
    {kind === 'frontend' && <p className="ak-stack-subtle"><Info size={14}/> Seules les variables publiques sont incorporées à votre JavaScript ; jamais de secret.</p>}
    {kind === 'backend' && <p className="ak-stack-subtle"><Info size={14}/> Les secrets sont chiffrés côté Django, masqués après enregistrement et injectés au démarrage du conteneur.</p>}
    {managed.map((item,index) => <div className="ak-stack-env-row ak-stack-env-managed" key={`managed-${index}`}>
      <input aria-label={`Nom variable automatique ${kind} ${index+1}`} value={item.key} readOnly={item.fixed}
        onChange={e => !item.fixed && onManagedKeyChange(e.target.value)} maxLength={100} spellCheck={false}/>
      <input aria-label={`Valeur automatique ${kind} ${index+1}`} value={item.value} readOnly aria-readonly="true"/>
      {kind === 'backend' && <span className="ak-stack-env-fixed-spacer"/>}
    </div>)}
    {rows.map((item,index) => <div className="ak-stack-env-row" key={index}>
      <input aria-label={`Nom variable ${kind} ${index+1}`} placeholder="CLE" value={item.key} maxLength={160}
        onChange={e=>edit(index,{key:e.target.value,secret:kind==='backend'&&sensitiveEnvName(e.target.value)})}/>
      <input aria-label={`Valeur variable ${kind} ${index+1}`} placeholder="Valeur" value={item.value}
        type={item.secret ? 'password':'text'} onChange={e=>edit(index,{value:e.target.value})}/>
      {kind === 'backend' && <label className="ak-stack-secret"><input type="checkbox" checked={Boolean(item.secret)} onChange={e=>edit(index,{secret:e.target.checked})}/> Secret</label>}
      <button type="button" className="button-outline" aria-label="Supprimer variable" onClick={()=>onChange(rows.filter((_,i)=>i!==index))}><Trash2 size={15}/></button>
    </div>)}
    {error && <div className="feedback error ak-stack-dismissible" role="alert"><span>{error}</span>
      <button type="button" className="ak-stack-dismiss" aria-label="Fermer l’erreur .env" onClick={()=>setError('')}><X size={17}/></button></div>}
  </section>
}

function ComponentCard({kind,data,onChange,branches,probe,host,apiHost}) {
  const label = kind === 'frontend' ? 'Frontend' : 'Backend'
  const choices = PROFILES[kind]
  const bad = probe.status === 'error'
  const managed = kind === 'frontend'
    ? [frontendApiVariable(data.profile,apiHost,data.api_env_key)]
    : [{key:'PORT',value:'8000',fixed:true},{key:data.origin_env_key || 'FRONTEND_ORIGIN',value:`https://${apiHost}`}]
  const currentKey = kind === 'frontend' ? managed[0].key : managed[1].key
  const invalidKey = kind === 'frontend' ? !validFrontendApiKey(data.profile,currentKey) : !validBackendOriginKey(currentKey)
  return <section className="ak-stack-component" aria-label={`Configuration ${label}`}>
    <div className="ak-stack-component-head"><h2>{label}</h2><span className={`ak-stack-state ${probe.status}`}>
      {probe.status === 'loading' ? <LoaderCircle className="ak-spin" size={15}/> : probe.status === 'ready' ? <CheckCircle2 size={15}/> : bad ? <AlertTriangle size={15}/> : <Info size={15}/>}
      {probe.status === 'ready' ? probe.analysis?.framework : probe.status === 'loading' ? 'Analyse GitHub…' : bad ? 'Source invalide' : 'À analyser'}
    </span></div>
    <div className="ak-stack-fields"><label><span><GitBranch size={14}/> Branche {label}</span>
      <select value={data.branch} onChange={e=>onChange({...data,branch:e.target.value,profile:''})}>
        {branches.length ? branches.map(name=><option key={name} value={name}>{name}</option>) : <option value={data.branch}>{data.branch}</option>}
      </select></label>
      <label><span><FolderGit2 size={14}/> Dossier source</span>
        <input aria-invalid={bad || !validPath(normalizeFolder(data.root_directory))} className={bad?'ak-stack-shake':''}
          value={data.root_directory} onChange={e=>onChange({...data,root_directory:e.target.value,profile:''})} placeholder={kind==='frontend'?'frontend':'backend'}/>
      </label></div>
    {probe.message && <p className={bad?'feedback error':'ak-stack-subtle'}>{probe.message}</p>}
    <label><span>Recette de déploiement</span><select value={data.profile} onChange={e=>{
      const nextProfile=e.target.value
      const previousDefault=frontendApiVariable(data.profile,apiHost).key
      onChange({...data,profile:nextProfile,
        output_directory:stackOutputDirectory(nextProfile,data.output_directory,kind),
        ...(kind==='frontend' && (!data.api_env_key || data.api_env_key===previousDefault) ? {api_env_key:''} : {})})
    }}>
      <option value="">Choisir un profil</option>
      {choices.map(([id,text])=><option key={id} value={id}>{text}</option>)}
    </select></label>
    {kind === 'frontend' && data.profile === 'npm_static' && <label><span>Dossier de sortie de compilation</span><select value={data.output_directory} onChange={e=>onChange({...data,output_directory:e.target.value})}><option value="build">build (React classique)</option><option value="dist">dist (Vue CLI)</option></select></label>}
    <p className="ak-stack-subtle">{probe.analysis ? `Détecté : ${probe.analysis.framework} · ${probe.analysis.runtime}` : 'Sélectionnez une branche et un dossier existant.'} Les commandes approuvées sont choisies automatiquement.</p>
    <section className="ak-stack-domain">
      <label><span><Globe size={14}/> Domaine HTTPS {label}</span>
        <input aria-label={`Domaine ${label}`} value={host} spellCheck={false}
          onChange={e=>onChange({...data,hostname:e.target.value})} placeholder={kind==='backend'?'backend.votre-projet.auto.ikom.mg':'votre-projet.auto.ikom.mg'}/>
      </label>
    </section>
    <label><span>Chemin de vérification HTTP</span><input value={data.healthcheck} onChange={e=>onChange({...data,healthcheck:e.target.value})} placeholder="/"/></label>
    <Variables kind={kind} rows={data.rows} managed={managed}
      onManagedKeyChange={key=>onChange({...data,[kind==='frontend'?'api_env_key':'origin_env_key']:key})}
      onChange={rows=>onChange({...data,rows})}/>
    {invalidKey && <p className="feedback error" role="alert">Nom de variable automatique invalide pour ce profil.</p>}
    {data.rows.some(row=>row.key===currentKey) && <p className="feedback error" role="alert">Ce nom est déjà utilisé dans les variables personnalisées.</p>}
    {kind==='backend' && data.rows.some(row=>row.key==='DB_PATH' && /^\.\/?/.test(row.value||'')) && <p className="ak-stack-warning"><AlertTriangle size={15}/> DB_PATH relatif : le conteneur est en lecture seule. Prévoir par exemple /tmp/app.db (données éphémères) ou un stockage persistant externe.</p>}
  </section>
}

export default function StackDeploy({navigate,projectId}) {
  const [record,setRecord] = useState(null)
  const [loading,setLoading] = useState(true)
  const [branches,setBranches] = useState([])
  const [step,setStep] = useState(0)
  const [frontend,setFrontend] = useState(INITIAL)
  const [backend,setBackend] = useState(INITIAL)
  const [auto,setAuto] = useState(true)
  const [highAvailability,setHighAvailability] = useState(false)
  const [accountPlan,setAccountPlan] = useState(null)
  const [probe,setProbe] = useState({frontend:{status:'idle'},backend:{status:'idle'}})
  const [busy,setBusy] = useState(false)
  const [error,setError] = useState('')
  const [notice,setNotice] = useState('')
  const [deployment,setDeployment] = useState(null)
  const [dnsProof,setDnsProof] = useState(null)
  const [ingressIPv4,setIngressIPv4] = useState('')
  const idem = useRef(null)
  const install = record?.project?.github_installation_id
  const repo = record?.project?.repository_external_id

  useEffect(() => {
    let active = true
    Promise.all([getRemoteProjectDetail(projectId),stackApi.detail(projectId),billingApi.account()])
      .then(async ([detail,stackResponse,accountResponse])=>{
        if (!active) return
        setRecord(detail)
        setAccountPlan(accountResponse?.subscription || null)
        const singleService = existingFrontendStackDefaults(detail)
        const primaryKind = existingServiceKind(detail)
        const branch = singleService.branch
        const existing = stackResponse?.stack
        const asForm = (data, fallback, kind) => ({...INITIAL,branch:data?.branch||fallback,root_directory:data?.root_directory||'',hostname:data?.domain||'',
          profile:data?.profile||'',output_directory:stackOutputDirectory(data?.profile,data?.output_directory,kind),healthcheck:data?.healthcheck||'/',
          api_env_key:kind==='frontend'?(data?.api_env_key||''):'',origin_env_key:kind==='backend'?(data?.origin_env_key||''):'',
          rows:withoutManagedFields((data?.variables||[]).map(x=>({key:x.key,value:x.value,secret:false})),kind)
            .filter(row=>row.key !== (kind==='frontend'?data?.api_env_key:data?.origin_env_key))})
        setFrontend(asForm(existing?.frontend || (primaryKind==='frontend' ? singleService : null),branch,'frontend'))
        setBackend(asForm(existing?.backend || (primaryKind==='backend' ? singleService : null),branch,'backend'))
        setAuto(existing ? Boolean(existing.auto_deploy) : Boolean(detail.configurations?.find(item => !item.is_draft)?.auto_deploy))
        if (PLATFORM_PREPROD) setHighAvailability(false)
        else setHighAvailability(Boolean(existing?.high_availability_requested) && Boolean(accountResponse?.subscription?.ha_available))
        if (detail.project.github_installation_id && detail.project.repository_external_id) {
          try {
            const result = await gitApi.branches(detail.project.github_installation_id,detail.project.repository_external_id)
            if (active) setBranches((result.branches||[]).map(b=>b.name))
          } catch { /* Existing branch remains selectable. */ }
        }
      }).catch(err=>{if(active)setError(errorText(err))}).finally(()=>{if(active)setLoading(false)})
    return()=>{active=false}
  },[projectId])

  useEffect(()=>{
    let active=true
    stackApi.domainSettings(projectId).then(data=>{ if(active)setIngressIPv4(data.ingress_ipv4||'') }).catch(()=>{})
    return()=>{active=false}
  },[projectId])

  useEffect(()=>{
    if (!install || !repo) return
    let active = true
    const timer = window.setTimeout(()=>{
      for (const [kind,data] of [['frontend',frontend],['backend',backend]]) {
        const folder = normalizeFolder(data.root_directory)
        if (!validPath(folder) || !data.branch) {
          setProbe(old=>({...old,[kind]:{status:'error',message:'Dossier source ou branche invalide.'}}));continue
        }
        setProbe(old=>({...old,[kind]:{status:'loading'}}))
        gitApi.directories(install,repo,data.branch,folder).then(result=>{
          if (!active)return
          const analysis = detectSource(result.files||[],result.package||null)
          const allowed = kind==='frontend' ? PROFILES.frontend.map(x=>x[0]) : BACKEND_PROFILES
          const guessed = frontendProfile(analysis)
          setProbe(old=>({...old,[kind]:{status:'ready',analysis}}))
          if (allowed.includes(guessed)) {
            const apply = kind==='frontend' ? setFrontend : setBackend
            apply(old=>old.profile ? old : {...old,profile:guessed,
              output_directory:stackOutputDirectory(guessed,analysis.framework==='Vue'?'dist':'build',kind)})
          }
        }).catch(err=>{if(active)setProbe(old=>({...old,[kind]:{status:'error',message:errorText(err)}}))})
      }
    },450)
    return()=>{active=false;window.clearTimeout(timer)}
  },[install,repo,frontend.branch,frontend.root_directory,backend.branch,backend.root_directory])

  const readySource = Boolean(probe.frontend.status==='ready' && probe.backend.status==='ready' &&
    PROFILES.frontend.some(([key])=>key===frontend.profile) && PROFILES.backend.some(([key])=>key===backend.profile) &&
    validPath(normalizeFolder(frontend.root_directory)) && validPath(normalizeFolder(backend.root_directory)))
  // The selected domain determines the locked .env values. DNS ownership and
  // real DNS validation remain mandatory and are enforced on the server.
  const defaultHost = record?.domains?.find(domain => (domain.domain_type||domain.type)==='SUBDOMAIN')?.hostname ||
    record?.project?.public_site?.hostname || `${record?.project?.slug||'app'}.auto.ikom.mg`
  const hosts = stackHosts(frontend.hostname, backend.hostname, defaultHost)
  const frontHost = hosts.frontend
  const backendHost = hosts.backend
  const frontApiKey = frontendApiVariable(frontend.profile,backendHost,frontend.api_env_key).key
  const backOriginKey = backend.origin_env_key || 'FRONTEND_ORIGIN'
  const reservedByUser = (frontend.rows || []).some(row=>row.key===frontApiKey) ||
    (backend.rows || []).some(row=>row.key===backOriginKey)
  const ready = readySource && hosts.valid && validFrontendApiKey(frontend.profile,frontApiKey) &&
    validBackendOriginKey(backOriginKey) && !reservedByUser
  function componentPayload(data,kind) {
    return {branch:data.branch,root_directory:normalizeFolder(data.root_directory),profile:data.profile,
      hostname:kind==='frontend'?frontHost:backendHost,
      ...(kind==='frontend'?{api_env_key:frontApiKey}:{origin_env_key:backOriginKey}),
      output_directory:stackOutputDirectory(data.profile,data.output_directory,kind),healthcheck:data.healthcheck,...stackEnvPayload(data.rows,kind)}
  }
  async function publish() {
    if (busy || !ready)return
    setBusy(true);setError('');setNotice('')
    try {
      const payload={frontend:componentPayload(frontend,'frontend'),backend:componentPayload(backend,'backend'),auto_deploy:auto,high_availability_requested:highAvailability}
      const resultConfig = await stackApi.configure(projectId,payload)
      setDnsProof(resultConfig?.stack || null)
      setNotice('Contrôle des domaines et de leur pointage DNS avant construction…')
      await stackApi.preflightDomains(projectId)
      const key=idem.current || crypto.randomUUID()
      idem.current=key
      const result=await stackApi.launch(projectId,key)
      if (!result?.stack_deployment?.id)throw new Error('Aucune publication groupée confirmée.')
      idem.current=null
      setDeployment({...result.stack_deployment,status:result.stack_deployment.status})
      setStep(2)
      setNotice('')
    } catch(err){setError(errorText(err))} finally{setBusy(false)}
  }

  if (loading)return <section role="status" className="ak-workspace-empty"><LoaderCircle size={18} className="ak-spin"/> Chargement du projet et des branches GitHub…</section>
  if (!record)return <section className="ak-workspace-empty" role="alert">{error||'Projet non disponible.'}</section>
  return <div className="ak-stack-page">
    <div className="ak-back-line"><button type="button" className="button-quiet" onClick={()=>navigate(`/projects/${projectId}`)}><ArrowLeft size={16}/> Retour au projet</button></div>
    <section className="page-hero ak-workspace-hero"><div><h1>Configuration des services</h1><p>{record.project.repository_name} · dépôt GitHub conservé · une offre commune.</p></div></section>
    <div className="ak-stack-progress"><span className={step===0?'active':''}>1 · Configuration</span><span className={step===1?'active':''}>2 · Récapitulatif</span><span className={step===2?'active':''}>3 · Publication</span></div>
    {error && <div className="feedback error ak-stack-dismissible" role="alert"><span>{error}</span>
      <button type="button" className="ak-stack-dismiss" aria-label="Fermer la notification d’erreur" onClick={()=>setError('')}><X size={17}/></button></div>}
    {notice && <div className="feedback success ak-stack-dismissible" role="status"><span>{notice}</span>
      <button type="button" className="ak-stack-dismiss" aria-label="Fermer la notification d’information" onClick={()=>setNotice('')}><X size={17}/></button></div>}
    {dnsProof && error && <div className="ak-stack-warning" role="alert">
      <AlertTriangle size={17}/> Vérifiez les enregistrements DNS : A vers l’IP publique {ingressIPv4 || 'de votre serveur'}. Le domaine externe nécessite aussi une preuve TXT.
      {[dnsProof.frontend,dnsProof.backend].filter(item=>item?.verification?.type==='TXT').map((item,index)=>
        <code key={index}>{item.verification.hostname} : {item.verification.value}</code>)}
    </div>}
    {step===0 && <>
      <div className="ak-stack-grid"><ComponentCard kind="frontend" data={frontend} onChange={setFrontend} branches={branches} probe={probe.frontend} host={frontHost} apiHost={backendHost}/>
      <ComponentCard kind="backend" data={backend} onChange={setBackend} branches={branches} probe={probe.backend} host={backendHost} apiHost={frontHost}/></div>
      <section className="ak-stack-component ak-stack-common-hosting" aria-label="Offre d'hébergement commune">
        <div className="ak-stack-inline"><h2>Offre d’hébergement commune</h2><span className="ak-stack-common-tag"><Layers3 size={14}/> Une seule offre pour deux services</span></div>
        <div className="ak-account-offer" role="group" aria-label="Abonnement du compte en lecture seule">
          <div><strong>{accountPlan?.plan_name || 'Découverte'}</strong><small>Formule actuelle de votre compte · {accountPlan?.max_instances || 1} instance(s) autorisée(s)</small></div>
          <button type="button" className="button-outline" onClick={() => navigate('/account')}>Mon abonnement <ArrowRight size={15}/></button>
        </div>
        <p className="ak-stack-subtle"><Info size={15}/> Les ressources sont réparties entre les deux conteneurs. La validation des quotas appartient au backend AUTOKOM ; aucune seconde offre n’est ajoutée automatiquement.</p>
      </section>
      <section className="ak-stack-component ak-stack-options"><label><input type="checkbox" checked={auto} onChange={e=>setAuto(e.target.checked)}/> Déployer automatiquement après un push sur l'une des deux branches</label><label><input type="checkbox" checked={highAvailability} disabled={!accountPlan?.ha_available || PLATFORM_PREPROD} onChange={e=>setHighAvailability(e.target.checked)}/> Demander la haute disponibilité pour le frontend et le backend</label><p className="ak-stack-subtle">La redondance doit être autorisée pour chacun des deux environnements et exécutée sur deux workers GCP. Aucune activation locale fictive. </p><p className="ak-stack-subtle">Le nouveau mode conserve la dernière version saine des deux services jusqu'à la validation du nouveau couple.</p></section>
      <div className="ak-stack-footer"><button type="button" className="button-primary" disabled={!ready} onClick={()=>setStep(1)}>Continuer <ArrowRight size={16}/></button></div>
    </>}
    {step===1 && <section className="ak-stack-component"><h2>Vérification avant publication</h2>
      <div className="ak-stack-review"><div><strong>Frontend</strong><span>{frontend.branch} · /{normalizeFolder(frontend.root_directory)}</span><span>{configLabel(frontend.profile)}</span><span><Globe size={14}/> https://{frontHost}</span></div>
      <div><strong>Backend</strong><span>{backend.branch} · /{normalizeFolder(backend.root_directory)}</span><span>{configLabel(backend.profile)}</span><span><Globe size={14}/> https://{backendHost}</span></div></div>
      <p className="ak-stack-subtle"><Info size={15}/> Le navigateur peut accéder à l'API par /__autokom_backend/ sur le domaine frontend. Les appels serveur utilisent BACKEND_INTERNAL_URL sur le réseau Docker privé. Les secrets backend ne sont jamais incorporés au build frontend.</p>
      <p className="ak-stack-subtle"><Layers3 size={15}/> Offre d’hébergement commune : ressources du projet existant, réparties entre frontend et backend.</p>
      <p className="ak-stack-subtle"><AlertTriangle size={15}/> Les deux services partagent les ressources du plan actif. Le runtime refusera de réserver plus que les quotas autorisés.</p>
      <div className="ak-stack-footer"><button type="button" className="button-outline" disabled={busy} onClick={()=>setStep(0)}><ArrowLeft size={15}/> Modifier</button>
        <button type="button" className="button-primary" disabled={busy||!ready} onClick={publish}>{busy?<LoaderCircle size={16} className="ak-spin"/>:<CloudUpload size={16}/>} Enregistrer et déployer</button></div>
    </section>}
    {step===2 && deployment?.id && <StackPublicationProgress projectId={projectId} runId={deployment.id} navigate={navigate}/>}
  </div>
}
