import { useMemo, useState } from 'react'
import { ArrowLeft, ArrowRight, FolderKanban, GitBranch, Plus, Search, Settings2, Trash2, Workflow } from 'lucide-react'
import { findPricingPlan } from '../config/pricing.js'
import { getProjectRecords, removeProject } from '../services/projectWorkspace.js'
import { API_AVAILABLE } from '../config/runtime.js'
import LiveProjects from './LiveProjects.jsx'
import ActionDialog from '../components/ActionDialog.jsx'

function goToConfiguration(project, navigate) {
  if (project.draft) {
    try { localStorage.setItem('autokom.ui.editDraft', project.id) } catch { /* Mode navigation sans stockage. */ }
  }
  navigate('/deploy')
}

function formatDate(value) {
  if (!value) return 'Non renseignée'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? 'Non renseignée' : new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'short', year: 'numeric' }).format(parsed)
}

export default function Projects(props) {
  return API_AVAILABLE ? <LiveProjects {...props}/> : <DemoProjects {...props}/>
}

function DemoProjects({ navigate, projectId = null }) {
  const [projects, setProjects] = useState(getProjectRecords)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const [pendingDelete, setPendingDelete] = useState(null)
  const [deleteError, setDeleteError] = useState('')
  const selected = projects.find(p => p.id === projectId)
  const visible = useMemo(() => projects.filter(p => {
    const matchesFilter = filter === 'all' || (filter === 'pipeline' ? Boolean(p.preview) : !p.preview)
    return matchesFilter && [p.name,p.repository,p.branch,p.workdir].join(' ').toLowerCase().includes(query.trim().toLowerCase())
  }),[projects,query,filter])

  function deleteProject(project) {
    try {
      removeProject(project.id)
      setProjects(getProjectRecords())
      if (projectId === project.id) navigate('/projects')
      setPendingDelete(null)
    } catch { setDeleteError('Le projet n’a pas pu être supprimé.'); setPendingDelete(null) }
  }

  const confirmDialog = <ActionDialog open={Boolean(pendingDelete)} title="Supprimer le projet ?"
    description={pendingDelete ? `« ${pendingDelete.name} » sera retiré de ce navigateur.` : ''}
    variant="danger" confirmLabel="Supprimer" onClose={() => setPendingDelete(null)}
    onConfirm={() => pendingDelete && deleteProject(pendingDelete)}/>

  if (projectId) {
    if (!selected) return <section className="ak-workspace-empty"><FolderKanban size={30}/><h1>Projet introuvable</h1><button className="button-outline" onClick={() => navigate('/projects')}>Mes projets</button></section>
    return <>
      {confirmDialog}
      {deleteError && <p className="feedback error" role="alert">{deleteError}</p>}
      <div className="ak-back-line"><button type="button" className="button-quiet" onClick={() => navigate('/projects')}><ArrowLeft size={17}/> Mes projets</button></div>
      <section className="page-hero ak-workspace-hero"><div><h1>{selected.name}</h1><p>{selected.repository} · {selected.branch}</p></div><button className="button-primary" type="button" onClick={() => goToConfiguration(selected,navigate)}><Settings2 size={17}/> Modifier</button></section>
      <section className="ak-workspace-sheet">
        <div className="ak-workspace-sheet-heading"><h2>Configuration du projet</h2><span className="ak-project-state">{selected.preview ? 'Pipeline préparé' : 'Brouillon enregistré'}</span></div>
        <dl className="ak-site-info-grid ak-workspace-details">
          <div><dt>Dépôt GitHub</dt><dd>{selected.repository || 'Non défini'}</dd></div>
          <div><dt>Branche</dt><dd>{selected.branch || 'Non définie'}</dd></div>
          <div><dt>Répertoire source</dt><dd>{selected.workdir}</dd></div>
          <div><dt>Environnement</dt><dd>{selected.environment}</dd></div>
          <div><dt>Mode du pipeline</dt><dd>{selected.mode === 'devops' ? 'DevOps' : 'DevSecOps'}</dd></div>
          <div><dt>Offre</dt><dd>{findPricingPlan(selected.pricingPlan).name}</dd></div>
          <div><dt>Dernière modification</dt><dd>{formatDate(selected.updatedAt)}</dd></div>
          <div><dt>Publication</dt><dd>Aucune publication réelle confirmée</dd></div>
        </dl>
        <div className="ak-project-detail-actions">
          {selected.preview ? <button type="button" className="button-primary" onClick={() => navigate(`/sites/${encodeURIComponent(selected.id)}`)}><Workflow size={16}/> Pipeline et logs <ArrowRight size={15}/></button> : <button type="button" className="button-outline" onClick={() => goToConfiguration(selected,navigate)}>Préparer le pipeline <ArrowRight size={15}/></button>}
          <button type="button" className="button-quiet ak-danger-action" onClick={() => setPendingDelete(selected)}><Trash2 size={16}/> Supprimer</button>
        </div>
      </section>
    </>
  }

  return <>
    {confirmDialog}
    {deleteError && <p className="feedback error" role="alert">{deleteError}</p>}
    <section className="page-hero ak-workspace-hero"><div><h1>Mes projets</h1><p>Retrouvez vos configurations et reprenez leur préparation.</p></div><button className="button-primary" type="button" onClick={() => navigate('/deploy')}><Plus size={17}/> Nouveau projet</button></section>
    <section className="ak-workspace-sheet">
      <div className="ak-workspace-toolbar">
        <div className="search-field"><Search size={17}/><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Rechercher un projet" aria-label="Rechercher un projet"/></div>
        <div className="ak-workspace-filters" aria-label="Filtrer les projets">
          {[['all','Tous'],['pipeline','Pipeline préparé'],['draft','Brouillons']].map(([value,label]) => <button key={value} type="button" className={filter===value?'active':''} aria-pressed={filter===value} onClick={() => setFilter(value)}>{label}</button>)}
        </div>
      </div>
      {visible.length > 0 ? <div className="ak-project-list">{visible.map(project => <div className="ak-project-row" key={project.id}>
        <div className="ak-project-row-symbol"><FolderKanban size={21}/></div>
        <button type="button" className="ak-project-row-main" onClick={() => navigate(`/projects/${encodeURIComponent(project.id)}`)}><strong>{project.name}</strong><small><GitBranch size={13}/>{project.repository} · {project.branch}</small></button>
        <span className="ak-project-state">{project.preview ? 'Pipeline préparé' : 'Brouillon'}</span>
        <button type="button" className="ak-project-open" onClick={() => navigate(`/projects/${encodeURIComponent(project.id)}`)} aria-label={`Ouvrir ${project.name}`}><ArrowRight size={19}/></button>
      </div>)}</div> : <div className="ak-workspace-empty"><FolderKanban size={29}/><h2>{projects.length ? 'Aucun résultat' : 'Aucun projet pour le moment'}</h2><p>{projects.length ? 'Essayez une autre recherche ou un autre filtre.' : 'Créez votre premier projet pour le retrouver ici.'}</p>{!projects.length && <button type="button" className="button-primary" onClick={() => navigate('/deploy')}>Créer un projet <ArrowRight size={16}/></button>}</div>}
    </section>
  </>
}
