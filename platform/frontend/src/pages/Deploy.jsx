import { useEffect, useState } from 'react'
import {
  ArrowRight, ArrowLeft, Check, CheckCircle2, ChevronDown, CloudUpload,
  Code2, ExternalLink, FileCode2, GitBranch, Globe2, Info, LockKeyhole,
  Plus, RefreshCw, Save, Settings2, ShieldCheck, Trash2, Workflow,
} from 'lucide-react'

const STORAGE_KEY = 'autokom.deploy.drafts.v3'
const SOURCE_PROVIDERS = ['GitHub', 'GitLab', 'Bitbucket', 'Git distant']
const CICD_PROVIDERS = ['Jenkins', 'GitLab CI', 'GitHub Actions', 'Autre outil']
const STEPS = ['Source du projet', 'Construction', 'Publication', 'Sécurité & automatisation']
const DEFAULT = {
  id: '', projectName: '', sourceProvider: 'GitHub', ciProvider: '', repository: '',
  branch: 'main', applicationType: 'frontend', framework: 'auto', workdir: './',
  packageManager: 'auto', installCommand: '', buildCommand: '', testCommand: '',
  outputDir: '', dockerfile: 'Dockerfile', startCommand: '', appPort: '',
  deploymentMode: 'auto', target: '', environment: 'production', domain: '',
  basePath: '/', publicUrl: '', healthPath: '/health', https: true,
  deploymentStrategy: 'rolling', autoRollback: true, previewPR: false,
  onPush: true, onMerge: false, manual: true, failOnSecurity: true,
  sast: true, sca: true, secretScan: true, imageScan: false, dast: false,
  envNames: '', notes: '', updatedAt: '',
}

function readDrafts() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')
    return Array.isArray(saved) ? saved.filter((value) => value && typeof value === 'object') : []
  } catch { return [] }
}

function TextField({ label, hint, full, ...props }) {
  return <label className={`field ${full ? 'full' : ''}`}><span>{label}</span><input {...props}/>{hint && <small>{hint}</small>}</label>
}

function SelectField({ label, children, full, ...props }) {
  return <label className={`field ${full ? 'full' : ''}`}><span>{label}</span><span className="select-wrap"><select {...props}>{children}</select><ChevronDown size={16}/></span></label>
}

function Toggle({ title, description, checked, onChange }) {
  return <label className="toggle-option"><span><strong>{title}</strong><small>{description}</small></span><span className="toggle-switch"><input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)}/><i aria-hidden="true" /></span></label>
}

function validateStep(step, form) {
  if (step === 0) {
    if (!form.projectName.trim()) return 'Indiquez un nom pour ce projet.'
    if (!form.repository.trim()) return 'Sélectionnez un dépôt ou renseignez son URL.'
    if (!/^(https?:\/\/|ssh:\/\/|git@)[^\s]+$/.test(form.repository.trim())) return 'L’adresse du dépôt doit être une URL Git HTTPS ou SSH valide.'
    if (!form.branch.trim()) return 'Indiquez la branche à suivre.'
    if (!form.ciProvider) return 'Choisissez l’outil CI/CD que l’administrateur utilisera.'
  }
  if (step === 1) {
    if (!form.workdir.trim()) return 'Le répertoire du projet est obligatoire.'
    if (form.applicationType !== 'static' && form.applicationType !== 'container' && form.appPort && !/^\d+$/.test(form.appPort)) return 'Le port doit être un entier.'
    if (form.applicationType === 'frontend' && !form.outputDir.trim()) return 'Indiquez le dossier de sortie de votre frontend (ex. dist).'
  }
  if (step === 2 && form.domain.trim() && !/^(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}$/.test(form.domain.trim())) return 'Le domaine doit être un nom DNS valide, sans protocole ni chemin.'
  return ''
}

