import { useMemo, useState } from 'react'
import { Check, ChevronRight, Folder, LockKeyhole, Search, Unplug } from 'lucide-react'
import { getSampleDirectory, getSampleRepository, SAMPLE_REPOSITORIES } from '../services/simulationData.js'
import { normalizeDirectoryPath } from '../services/github.js'
import GitHubMark from './GitHubMark.jsx'
import LiveGitHubConnector from './LiveGitHubConnector.jsx'
import { API_AVAILABLE } from '../config/runtime.js'

/** Sélecteur autonome pour évaluer l'ergonomie avant la connexion GitHub réelle. */
export default function GitHubConnector(props) {
  return API_AVAILABLE ? <LiveGitHubConnector {...props}/> : <DemoGitHubConnector {...props}/>
}

function DemoGitHubConnector({ form, update, onNotice }) {
  const [connected, setConnected] = useState(() => { try { return sessionStorage.getItem('autokom.ui.github') === '1' } catch { return false } })
  const [search, setSearch] = useState('')
  const [browsePath, setBrowsePath] = useState(form.workdir || './')
  const selectedRepo = getSampleRepository(form.githubRepositoryId)
  const repositories = useMemo(() => SAMPLE_REPOSITORIES.filter(repo => `${repo.name} ${repo.summary}`.toLowerCase().includes(search.toLowerCase())), [search])
  const contents = getSampleDirectory(form.githubRepositoryId, browsePath)
  const crumbs = browsePath === './' ? [] : browsePath.split('/')

  function connect() {
    setConnected(true)
    try { sessionStorage.setItem('autokom.ui.github', '1') } catch { /* session non persistante */ }
  }
  function disconnect() {
    setConnected(false)
    setBrowsePath('./')
    try { sessionStorage.removeItem('autokom.ui.github') } catch { /* session non persistante */ }
    update({ repository:'', repositorySlug:'', githubRepositoryId:'', branch:'', workdir:'./' })
    onNotice?.(null)
  }
  function chooseRepo(repo) {
    setBrowsePath('./')
    update({ repository: `https://github.com/${repo.name}.git`, repositorySlug: repo.name, githubRepositoryId: repo.id,
      branch: repo.defaultBranch, workdir: './', projectName: repo.name.split('/')[1] })
    onNotice?.(null)
  }
  function chooseBranch(branch) {
    setBrowsePath('./')
    update({ branch, workdir:'./' })
    onNotice?.(null)
  }
  return <div className="gh-connect ak-gh-workspace">
    <div className="gh-connect-head">
      <span className="gh-icon"><GitHubMark size={23}/></span>
      <div className="gh-connect-title"><h3>GitHub</h3><p>Choisissez le dépôt, la branche et le dossier.</p></div>
      <span className={`gh-state gh-state-${connected?'connected':'disconnected'}`}>{connected?<Check size={14}/>:<Unplug size={14}/>} {connected?'Espace ouvert':'À connecter'}</span>
    </div>
    <p className="ak-simulation-disclosure">Parcours interactif avec des dépôts d’exemple, sans accès à votre compte GitHub.</p>
    {!connected ? <div className="ak-gh-start"><button className="button-primary" type="button" onClick={connect}><GitHubMark size={17}/> Continuer avec GitHub</button></div> : <>
      <div className="gh-inline"><span className="gh-user">Espace de sélection</span><button className="gh-subtle-button" type="button" onClick={disconnect}><Unplug size={15}/> Déconnecter</button></div>
      <div className="gh-repo-browser">
        <label className="gh-search"><Search size={17}/><input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Rechercher un dépôt…" aria-label="Rechercher un dépôt" /></label>
        <div className="gh-repositories" role="group" aria-label="Exemples de dépôts">
          {repositories.map(repo => <button key={repo.id} type="button" className={`gh-repository ${selectedRepo?.id===repo.id?'selected':''}`} onClick={()=>chooseRepo(repo)}><GitHubMark size={15}/><span><strong>{repo.name}</strong><small>{repo.summary}</small></span>{repo.private&&<LockKeyhole size={14}/>} {selectedRepo?.id===repo.id&&<Check size={15}/>}</button>)}
          {!repositories.length&&<p className="gh-empty">Aucun dépôt correspondant.</p>}
        </div>
      </div>
      {selectedRepo&&<div className="ak-gh-source-config">
        <label className="field"><span>Branche à déployer</span><select value={form.branch} onChange={e=>chooseBranch(e.target.value)}>{selectedRepo.branches.map(branch=><option key={branch} value={branch}>{branch}</option>)}</select></label>
        <div className="ak-gh-folders">
          <div className="ak-gh-folders-head"><strong>Emplacement du code source</strong><span>{form.workdir==='./'?'Racine':form.workdir}</span></div>
          <div className="ak-gh-breadcrumb"><button type="button" onClick={()=>setBrowsePath('./')}>Racine</button>{crumbs.map((crumb,index)=><span key={`${crumb}-${index}`}><ChevronRight size={13}/><button type="button" onClick={()=>setBrowsePath(crumbs.slice(0,index+1).join('/'))}>{crumb}</button></span>)}</div>
          <div className="ak-gh-folder-list">{contents.filter(item=>item.type==='dir').map(item=><button type="button" key={item.path} onClick={()=>setBrowsePath(item.path)}><Folder size={17}/>{item.name}<ChevronRight size={15}/></button>)}{!contents.some(item=>item.type==='dir')&&<small>Aucun sous-dossier.</small>}</div>
          <button type="button" className="button-outline ak-gh-use-folder" onClick={()=>{update({workdir: normalizeDirectoryPath(browsePath)});onNotice?.({type:'success',text:`Dossier source sélectionné : ${browsePath}`})}}><Check size={15}/> Choisir {browsePath==='./'?'la racine':browsePath}</button>
        </div>
      </div>}
    </>}
  </div>
}
