import { useEffect, useState } from 'react'
import {
  ArrowDownToLine, ArrowRight, Check, CheckCircle2, ChevronRight,
  CircleHelp, CloudUpload, FileCode2, FolderGit2, GitBranch, Github,
  Info, LockKeyhole, Plus, Save, Settings2, Shield, Trash2, X,
} from 'lucide-react'
import { API_BASE_URL, githubIntegrationReady } from '../config/api.js'

const STORAGE_KEY = 'autokom:deployment-automation:drafts:v1'
const INITIAL_FORM = {
  id: '', projectName: '', repository: '', branch: 'main',
  projectType: 'static', rootDirectory: '.', installCommand: '',
  buildCommand: '', testCommand: '', outputDirectory: 'dist',
  dockerfile: 'Dockerfile', containerPort: '3000', domain: '',
  triggerPush: true, triggerPR: false,
  sast: true, sca: true, secrets: true, imageScan: true,
}

function readDrafts() {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')
    return Array.isArray(value) ? value.filter((item) => item && typeof item.id === 'string') : []
  } catch {
    return []
  }
}

function parseRepository(value) {
  try {
    const url = new URL(value.trim())
    const path = url.pathname.replace(/\/$/, '').replace(/\.git$/, '')
    const parts = path.split('/').filter(Boolean)
    if (url.protocol !== 'https:' || url.hostname.toLowerCase() !== 'github.com' || parts.length !== 2 || url.username || url.password) {
      return null
    }
    if (!parts.every((segment) => /^[\w.-]+$/.test(segment)) || parts.some((x) => x === '..' || x === '.')) return null
    return { owner: parts[0], name: parts[1] }
  } catch {
    return null
  }
}

function validate(form) {
  if (!form.projectName.trim()) return 'Renseigne un nom de projet.'
  if (!parseRepository(form.repository)) return 'Utilise une URL GitHub au format https://github.com/organisation/depot.'
  if (!form.branch.trim()) return 'Renseigne la branche à surveiller.'
  if (!/^[\w./-]+$/.test(form.branch.trim()) || form.branch.includes('..') || form.branch.startsWith('-')) return 'Le nom de la branche est invalide.'
  const paths = [form.rootDirectory, form.projectType === 'static' ? form.outputDirectory : form.dockerfile]
  if (paths.some((p) => !p.trim() || p.startsWith('/') || p.split('/').includes('..') || p.includes('\\'))) {
    return 'Les chemins doivent être relatifs au dépôt et sans « .. ».'
  }
  if (form.projectType === 'docker' && (!/^\d+$/.test(form.containerPort) || Number(form.containerPort) < 1 || Number(form.containerPort) > 65535)) {
    return 'Le port du conteneur doit être compris entre 1 et 65535.'
  }
  if (!form.triggerPush && !form.triggerPR) return 'Active au moins un évènement GitHub.'
  if (form.domain && !/^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/i.test(form.domain.trim())) {
    return 'Saisis un nom de domaine valide, sans protocole ni chemin.'
  }
  return ''
}

function Toggle({ id, checked, onChange, title, hint }) {
  return (
    <label className="toggle-row" htmlFor={id}>
      <span><strong>{title}</strong><small>{hint}</small></span>
      <span className="switch-wrap">
        <input id={id} type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
        <span className="switch" aria-hidden="true" />
      </span>
    </label>
  )
}

function TextInput({ label, hint, id, ...props }) {
  return (
    <label className="field" htmlFor={id}>
      <span className="field-label">{label}</span>
      <input id={id} {...props} />
      {hint && <small className="field-hint">{hint}</small>}
    </label>
  )
}

