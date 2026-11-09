import { useEffect, useState } from 'react'
import { ArrowLeft, Settings2, Trash2, Archive, RotateCcw, Save, LoaderCircle, History } from 'lucide-react'
import { projectsApi } from '../services/projectsApi.js'
import { getRemoteProjectDetail, errorText } from '../services/projectWorkflow.js'
import ProjectPublicSite from '../components/ProjectPublicSite.jsx'
import ActionDialog from '../components/ActionDialog.jsx'

/** User-facing settings. Destruction is confined to the bottom Danger Zone. */
export default function LiveProjectSettings({ navigate, projectId }) {
  const [record,setRecord] = useState(null)
  const [name,setName] = useState('')
  const [description,setDescription] = useState('')
  const [loading,setLoading] = useState(true)
  const [busy,setBusy] = useState(false)
  const [error,setError] = useState('')
  const [notice,setNotice] = useState('')
  const [removeOpen, setRemoveOpen] = useState(false)
  const [archiveOpen, setArchiveOpen] = useState(false)

  useEffect(() => {
    let active=true
    getRemoteProjectDetail(projectId).then(info => {
      if (!active) return
      setRecord(info);setName(info.project.name);setDescription(info.project.description || '')
    }).catch(err => { if(active) setError(errorText(err)) }).finally(() => {if(active)setLoading(false)})
    return () => {active=false}
  },[projectId])

  async function operation(task, success) {
    if(busy)return
    setBusy(true);setError('');setNotice('')
    try {
      const result=await task()
      if(result?.project) setRecord(old => ({...old,project:result.project}))
      setNotice(success)
    } catch (err) {setError(errorText(err))}
    finally {setBusy(false)}
  }
  async function removeProject() {
    if (!record || busy) return
    const expected=record.project.name
    setRemoveOpen(false)
    setBusy(true);setError('')
    try {await projectsApi.deleteProject(projectId,expected);navigate('/projects')}
    catch(err){setError(errorText(err));setBusy(false)}
  }
  if(loading) return <section className="ak-workspace-empty" role="status"><LoaderCircle className="ak-spin" size={18}/> Chargement des paramètres…</section>
  if(!record) return <div className="ak-workspace-empty"><p role="alert">{error || 'Projet introuvable'}</p><button type="button" className="button-outline" onClick={() => navigate('/projects')}>Mes projets</button></div>
  const project=record.project
  return <>
    <div className="ak-back-line"><button type="button" className="button-quiet" onClick={() => navigate(`/projects/${projectId}`)}><ArrowLeft size={16}/> Détails du projet</button></div>
    <section className="page-hero ak-workspace-hero"><div><h1><Settings2 size={26}/> Paramètres · {project.name}</h1><p>Gérer le projet, son adresse publique et son cycle de vie.</p></div><div className="ak-project-settings-shortcuts"><button type="button" className="button-outline" onClick={() => navigate(`/projects/${projectId}/configure`)} disabled={project.status === 'ARCHIVED'}><Settings2 size={16}/> Modifier la configuration</button><button type="button" className="button-outline" onClick={() => navigate(`/projects/${projectId}/history`)}><History size={16}/> Voir les déploiements</button></div></section>
    {error && <p className="feedback error" role="alert">{error}</p>}
    {notice && <p className="feedback success" role="status">{notice}</p>}
    <ProjectPublicSite site={project.public_site} onManage={() => navigate(`/sites/${projectId}`)}/>
    <section className="ak-workspace-sheet ak-project-settings-main"><h2>Informations générales</h2>
      <form onSubmit={event => {event.preventDefault();operation(() => projectsApi.update(projectId,{name:name.trim(),description}), 'Paramètres enregistrés.')}}>
        <label htmlFor="ak-settings-name">Nom du projet</label>
        <input id="ak-settings-name" value={name} maxLength={160} required onChange={event => setName(event.target.value)}/>
        <label htmlFor="ak-settings-description">Description</label>
        <textarea id="ak-settings-description" value={description} maxLength={4000} rows={3} onChange={event => setDescription(event.target.value)}/>
        <button type="submit" className="button-primary" disabled={busy || !name.trim()}>{busy ? <LoaderCircle className="ak-spin" size={16}/> : <Save size={16}/>} Enregistrer</button>
      </form>
      <div className="ak-project-settings-other"><span>Statut du projet : <strong>{project.status==='ARCHIVED'?'Archivé':'Actif'}</strong></span>
        {project.status==='ARCHIVED' ? <button className="button-outline" disabled={busy} onClick={() => operation(() => projectsApi.restore(projectId), 'Projet restauré.')}><RotateCcw size={16}/> Restaurer</button> : <button className="button-outline" disabled={busy} onClick={() => {
          setArchiveOpen(true)
        }}><Archive size={16}/> Archiver</button>}
      </div>
    </section>
    <section className="ak-workspace-sheet ak-danger-zone" aria-labelledby="danger-zone-heading">
      <h2 id="danger-zone-heading">Zone de danger</h2>
      <div className="ak-danger-zone-content"><div><strong>Supprimer ce projet</strong><p>Arrête les ressources AUTOKOM et désactive les domaines gérés. Cette action retire le projet de la liste. L’historique d’audit reste conservé.</p></div>
      <button type="button" className="ak-danger-button" disabled={busy} onClick={() => setRemoveOpen(true)}><Trash2 size={16}/> Supprimer ce projet</button></div>
    </section>
    <ActionDialog open={archiveOpen} title="Archiver ce projet ?" variant="warning"
      description={`Le projet « ${project.name} » sera archivé. Vous pourrez le restaurer depuis les paramètres.`}
      confirmLabel="Archiver" busy={busy} onClose={() => setArchiveOpen(false)}
      onConfirm={() => { setArchiveOpen(false); void operation(() => projectsApi.archive(projectId), 'Projet archivé.') }}/>
    <ActionDialog open={removeOpen} title="Supprimer définitivement ce projet ?" variant="danger"
      description="Le site sera arrêté et les domaines désactivés. L'historique d'audit reste conservé. Cette opération est irréversible."
      requiredText={project.name} confirmLabel="Supprimer définitivement" busy={busy}
      onClose={() => setRemoveOpen(false)} onConfirm={() => { void removeProject() }}/>
  </>
}