export default function Deploy() {
  const [step, setStep] = useState(0)
  const [form, setForm] = useState(DEFAULT)
  const [drafts, setDrafts] = useState(readDrafts)
  const [notice, setNotice] = useState(null)
  const update = (key, value) => setForm((state) => ({ ...state, [key]: value }))

  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(drafts)) }
    catch { /* The save handler still reports outcomes; no credentials are retained. */ }
  }, [drafts])

  function connectProvider() {
    setNotice({ type: 'info', text: 'La connexion au fournisseur sera disponible après intégration.' })
  }

  function move(next) {
    if (next > step) {
      const error = validateStep(step, form)
      if (error) { setNotice({ type: 'error', text: error }); return }
    }
    setStep(Math.max(0, Math.min(3, next)))
    setNotice(null)
    window.scrollTo({ top: 160, behavior: 'smooth' })
  }

  function saveDraft() {
    const id = form.id || (globalThis.crypto?.randomUUID?.() || `draft-${Date.now()}`)
    const record = { ...form, id, updatedAt: new Date().toISOString() }
    try {
      const next = [record, ...drafts.filter((item) => item.id !== id)]
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
      setDrafts(next)
      setForm(record)
      setNotice({ type: 'success', text: 'Configuration enregistrée localement, sans déploiement ni création de pipeline.' })
    } catch {
      setNotice({ type: 'error', text: 'Stockage local indisponible : le brouillon n’a pas été sauvegardé.' })
    }
  }

  function clearForm() { setForm({ ...DEFAULT }); setStep(0); setNotice(null) }
  function loadDraft(draft) { setForm({ ...DEFAULT, ...draft }); setStep(0); setNotice(null) }
  function removeDraft(id) {
    const selected = drafts.find((item) => item.id === id)
    if (!selected || !window.confirm(`Supprimer le brouillon « ${selected.projectName || 'Sans nom'} » ?`)) return
    const next = drafts.filter((item) => item.id !== id)
    setDrafts(next)
    if (form.id === id) clearForm()
  }
  function exportConfig() {
    for (let i = 0; i < 4; i++) {
      const error = validateStep(i, form)
      if (error) { setStep(i); setNotice({ type: 'error', text: error }); return }
    }
    const { id, updatedAt, ...configuration } = form
    // Never export tokens, credentials, or secret values (only env variable names).
    const blob = new Blob([JSON.stringify({ schemaVersion: 3, kind: 'autokom.deployment.config', configuration }, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${form.projectName.trim().replace(/[^a-z0-9_-]/gi, '-').toLowerCase() || 'autokom'}-deployment.json`
    anchor.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 500)
    setNotice({ type: 'success', text: 'Configuration exportée en JSON. Aucun secret ni identifiant n’est inclus.' })
  }

  const autoEvents = [form.onPush && 'Push', form.onMerge && 'Merge', form.previewPR && 'Pull request', form.manual && 'Manuel'].filter(Boolean)

  return (
    <>
      <section className="page-hero">
        <div><div className="eyebrow"><span className="short-stroke"/> AUTOMATISATION / DÉPLOIEMENT</div><h1>Un projet, <em>prêt à déployer.</em></h1><p>Choisissez votre fournisseur, définissez votre application et préparez les règles d’automatisation. Enregistrez vos paramètres ; l’exécution sera intégrée ultérieurement.</p></div>
        <button type="button" className="button-quiet new-project" onClick={clearForm}><Plus size={17}/> Nouveau projet</button>
      </section>
      {notice && <div role={notice.type === 'error' ? 'alert' : 'status'} className={`feedback ${notice.type}`}>{notice.text}</div>}
      <div className="deploy-layout">
        <div className="deploy-column">
          <div className="step-navigation" role="tablist" aria-label="Étapes de configuration">
            {STEPS.map((title, i) => <button key={title} className={`step-tab ${step === i ? 'current' : ''} ${step > i ? 'completed' : ''}`} type="button" role="tab" aria-selected={step === i} onClick={() => { setStep(i); setNotice(null) }}><span className="step-number">{step > i ? <Check size={14}/> : `0${i + 1}`}</span><span>{title}</span></button>)}
          </div>
          <div className="cut-frame main-panel"><div className="cut-body">
            {step === 0 && <>
              <div className="panel-heading"><span className="eyebrow">01 / SOURCES</span><h2>Quel projet déployer ?</h2><p>Choisissez librement votre fournisseur de code et votre outil CI/CD.</p></div>
              <div className="form-grid">
                <TextField label="Nom du projet *" placeholder="Ex. portail-client" value={form.projectName} onChange={(e) => update('projectName', e.target.value)}/>
                <SelectField label="Type d’application" value={form.applicationType} onChange={(e) => update('applicationType', e.target.value)}>
                  <option value="frontend">Frontend / SPA</option><option value="backend">Backend / API</option><option value="fullstack">Full-stack</option><option value="static">Site statique</option><option value="container">Application conteneurisée</option><option value="worker">Service / Worker</option>
                </SelectField>
                <SelectField label="Fournisseur de dépôt" value={form.sourceProvider} onChange={(e) => { update('sourceProvider', e.target.value); update('repository', '') }}>
                  {SOURCE_PROVIDERS.map((item) => <option key={item}>{item}</option>)}
                </SelectField>
                <SelectField label="Orchestrateur CI/CD *" value={form.ciProvider} onChange={(e) => update('ciProvider', e.target.value)}>
                  <option value="">Choisir un outil…</option>{CICD_PROVIDERS.map((item) => <option key={item}>{item}</option>)}
                </SelectField>
              </div>
              <div className="source-connect"><div className="source-avatar"><GitBranch size={23}/></div><div><strong>{form.sourceProvider === 'Git distant' ? 'Dépôt Git manuel' : `Connexion ${form.sourceProvider}`}</strong><span>{form.sourceProvider === 'Git distant' ? 'Saisissez une URL Git manuellement.' : 'Connexion à configurer ultérieurement.'}</span></div>{form.sourceProvider !== 'Git distant' && <button className="button-outline" type="button" onClick={connectProvider}>Se connecter<ExternalLink size={15}/></button>}</div>
              <div className="form-grid">
                <TextField full label="URL du dépôt Git *" placeholder="https://git.example.org/equipe/projet.git" value={form.repository} onChange={(e) => update('repository', e.target.value)} hint="Saisie manuelle possible ; aucun accès à votre compte sans autorisation."/>
                <TextField label="Branche de déploiement *" placeholder="main" value={form.branch} onChange={(e) => update('branch', e.target.value)}/>
                <SelectField label="Framework" value={form.framework} onChange={(e) => update('framework', e.target.value)}><option value="auto">Détection automatique</option><option value="react">React / Vite</option><option value="next">Next.js</option><option value="vue">Vue / Nuxt</option><option value="angular">Angular</option><option value="django">Django</option><option value="fastapi">FastAPI</option><option value="spring">Spring Boot</option><option value="node">Node.js / Express</option><option value="laravel">Laravel</option><option value="other">Autre / manuel</option></SelectField>
              </div>
            </>}
            {step === 1 && <>
              <div className="panel-heading"><span className="eyebrow">02 / BUILD</span><h2>Construction & exécution</h2><p>Définissez les commandes selon le framework, sans imposer un outil unique.</p></div>
              <div className="form-grid">
                <TextField label="Répertoire source *" value={form.workdir} onChange={(e) => update('workdir', e.target.value)} placeholder="./" hint="Supporte les monorepos (ex. apps/frontend)."/>
                <SelectField label="Gestionnaire de dépendances" value={form.packageManager} onChange={(e) => update('packageManager', e.target.value)}><option value="auto">Détection automatique</option><option value="npm">npm</option><option value="pnpm">pnpm</option><option value="yarn">yarn</option><option value="pip">pip / uv</option><option value="maven">Maven</option><option value="gradle">Gradle</option><option value="composer">Composer</option><option value="custom">Autre</option></SelectField>
                <TextField label="Commande d’installation" value={form.installCommand} onChange={(e) => update('installCommand', e.target.value)} placeholder="Ex. npm ci" />
                <TextField label="Commande de build" value={form.buildCommand} onChange={(e) => update('buildCommand', e.target.value)} placeholder="Ex. npm run build" />
                <TextField label="Commande de tests" value={form.testCommand} onChange={(e) => update('testCommand', e.target.value)} placeholder="Ex. npm test -- --run" />
                <TextField label="Répertoire de sortie" value={form.outputDir} onChange={(e) => update('outputDir', e.target.value)} placeholder="Ex. dist, build, .next" hint={form.applicationType === 'frontend' ? 'Obligatoire pour un frontend.' : 'Peut rester vide pour les API et conteneurs.'}/>
                <TextField label="Commande de lancement" value={form.startCommand} onChange={(e) => update('startCommand', e.target.value)} placeholder="Ex. gunicorn config.wsgi:application" />
                <TextField label="Port interne" inputMode="numeric" value={form.appPort} onChange={(e) => update('appPort', e.target.value)} placeholder="Ex. 8000" />
                <TextField label="Chemin Dockerfile" value={form.dockerfile} onChange={(e) => update('dockerfile', e.target.value)} placeholder="Dockerfile" hint="Pris en compte si la stratégie utilise Docker." />
                <SelectField label="Méthode d’exécution" value={form.deploymentMode} onChange={(e) => update('deploymentMode', e.target.value)}><option value="auto">Recommandation automatique</option><option value="static">Fichiers statiques via Nginx</option><option value="docker">Conteneur Docker</option><option value="process">Service applicatif</option><option value="custom">Commande personnalisée</option></SelectField>
              </div>
              <div className="info-strip"><Code2 size={19}/><span>Les commandes sont stockées comme paramètres. Le navigateur ne les exécute pas. Elles devront être validées par le serveur avant utilisation.</span></div>
            </>}
            {step === 2 && <>
              <div className="panel-heading"><span className="eyebrow">03 / PUBLICATION</span><h2>Où publier votre application ?</h2><p>Définissez la destination, le domaine, le routage et la surveillance de santé.</p></div>
              <div className="form-grid">
                <SelectField label="Environnement" value={form.environment} onChange={(e) => update('environment', e.target.value)}><option value="production">Production</option><option value="staging">Staging</option><option value="development">Développement</option></SelectField>
                <TextField label="Cible d’infrastructure" value={form.target} onChange={(e) => update('target', e.target.value)} placeholder="Alias de serveur / environnement" hint="Les cibles détectées seront sélectionnables après synchronisation."/>
                <TextField label="Domaine personnalisé" value={form.domain} onChange={(e) => update('domain', e.target.value)} placeholder="app.exemple.com"/>
                <TextField label="Chemin public" value={form.basePath} onChange={(e) => update('basePath', e.target.value)} placeholder="/"/>
                <TextField label="Chemin de vérification santé" value={form.healthPath} onChange={(e) => update('healthPath', e.target.value)} placeholder="/health"/>
                <SelectField label="Stratégie de déploiement" value={form.deploymentStrategy} onChange={(e) => update('deploymentStrategy', e.target.value)}><option value="rolling">Mise à jour progressive</option><option value="recreate">Remplacement contrôlé</option><option value="blue-green">Blue / Green (si pris en charge)</option></SelectField>
              </div>
              <div className="toggle-group">
                <Toggle title="HTTPS automatique" description="Certificat TLS et redirection, selon la configuration du serveur." checked={form.https} onChange={(value) => update('https', value)}/>
                <Toggle title="Retour arrière si échec" description="Restauration demandée si les contrôles après déploiement échouent." checked={form.autoRollback} onChange={(value) => update('autoRollback', value)}/>
              </div>
              <label className="field full"><span>Noms des variables d’environnement (sans valeurs ni secrets)</span><textarea rows="3" placeholder={'DATABASE_URL\nAPI_PUBLIC_URL\nREDIS_URL'} value={form.envNames} onChange={(e) => update('envNames', e.target.value)} /><small>Indiquez seulement les noms. Les valeurs sensibles seront renseignées dans un coffre de secrets côté backend.</small></label>
            </>}
            {step === 3 && <>
              <div className="panel-heading"><span className="eyebrow">04 / POLITIQUES</span><h2>Déclencheurs & sécurité</h2><p>Adaptez les garde-fous DevSecOps sans reproduire la console du moteur CI/CD.</p></div>
              <h3 className="form-subheading"><Workflow size={18}/> Événements déclencheurs</h3>
              <div className="toggle-group">
                <Toggle title="Déploiement sur push" description={`Démarrer lorsque ${form.branch || 'la branche suivie'} reçoit un push.`} checked={form.onPush} onChange={(value) => update('onPush', value)}/>
                <Toggle title="Déploiement après fusion" description="Déclencher après fusion vers la branche de production." checked={form.onMerge} onChange={(value) => update('onMerge', value)}/>
                <Toggle title="Environnements de prévisualisation" description="Prévoir un environnement temporaire pour les pull/merge requests." checked={form.previewPR} onChange={(value) => update('previewPR', value)}/>
                <Toggle title="Lancement manuel autorisé" description="Permettre une exécution manuelle depuis l’orchestrateur." checked={form.manual} onChange={(value) => update('manual', value)}/>
              </div>
              <h3 className="form-subheading"><ShieldCheck size={18}/> Contrôles DevSecOps</h3>
              <div className="security-grid">
                <Toggle title="SAST" description="Analyse statique du code" checked={form.sast} onChange={(value) => update('sast', value)}/>
                <Toggle title="SCA" description="Dépendances vulnérables" checked={form.sca} onChange={(value) => update('sca', value)}/>
                <Toggle title="Secrets" description="Fuites de secrets" checked={form.secretScan} onChange={(value) => update('secretScan', value)}/>
                <Toggle title="Images Docker" description="Contrôle d’image" checked={form.imageScan} onChange={(value) => update('imageScan', value)}/>
                <Toggle title="DAST" description="Analyse dynamique, si adaptée" checked={form.dast} onChange={(value) => update('dast', value)}/>
              </div>
              <div className="toggle-group final-toggles"><Toggle title="Bloquer les déploiements non conformes" description="La politique sera appliquée par le moteur de déploiement choisi." checked={form.failOnSecurity} onChange={(value) => update('failOnSecurity', value)}/></div>
              <label className="field full"><span>Notes d’exploitation (facultatif)</span><textarea rows="3" value={form.notes} onChange={(e) => update('notes', e.target.value)} placeholder="Exigences de validation, dépendances, instructions de rollback…"/></label>
            </>}
            <div className="form-actions">
              <button className="button-quiet" type="button" onClick={saveDraft}><Save size={16}/> Enregistrer le brouillon</button>
              <div className="actions-right">
                {step > 0 && <button className="button-outline" type="button" onClick={() => move(step - 1)}><ArrowLeft size={16}/> Précédent</button>}
                {step < 3 ? <button className="button-primary" type="button" onClick={() => move(step + 1)}>Continuer <ArrowRight size={16}/></button> : <button className="button-primary" type="button" onClick={exportConfig}><CloudUpload size={17}/> Exporter la configuration</button>}
              </div>
            </div>
          </div></div>
          <div className="cut-frame draft-panel"><div className="cut-body"><div className="draft-heading"><div><span className="eyebrow">ESPACE LOCAL</span><h3>Mes configurations</h3></div><span className="draft-count">{drafts.length} brouillon{drafts.length > 1 ? 's' : ''}</span></div>
            {drafts.length === 0 ? <p className="empty-small">Aucun brouillon pour le moment. Enregistrez un projet pour le reprendre plus tard.</p> : <div className="draft-list">{drafts.map((draft) => <div className="draft-row" key={draft.id}><span className="draft-icon"><FileCode2 size={18}/></span><div className="draft-info"><strong>{draft.projectName || 'Sans nom'}</strong><small>{draft.sourceProvider} · {draft.branch || '—'} · {draft.environment}</small></div><button className="mini-button" type="button" onClick={() => loadDraft(draft)}>Ouvrir</button><button className="mini-icon" type="button" onClick={() => removeDraft(draft.id)} aria-label={`Supprimer ${draft.projectName}`}><Trash2 size={16}/></button></div>)}</div>}
          </div></div>
        </div>
        <aside className="deploy-aside" aria-label="Récapitulatif du projet">
          <div className="cut-frame summary-panel"><div className="cut-body"><span className="eyebrow">APERÇU DU PROJET</span><div className="summary-symbol"><Settings2 size={25}/></div><h2>{form.projectName || 'Votre prochain projet'}</h2><p className="summary-description">Une configuration progressive, adaptée à votre stack.</p>
            <div className="summary-list"><div><span>Source</span><strong>{form.sourceProvider}</strong></div><div><span>Dépôt</span><strong className="truncate-value">{form.repository || 'Non défini'}</strong></div><div><span>Branche</span><strong>{form.branch || '—'}</strong></div><div><span>Application</span><strong>{form.applicationType}</strong></div><div><span>CI/CD</span><strong>{form.ciProvider || 'À choisir'}</strong></div><div><span>Publication</span><strong>{form.environment}</strong></div><div><span>Destination</span><strong>{form.domain || form.target || 'À configurer'}</strong></div></div>
            <div className="summary-bottom"><div className="summary-trigger"><span>Déclencheurs</span><div>{autoEvents.length ? autoEvents.map((event) => <span key={event} className="small-tag">{event}</span>) : <span className="small-tag">Aucun</span>}</div></div><div className="notice-bar"><LockKeyhole size={18}/><span>Aucun webhook ni pipeline n’est créé par cette interface.</span></div></div>
          </div></div>
          <div className="aside-caption"><span className="mono-label">AUTOKOM / AUTOMATION</span><p>Les secrets et les décisions d'autorisation appartiennent aux services serveur.</p></div>
        </aside>
      </div>
    </>
  )
}