export default function Automation() {
  const [drafts, setDrafts] = useState(readDrafts)
  const [form, setForm] = useState(INITIAL_FORM)
  const [message, setMessage] = useState(null)
  const [search, setSearch] = useState('')
  const [showGithubHelp, setShowGithubHelp] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const update = (key, value) => setForm((current) => ({ ...current, [key]: value }))
  const repository = parseRepository(form.repository)
  const selected = Boolean(form.id)

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(drafts))
    } catch {
      setMessage({ type: 'error', content: 'Le stockage local est indisponible. Tu peux exporter ta configuration.' })
    }
  }, [drafts])

  function startNew() {
    setForm({ ...INITIAL_FORM })
    setShowAdvanced(false)
    setMessage(null)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  function selectDraft(draft) {
    setForm({ ...INITIAL_FORM, ...draft })
    setShowAdvanced(false)
    setMessage(null)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  function saveDraft(event) {
    event.preventDefault()
    const error = validate(form)
    if (error) {
      setMessage({ type: 'error', content: error })
      return
    }
    const id = form.id || (globalThis.crypto?.randomUUID?.() ?? `draft-${Date.now()}`)
    const record = { ...form, id, updatedAt: new Date().toISOString() }
    setDrafts((items) => [record, ...items.filter((item) => item.id !== id)])
    setForm(record)
    setMessage({ type: 'success', content: 'Brouillon enregistré dans ce navigateur. Aucun déploiement n’a été lancé.' })
  }

  function deleteDraft(id) {
    const draft = drafts.find((item) => item.id === id)
    if (!draft || !window.confirm(`Supprimer le brouillon « ${draft.projectName} » ?`)) return
    setDrafts((items) => items.filter((item) => item.id !== id))
    if (form.id === id) setForm({ ...INITIAL_FORM })
    setMessage({ type: 'success', content: 'Brouillon local supprimé.' })
  }

  function exportDraft() {
    const error = validate(form)
    if (error) {
      setMessage({ type: 'error', content: error })
      return
    }
    const { id, updatedAt, ...configuration } = form
    const exportData = { schemaVersion: 1, type: 'autokom.deployment_automation.draft', configuration }
    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${form.projectName.trim().replace(/[^a-z0-9_-]/gi, '-').toLowerCase()}-autokom.json`
    link.click()
    URL.revokeObjectURL(url)
    setMessage({ type: 'success', content: 'Configuration exportée. Les identifiants GitHub et Jenkins ne sont pas inclus.' })
  }

  const filtered = drafts.filter((item) => `${item.projectName} ${item.repository}`.toLowerCase().includes(search.toLowerCase()))

  return (
    <>
      <section className="hero" aria-labelledby="page-title">
        <div className="eyebrow"><span className="tiny-line" /> CONFIGURATION / AUTOMATISATION</div>
        <div className="hero-row">
          <div>
            <h1 id="page-title">Déployez sans <em>répéter.</em></h1>
            <p>Un dépôt GitHub, quelques paramètres, et AUTOKOM préparera l’automatisation côté Jenkins. Ici, vous configurez. <strong>Jenkins exécute.</strong></p>
          </div>
          <div className="hero-mark" aria-hidden="true"><span>01</span><GitBranch size={30} /></div>
        </div>
      </section>

      <div className="work-layout">
        <section className="work-main" aria-label="Configuration de déploiement">
          <div className="section-heading">
            <div><p className="section-kicker">ESPACE DE TRAVAIL</p><h2>{selected ? 'Modifier une automatisation' : 'Nouvelle automatisation'}</h2></div>
            <button type="button" className="text-button" onClick={startNew}><Plus size={17} /> Nouveau</button>
          </div>

          <div className="cut-frame integration-card">
            <div className="frame-content integration-content">
              <div className="integration-logo"><Github size={25} /></div>
              <div className="integration-copy">
                <div className="integration-title"><h3>Connexion GitHub</h3><span className="pill neutral">Non connectée</span></div>
                <p>Connectez GitHub une fois pour retrouver vos dépôts et créer automatiquement les webhooks.</p>
              </div>
              <button type="button" className="button-outline" onClick={() => setShowGithubHelp((v) => !v)} aria-expanded={showGithubHelp}>
                <CircleHelp size={16} /> Pourquoi ?
              </button>
            </div>
            {showGithubHelp && (
              <div className="github-help">
                <Info size={18} />
                <p>La connexion GitHub App nécessite le backend AUTOKOM. Cette version permet de préparer des brouillons manuellement, mais ne peut ni authentifier GitHub ni créer un webhook.</p>
              </div>
            )}
          </div>

          <form onSubmit={saveDraft} noValidate>
            <div className="cut-frame form-frame">
              <div className="frame-content">
                <div className="frame-heading"><span className="step-no">01</span><div><h3>Votre projet</h3><p>Identifiez le dépôt à automatiser.</p></div><FolderGit2 size={20} className="section-icon" /></div>
                <div className="form-grid">
                  <TextInput id="project" label="Nom du projet" value={form.projectName} onChange={(e) => update('projectName', e.target.value)} placeholder="ex. mon-application" autoComplete="off" />
                  <TextInput id="branch" label="Branche à surveiller" value={form.branch} onChange={(e) => update('branch', e.target.value)} placeholder="main" autoComplete="off" />
                  <div className="field full-span">
                    <label className="field-label" htmlFor="repo">URL du dépôt GitHub</label>
                    <div className="field-with-icon"><Github size={18}/><input id="repo" type="url" value={form.repository} onChange={(e) => update('repository', e.target.value)} placeholder="https://github.com/organisation/projet" autoComplete="url" spellCheck={false}/></div>
                    <small className="field-hint">Saisie manuelle provisoire ; les dépôts seront proposés automatiquement après connexion de GitHub.</small>
                  </div>
                </div>
              </div>
            </div>

            <div className="cut-frame form-frame">
              <div className="frame-content">
                <div className="frame-heading"><span className="step-no">02</span><div><h3>Construction</h3><p>Uniquement les paramètres nécessaires à votre application.</p></div><Settings2 size={20} className="section-icon" /></div>
                <div className="project-types" role="group" aria-label="Type de projet">
                  <button type="button" onClick={() => update('projectType','static')} className={`type-card ${form.projectType === 'static' ? 'selected' : ''}`} aria-pressed={form.projectType === 'static'}><CloudUpload size={20}/><strong>Web statique</strong><small>Vite, React, Vue, Angular…</small></button>
                  <button type="button" onClick={() => update('projectType','docker')} className={`type-card ${form.projectType === 'docker' ? 'selected' : ''}`} aria-pressed={form.projectType === 'docker'}><FileCode2 size={20}/><strong>Docker</strong><small>Backend, API, conteneur…</small></button>
                </div>
                <div className="form-grid">
                  <TextInput id="root" label="Répertoire du projet" value={form.rootDirectory} onChange={(e) => update('rootDirectory', e.target.value)} placeholder="." hint="Chemin relatif dans le dépôt" spellCheck={false}/>
                  {form.projectType === 'static' ? (
                    <TextInput id="output" label="Répertoire de sortie" value={form.outputDirectory} onChange={(e) => update('outputDirectory', e.target.value)} placeholder="dist" spellCheck={false}/>
                  ) : (
                    <TextInput id="dockerfile" label="Chemin du Dockerfile" value={form.dockerfile} onChange={(e) => update('dockerfile', e.target.value)} placeholder="Dockerfile" spellCheck={false}/>
                  )}
                  <TextInput id="install" label="Commande d'installation (optionnel)" value={form.installCommand} onChange={(e) => update('installCommand', e.target.value)} placeholder={form.projectType === 'static' ? 'npm ci' : 'Selon votre Dockerfile'} spellCheck={false}/>
                  <TextInput id="build" label="Commande de build (optionnel)" value={form.buildCommand} onChange={(e) => update('buildCommand', e.target.value)} placeholder={form.projectType === 'static' ? 'npm run build' : 'docker build… géré côté Jenkins'} spellCheck={false}/>
                  {form.projectType === 'docker' && <TextInput id="port" label="Port du conteneur" type="number" min="1" max="65535" value={form.containerPort} onChange={(e) => update('containerPort', e.target.value)} placeholder="3000" />}
                </div>
                <button className="advanced-button" type="button" aria-expanded={showAdvanced} onClick={() => setShowAdvanced((v) => !v)}><ChevronRight size={16} className={showAdvanced ? 'rotated' : ''}/> Paramètres complémentaires</button>
                {showAdvanced && <div className="form-grid advanced-grid">
                  <TextInput id="tests" label="Commande de tests (optionnel)" value={form.testCommand} onChange={(e) => update('testCommand', e.target.value)} placeholder="npm test" spellCheck={false}/>
                  <TextInput id="domain" label="Domaine cible (optionnel)" value={form.domain} onChange={(e) => update('domain', e.target.value)} placeholder="app.example.com" spellCheck={false}/>
                  <p className="full-span privacy-note"><LockKeyhole size={16}/> Les variables sensibles et identifiants de déploiement seront configurés uniquement via le backend sécurisé, pas dans le stockage local du navigateur.</p>
                </div>}
              </div>
            </div>

            <div className="cut-frame form-frame">
              <div className="frame-content">
                <div className="frame-heading"><span className="step-no">03</span><div><h3>Automatisation et sécurité</h3><p>Définissez quand exécuter Jenkins et les contrôles attendus.</p></div><Shield size={20} className="section-icon" /></div>
                <div className="settings-group">
                  <p className="group-label">DÉCLENCHEURS</p>
                  <Toggle id="push" checked={form.triggerPush} onChange={(value) => update('triggerPush',value)} title="À chaque push" hint={`Lancer Jenkins lorsqu’un commit arrive sur ${form.branch || 'la branche définie'}.`}/>
                  <Toggle id="pull" checked={form.triggerPR} onChange={(value) => update('triggerPR',value)} title="Sur pull request" hint="Vérifier une proposition de modification (sans mise en production automatique)."/>
                </div>
                <div className="settings-group security-group">
                  <p className="group-label">CONTRÔLES DEVSECOPS</p>
                  <div className="security-grid">
                    <label><input type="checkbox" checked={form.sast} onChange={(e) => update('sast', e.target.checked)} /> <span>Analyse du code <small>SAST</small></span></label>
                    <label><input type="checkbox" checked={form.sca} onChange={(e) => update('sca', e.target.checked)} /> <span>Dépendances <small>SCA</small></span></label>
                    <label><input type="checkbox" checked={form.secrets} onChange={(e) => update('secrets', e.target.checked)} /> <span>Détection de secrets</span></label>
                    {form.projectType === 'docker' && <label><input type="checkbox" checked={form.imageScan} onChange={(e) => update('imageScan', e.target.checked)} /> <span>Analyse d’image Docker</span></label>}
                  </div>
                  <p className="settings-help">Ces contrôles devront être traduits en étapes Jenkins par le futur moteur d'orchestration.</p>
                </div>
              </div>
            </div>

            {message && <div className={`form-message ${message.type}`} role="status"><span>{message.type === 'success' ? <CheckCircle2 size={18}/> : <Info size={18}/>} {message.content}</span><button type="button" aria-label="Fermer" onClick={() => setMessage(null)}><X size={16}/></button></div>}

            <div className="form-actions">
              <button className="button-quiet" type="button" onClick={exportDraft}><ArrowDownToLine size={18}/> Exporter JSON</button>
              <button className="button-primary" type="submit"><Save size={18}/> Enregistrer le brouillon <ArrowRight size={18}/></button>
            </div>
          </form>
        </section>

        <aside className="work-aside" aria-label="Récapitulatif et brouillons">
          <div className="cut-frame aside-panel">
            <div className="frame-content">
              <div className="aside-kicker">VOTRE CONFIGURATION <span className="live-dot" /></div>
              <h3>Aperçu</h3>
              <div className="summary-head"><div className="summary-icon"><GitBranch size={23}/></div><div><strong>{form.projectName.trim() || 'Projet sans nom'}</strong><small>{repository ? `${repository.owner}/${repository.name}` : 'Aucun dépôt sélectionné'}</small></div></div>
              <dl className="summary-list">
                <div><dt>Branche</dt><dd>{form.branch || '—'}</dd></div>
                <div><dt>Build</dt><dd>{form.projectType === 'docker' ? 'Docker' : 'Web statique'}</dd></div>
                <div><dt>Déclencheur</dt><dd>{form.triggerPush ? 'Push automatique' : form.triggerPR ? 'Pull request' : 'Aucun'}</dd></div>
                <div><dt>Contrôles</dt><dd>{[form.sast,form.sca,form.secrets,form.projectType === 'docker' && form.imageScan].filter(Boolean).length} sélectionnés</dd></div>
              </dl>
              <div className="activation"><span className="activation-icon"><LockKeyhole size={16}/></span><div><strong>Activation non disponible</strong><p>La création des webhooks, du Jenkinsfile et du job Jenkins nécessite le backend.</p></div></div>
              <button type="button" className="button-disabled" disabled><CloudUpload size={17}/> Activer l'automatisation</button>
            </div>
          </div>

          <div className="aside-drafts">
            <div className="drafts-title"><div><p className="section-kicker">ENREGISTRÉS EN LOCAL</p><h3>Mes brouillons <span>{drafts.length}</span></h3></div></div>
            {drafts.length > 3 && <label className="sr-only" htmlFor="draftsearch">Rechercher une configuration</label>}
            {drafts.length > 3 && <input id="draftsearch" className="draft-search" placeholder="Rechercher..." value={search} onChange={(e) => setSearch(e.target.value)}/>}
            {filtered.length === 0 ? <div className="draft-empty"><FolderGit2 size={24}/><p>{drafts.length ? 'Aucun résultat.' : 'Vos configurations apparaîtront ici après enregistrement.'}</p></div> : (
              <div className="draft-list">
                {filtered.map((draft) => <div className={`draft-item ${form.id === draft.id ? 'active' : ''}`} key={draft.id}>
                  <button type="button" onClick={() => selectDraft(draft)} className="draft-open"><span className="draft-symbol"><FolderGit2 size={18}/></span><span><strong>{draft.projectName}</strong><small>{parseRepository(draft.repository)?.name || 'Dépôt'}</small></span><ChevronRight size={16}/></button>
                  <button className="draft-delete" type="button" title={`Supprimer ${draft.projectName}`} aria-label={`Supprimer ${draft.projectName}`} onClick={() => deleteDraft(draft.id)}><Trash2 size={15}/></button>
                </div>)}
              </div>
            )}
            <p className="storage-note"><LockKeyhole size={14}/> Brouillons dans ce navigateur uniquement.</p>
          </div>
          <div className="aside-footnote"><Check size={16}/><span>Interface volontairement limitée à la <strong>configuration</strong>. L’exécution et les résultats restent dans Jenkins.</span></div>
          {API_BASE_URL && <p className="api-note">URL d'API renseignée. L'intégration n'est pas encore active.</p>}
          {!githubIntegrationReady && <span className="sr-only">Connexion GitHub non implémentée côté frontend.</span>}
        </aside>
      </div>
    </>
  )
}
