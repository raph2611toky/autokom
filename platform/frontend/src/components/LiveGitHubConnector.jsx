import { useEffect, useMemo, useState } from 'react'
import { Check, ChevronRight, Folder, LockKeyhole, Search, Unplug } from 'lucide-react'
import GitHubMark from './GitHubMark.jsx'
import { GITHUB_AVAILABLE } from '../config/runtime.js'
import { projectsApi, gitApi } from '../services/projectsApi.js'
import { errorText } from '../services/projectWorkflow.js'

/** Même interface GitHub, données provenant exclusivement de Django/GitHub App. */
export default function LiveGitHubConnector({ form, update, onNotice }) {
  const [connected, setConnected] = useState(false)
  const [backendConfigured, setBackendConfigured] = useState(null)
  const [loading, setLoading] = useState(true)
  const [installations, setInstallations] = useState([])
  const [organizations, setOrganizations] = useState([])
  const [repos, setRepos] = useState([])
  const [branches, setBranches] = useState([])
  const [directories, setDirectories] = useState([])
  const [browsePath, setBrowsePath] = useState('')
  const [search, setSearch] = useState('')
  const [error, setError] = useState('')
  const [installationUrl, setInstallationUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [chosenRepo, setChosenRepo] = useState(null)

  useEffect(() => {
    if (!GITHUB_AVAILABLE) { setLoading(false); return }
    let alive = true
    Promise.all([gitApi.status(), projectsApi.organizations()]).then(async ([status, org]) => {
      if (!alive) return
      setBackendConfigured(status?.configured !== false)
      setConnected(Boolean(status?.connected) && status?.configured !== false)
      setInstallationUrl(status?.installation_url || '')
      setOrganizations(org?.organizations || [])
      if (!form.organizationId && org?.organizations?.[0]) update({ organizationId: org.organizations[0].id })
      if (status?.connected) setInstallations((await gitApi.installations())?.installations || [])
    }).catch(err => {
      if (alive) {
        if (err?.status === 401 || err?.status === 409) setConnected(false)
        setError(errorText(err))
      }
    }).finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [])

  useEffect(() => {
    if (!GITHUB_AVAILABLE || !connected || !form.githubInstallationId) return
    let active = true
    setBusy(true)
    gitApi.repositories(form.githubInstallationId).then(data => {
      if (active) setRepos(data?.repositories || [])
    }).catch(err => { if (active) setError(errorText(err)) }).finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [connected, form.githubInstallationId])

  useEffect(() => {
    if (!GITHUB_AVAILABLE || !form.githubInstallationId || !form.githubRepositoryId) return
    let active = true
    gitApi.branches(form.githubInstallationId, form.githubRepositoryId).then(data => {
      if (active) setBranches(data?.branches || [])
    }).catch(err => { if (active) setError(errorText(err)) })
    return () => { active = false }
  }, [form.githubInstallationId, form.githubRepositoryId])

  useEffect(() => {
    if (!GITHUB_AVAILABLE || !form.githubInstallationId || !form.githubRepositoryId || !form.branch) return
    let active = true
    gitApi.directories(form.githubInstallationId, form.githubRepositoryId, form.branch, browsePath).then(data => {
      if (active) setDirectories(data?.directories || [])
    }).catch(err => { if (active) { setDirectories([]); setError(errorText(err)) } })
    return () => { active = false }
  }, [form.githubInstallationId, form.githubRepositoryId, form.branch, browsePath])

  const visible = useMemo(() => repos.filter(repo => `${repo.full_name} ${repo.description || ''}`.toLowerCase().includes(search.toLowerCase())), [repos, search])
  async function connect() {
    if (!GITHUB_AVAILABLE || busy) return
    setBusy(true)
    try {
      const data = await gitApi.start()
      const url = new URL(data.authorization_url)
      if (url.protocol !== 'https:' || url.hostname !== 'github.com') throw new Error('Destination OAuth GitHub invalide.')
      window.location.assign(url.href)
    } catch (err) { setError(errorText(err)); setBusy(false) }
  }
  async function disconnect() {
    if (!window.confirm('Déconnecter la connexion GitHub de ce compte AUTOKOM ?')) return
    setBusy(true)
    try {
      await gitApi.disconnect()
      setConnected(false); setRepos([]); setBranches([]); setDirectories([])
      update({ repository: '', repositorySlug: '', githubRepositoryId: '', githubInstallationId: '', branch: '', workdir: './' })
    } catch (err) { setError(errorText(err)) }
    finally { setBusy(false) }
  }
  function chooseRepo(repo) {
    setChosenRepo(repo)
    setBrowsePath('')
    setDirectories([]); setBranches([]); setError('')
    update({ repository: repo.html_url, repositorySlug: repo.full_name, githubRepositoryId: repo.id,
      branch: repo.default_branch, workdir: './', projectName: repo.name })
    onNotice?.(null)
  }
  return <div className="gh-connect ak-gh-workspace">
    <div className="gh-connect-head">
      <span className="gh-icon"><GitHubMark size={23}/></span>
      <div className="gh-connect-title"><h3>GitHub</h3><p>Choisissez le dépôt, la branche et le dossier.</p></div>
      <span className={`gh-state gh-state-${connected ? 'connected' : 'disconnected'}`}>
        {connected ? <Check size={14}/> : <Unplug size={14}/>} {connected ? 'Connecté' : 'À connecter'}
      </span>
    </div>
    {!!form.backendProjectId && <p className="ak-simulation-disclosure">Dépôt déjà lié au projet : {form.repositorySlug}. Vous pouvez modifier la configuration sans reconnecter GitHub.</p>}
    {!GITHUB_AVAILABLE && <p className="ak-simulation-disclosure">Connexion aux dépôts GitHub désactivée dans la configuration frontend. Aucun dépôt fictif n’est utilisé.</p>}
    {GITHUB_AVAILABLE && backendConfigured === false && <p className="ak-simulation-disclosure">GitHub App non configurée côté Django : vérifier Client ID, secret, callback, slug et chiffrement. Aucun dépôt fictif n’est affiché.</p>}
    {loading && <p className="ak-simulation-disclosure" role="status">Connexion à votre espace GitHub…</p>}
    {error && <p className="feedback error" role="alert">{error}</p>}
    {!loading && GITHUB_AVAILABLE && backendConfigured !== false && !connected && <div className="ak-gh-start">
      <button className="button-primary" type="button" disabled={busy} onClick={connect}><GitHubMark size={17}/> Connecter GitHub</button>
    </div>}
    {connected && <>
      <div className="gh-inline"><span className="gh-user">Votre espace GitHub réel</span>
        <button className="gh-subtle-button" type="button" onClick={disconnect} disabled={busy}><Unplug size={15}/> Déconnecter</button></div>
      {organizations.length > 1 && <label className="field"><span>Organisation AUTOKOM</span><select value={form.organizationId || ''} onChange={event => update({ organizationId: event.target.value })}>
        <option value="">Sélectionner…</option>{organizations.map(org => <option value={org.id} key={org.id}>{org.name}</option>)}
      </select></label>}
      <label className="field"><span>Installation GitHub</span><select value={form.githubInstallationId || ''} onChange={event => {
        update({ githubInstallationId: event.target.value, githubRepositoryId: '', branch: '', repositorySlug: '', workdir: './' }); setChosenRepo(null)
      }}><option value="">Choisir l'installation…</option>{installations.map(item => <option key={item.id} value={item.id}>{item.account}</option>)}</select></label>
      {!installations.length && <p className="ak-simulation-disclosure">Aucune installation GitHub App autorisée sur tes dépôts.</p>}
      {installationUrl && <a className="gh-subtle-button" href={installationUrl} target="_blank" rel="noopener noreferrer">Configurer l'installation GitHub</a>}
      {!!form.githubInstallationId && <div className="gh-repo-browser">
        <label className="gh-search"><Search size={17}/><input value={search} onChange={event => setSearch(event.target.value)} placeholder="Rechercher un dépôt…" aria-label="Rechercher un dépôt"/></label>
        <div className="gh-repositories" role="group" aria-label="Dépôts GitHub autorisés">
          {visible.map(repo => <button key={repo.id} type="button" className={`gh-repository ${String(form.githubRepositoryId) === String(repo.id) ? 'selected' : ''}`} onClick={() => chooseRepo(repo)}><GitHubMark size={15}/><span><strong>{repo.full_name}</strong><small>{repo.description || 'Dépôt GitHub autorisé'}</small></span>{repo.private && <LockKeyhole size={14}/>} {String(form.githubRepositoryId) === String(repo.id) && <Check size={15}/>}</button>)}
          {!visible.length && <p className="gh-empty">{busy ? 'Chargement des dépôts…' : 'Aucun dépôt autorisé trouvé.'}</p>}
        </div>
      </div>}
      {!!form.githubRepositoryId && <div className="ak-gh-source-config">
        <label className="field"><span>Branche à déployer</span><select value={form.branch} onChange={event => { setBrowsePath(''); update({ branch: event.target.value, workdir: './' }) }}>
          {!branches.some(b => b.name === form.branch) && <option value={form.branch}>{form.branch}</option>}
          {branches.map(item => <option key={item.name} value={item.name}>{item.name}</option>)}
        </select></label>
        <div className="ak-gh-folders">
          <div className="ak-gh-folders-head"><strong>Emplacement du code source</strong><span>{form.workdir === './' ? 'Racine' : form.workdir}</span></div>
          <div className="ak-gh-breadcrumb"><button type="button" onClick={() => setBrowsePath('')}>Racine</button>{browsePath.split('/').filter(Boolean).map((part, idx, arr) => <span key={idx}><ChevronRight size={13}/><button type="button" onClick={() => setBrowsePath(arr.slice(0, idx+1).join('/'))}>{part}</button></span>)}</div>
          <div className="ak-gh-folder-list">{directories.map(item => <button key={item.path} type="button" onClick={() => setBrowsePath(item.path)}><Folder size={17}/>{item.name}<ChevronRight size={15}/></button>)}{!directories.length && <small>Aucun sous-dossier à ce niveau.</small>}</div>
          <button type="button" className="button-outline ak-gh-use-folder" onClick={() => { update({ workdir: browsePath || './' }); onNotice?.({ type: 'success', text: `Dossier source : ${browsePath || 'racine'}` }) }}><Check size={15}/> Choisir {browsePath || 'la racine'}</button>
        </div>
      </div>}
    </>}
  </div>
}
