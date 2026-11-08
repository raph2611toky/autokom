import { useEffect, useMemo, useRef, useState } from 'react'
import { Check, ChevronRight, ExternalLink, Folder, LoaderCircle, LockKeyhole, Search, Unplug, Code2 } from 'lucide-react'
import GitHubMark from './GitHubMark.jsx'
import ActionDialog from './ActionDialog.jsx'
import { detectSource, recommendFrontendCommands } from '../services/sourceAnalysis.js'
import { GITHUB_AVAILABLE } from '../config/runtime.js'
import { projectsApi, gitApi } from '../services/projectsApi.js'
import { errorText } from '../services/projectWorkflow.js'
import { startGitHubAccountAuth, GITHUB_FINISH_SETUP_KEY, validatedGitHubAuthorizationUrl } from '../services/githubAuthFlow.js'
import { INSTALL_HANDOFF, verifiedInstallationUrl, selectAuthorizedInstallation } from '../services/githubInstallFlow.js'

/** Même interface GitHub, données provenant exclusivement de Django/GitHub App. */
export default function LiveGitHubConnector({ form, update, onNotice, onConnectionChange }) {
  const [connected, setConnected] = useState(false)
  const [backendConfigured, setBackendConfigured] = useState(null)
  const [loading, setLoading] = useState(true)
  const [installations, setInstallations] = useState([])
  const [organizations, setOrganizations] = useState([])
  const [repos, setRepos] = useState([])
  const [branches, setBranches] = useState([])
  const [directories, setDirectories] = useState([])
  const [browseAnalysis, setBrowseAnalysis] = useState(null)
  const [foldersLoading, setFoldersLoading] = useState(false)
  const [browsePath, setBrowsePath] = useState('')
  const [search, setSearch] = useState('')
  const [error, setError] = useState('')
  const [installationUrl, setInstallationUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [chosenRepo, setChosenRepo] = useState(null)
  const [confirmDisconnect, setConfirmDisconnect] = useState(false)
  const [permissionPending, setPermissionPending] = useState(false)
  const permissionBaseline = useRef({ ids: new Set(), installs: new Set() })
  const permissionBusy = useRef(false)
  const permissionStart = useRef(0)

  useEffect(() => { onConnectionChange?.(Boolean(GITHUB_AVAILABLE && connected)) }, [connected, onConnectionChange])

  useEffect(() => {
    if (!GITHUB_AVAILABLE) { setLoading(false); return }
    let alive = true
    Promise.all([gitApi.status(), projectsApi.organizations()]).then(async ([status, org]) => {
      if (!alive) return
      setBackendConfigured(status?.configured !== false)
      setConnected(Boolean(status?.connected) && status?.configured !== false)
      setInstallationUrl(status?.installation_url || '')
      setOrganizations(org?.organizations || [])
      // After explicit GitHub signup, continue with repository permissions without
      // requiring a second AUTOKOM button. GitHub itself must still receive consent.
      if (!status?.connected && sessionStorage.getItem(GITHUB_FINISH_SETUP_KEY) === '1') {
        sessionStorage.removeItem(GITHUB_FINISH_SETUP_KEY)
        const grant = await gitApi.start()
        window.location.assign(validatedGitHubAuthorizationUrl(grant.authorization_url))
        return
      }
      if (status?.connected) sessionStorage.removeItem(GITHUB_FINISH_SETUP_KEY)
      if (!form.organizationId && org?.organizations?.[0]) update({ organizationId: org.organizations[0].id })
      if (status?.connected) {
        const installed = (await gitApi.installations())?.installations || []
        setInstallations(installed)
        if (installed.length) sessionStorage.removeItem('autokom.github.install-started')
        let previous = []
        try {
          const stored = sessionStorage.getItem(INSTALL_HANDOFF)
          if (stored) { previous = JSON.parse(stored); sessionStorage.removeItem(INSTALL_HANDOFF) }
        } catch { sessionStorage.removeItem(INSTALL_HANDOFF) }
        const hinted = new URLSearchParams(window.location.search).get('installation_id') || ''
        const chosen = selectAuthorizedInstallation(installed, previous, hinted)
        // Le compte unique est imposé automatiquement ; une ancienne sélection
        // (désormais révoquée) ne doit pas bloquer le parcours.
        const currentAuthorized = installed.some(item => String(item.id) === String(form.githubInstallationId))
        if (chosen && !currentAuthorized) {
          update({ githubInstallationId: chosen, repository: '', repositorySlug: '',
            githubRepositoryId: '', branch: '', workdir: './' })
        }
        // After OAuth, continue the setup flow automatically if there is no installation.
        // GitHub still requires the user to approve its installation UI.
        if (!installed.length && status.installation_url &&
            sessionStorage.getItem('autokom.github.install-started') !== '1') {
          sessionStorage.setItem('autokom.github.install-started', '1')
          sessionStorage.setItem(INSTALL_HANDOFF, JSON.stringify([]))
          window.location.assign(verifiedInstallationUrl(status.installation_url))
        }
      }
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
    setFoldersLoading(true); setError(''); setDirectories([]); setBrowseAnalysis(null)
    gitApi.directories(form.githubInstallationId, form.githubRepositoryId, form.branch, browsePath).then(data => {
      if (active) {
        setDirectories(data?.directories || [])
        const detected = detectSource(data?.files || [], data?.package || null)
        setBrowseAnalysis(detected.available ? { ...recommendFrontendCommands(detected, data?.files || []), storageWarning: data?.storage_guard?.detected ? data.storage_guard.message : '' } : null)
      }
    }).catch(err => { if (active) { setDirectories([]); setError(errorText(err)) } })
      .finally(() => { if (active) setFoldersLoading(false) })
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
  async function linkForLogin() {
    if (!GITHUB_AVAILABLE || busy) return
    setBusy(true); setError('')
    try { await startGitHubAccountAuth('link') }
    catch (err) { setError(errorText(err)); setBusy(false) }
  }
  async function disconnect() {
    setBusy(true)
    try {
      await gitApi.disconnect()
      setConnected(false); setRepos([]); setBranches([]); setDirectories([])
      update({ repository: '', repositorySlug: '', githubRepositoryId: '', githubInstallationId: '', branch: '', workdir: './' })
    } catch (err) { setError(errorText(err)) }
    finally { setBusy(false) }
  }
  function configureInstallation() {
    // On repository updates GitHub may not redirect to the OAuth callback.
    // Keep AUTOKOM open while GitHub manages installation permissions in a new tab.
    permissionBaseline.current = {
      ids: new Set(repos.map(item => String(item.id))),
      installs: new Set(installations.map(item => String(item.id))),
    }
    permissionStart.current = Date.now()
    setPermissionPending(true)
    setError('')
  }
  async function refreshPermissions({ finishUnchanged = false } = {}) {
    if (permissionBusy.current || !connected) return
    permissionBusy.current = true
    try {
      const current = (await gitApi.installations())?.installations || []
      const newlyInstalled = current.find(item => !permissionBaseline.current.installs.has(String(item.id)))
      const preferred = newlyInstalled || current.find(item => String(item.id) === String(form.githubInstallationId)) || current[0]
      const updated = preferred ? (await gitApi.repositories(preferred.id))?.repositories || [] : []
      const newRepo = updated.filter(repo => !permissionBaseline.current.ids.has(String(repo.id)))
      const changedInstall = preferred && !permissionBaseline.current.installs.has(String(preferred.id))
      setInstallations(current)
      if (preferred) {
        setRepos(updated)
        if (String(form.githubInstallationId) !== String(preferred.id)) {
          update({ githubInstallationId: String(preferred.id), repository: '', repositorySlug: '',
            githubRepositoryId: '', branch: '', workdir: './' })
        }
      }
      if (newRepo.length || changedInstall || finishUnchanged) {
        setPermissionPending(false)
        if (newRepo.length === 1) chooseRepo(newRepo[0])
        onNotice?.({type: 'success', text: newRepo.length === 1
          ? `Dépôt ${newRepo[0].full_name} autorisé. Vous pouvez le configurer.`
          : 'Autorisations GitHub actualisées. Choisissez votre dépôt dans AUTOKOM.'})
      }
    } catch (err) { setError(errorText(err)) }
    finally { permissionBusy.current = false }
  }
  useEffect(() => {
    if (!permissionPending) return
    const check = () => {
      if (Date.now() - permissionStart.current > 180000) { setPermissionPending(false); return }
      if (document.visibilityState === 'visible') void refreshPermissions()
    }
    const interval = window.setInterval(check, 4000)
    const onFocus = () => { void refreshPermissions() }
    window.addEventListener('focus', onFocus)
    return () => { window.clearInterval(interval); window.removeEventListener('focus', onFocus) }
  }, [permissionPending, connected, form.githubInstallationId, repos, installations])
  function chooseRepo(repo) {
    setChosenRepo(repo)
    setBrowsePath('')
    setDirectories([]); setBranches([]); setFoldersLoading(false); setError('')
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
        <button className="gh-subtle-button" type="button" onClick={linkForLogin} disabled={busy}><GitHubMark size={15}/> Associer au compte AUTOKOM</button>
        <button className="gh-subtle-button" type="button" onClick={() => setConfirmDisconnect(true)} disabled={busy}><Unplug size={15}/> Déconnecter</button></div>
      {organizations.length > 1 && <label className="field"><span>Organisation AUTOKOM</span><select value={form.organizationId || ''} onChange={event => update({ organizationId: event.target.value })}>
        <option value="">Sélectionner…</option>{organizations.map(org => <option value={org.id} key={org.id}>{org.name}</option>)}
      </select></label>}
      {installations.length > 1 ? <label className="field"><span>Compte GitHub autorisé</span><select value={form.githubInstallationId || ''} onChange={event => {
        update({ githubInstallationId: event.target.value, githubRepositoryId: '', branch: '', repositorySlug: '', workdir: './' }); setChosenRepo(null)
      }}><option value="">Choisir le compte…</option>{installations.map(item => <option key={item.id} value={item.id}>{item.account}</option>)}</select></label>
        : installations.length === 1 ? <p className="ak-simulation-disclosure" role="status">Compte GitHub sélectionné automatiquement : {installations[0].account}</p> :
          <p className="ak-simulation-disclosure">Aucun dépôt autorisé pour ce compte. GitHub demande votre accord avant l’installation.</p>}
      {installationUrl && <a className="gh-subtle-button ak-gh-install-action"
          href={verifiedInstallationUrl(installationUrl)} target="_blank" rel="noopener noreferrer"
          onClick={configureInstallation} title="Ouvrir GitHub dans un nouvel onglet ; AUTOKOM reste ouvert">
        {installations.length ? 'Gérer les dépôts autorisés' : 'Autoriser des dépôts GitHub'} <ExternalLink size={15} aria-hidden="true"/>
      </a>}
      {permissionPending && <div className="ak-gh-permission-handoff" role="status" aria-live="polite">
        <LoaderCircle className="ak-spin" size={17}/>
        <span>Validez les dépôts sur GitHub. Revenez à cet onglet : AUTOKOM actualise la liste automatiquement.</span>
        <button type="button" className="gh-subtle-button" onClick={() => void refreshPermissions({finishUnchanged: true})}>Actualiser maintenant</button>
      </div>}
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
          <div className="ak-gh-folders-head"><strong>Dossier du projet (sources, package.json ou Dockerfile) — pas dist/build</strong><span>{form.workdir === './' ? 'Racine' : form.workdir}</span></div>
          <div className="ak-gh-breadcrumb"><button type="button" onClick={() => setBrowsePath('')}>Racine</button>{browsePath.split('/').filter(Boolean).map((part, idx, arr) => <span key={idx}><ChevronRight size={13}/><button type="button" onClick={() => setBrowsePath(arr.slice(0, idx+1).join('/'))}>{part}</button></span>)}</div>
          <div className="ak-gh-folder-list" aria-busy={foldersLoading}>
            {foldersLoading ? <div className="ak-gh-loading" role="status"><LoaderCircle className="ak-spin" size={17}/> Chargement du dossier GitHub…</div> : directories.map(item => <button key={item.path} type="button" onClick={() => setBrowsePath(item.path)}><Folder size={17}/>{item.name}<ChevronRight size={15}/></button>)}
            {!foldersLoading && !directories.length && <small>Aucun sous-dossier à ce niveau.</small>}
          </div>
          {browseAnalysis && <div className="ak-gh-folder-detection" role="status"><Code2 size={17}/><div><strong>{browseAnalysis.framework} détecté dans {browsePath || 'la racine'}</strong><small>{browseAnalysis.supported ? 'Compatible avec la recette automatique' : 'Framework reconnu · vérifier les conditions de publication'}</small></div></div>}
          {browseAnalysis?.storageWarning && <small className="ak-storage-note">SQLite détectée : vous pourrez confirmer le risque au récapitulatif.</small>}
          <button type="button" className="button-outline ak-gh-use-folder" disabled={foldersLoading} onClick={() => { update({ workdir: browsePath || './' }); onNotice?.({ type: 'success', text: `Dossier source : ${browsePath || 'racine'}` }) }}><Check size={15}/> Choisir {browsePath || 'la racine'}</button>
        </div>
      </div>}
    </>}
    <ActionDialog open={confirmDisconnect} title="Déconnecter GitHub ?"
      description="La connexion GitHub de votre compte AUTOKOM sera retirée. Les projets existants ne sont pas supprimés."
      confirmLabel="Déconnecter" variant="warning" busy={busy}
      onClose={() => setConfirmDisconnect(false)}
      onConfirm={() => { setConfirmDisconnect(false); void disconnect() }}/>
  </div>
}
