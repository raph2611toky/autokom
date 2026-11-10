import { useEffect, useMemo, useState } from 'react'
import { ArrowLeft, ArrowRight, CheckCircle2, Download, ExternalLink, GitBranch, Globe2, Info, LoaderCircle, Pencil, Plus, Rocket, Search, ShieldCheck, Trash2, X, XCircle } from 'lucide-react'
import { projectsApi } from '../services/projectsApi.js'
import { errorText, getRemoteProjectDetail, listRemoteProjectRecords } from '../services/projectWorkflow.js'
import { publicSiteUrl, siteStateLabel } from '../services/sitePresentation.js'
import ProjectPublicSite from '../components/ProjectPublicSite.jsx'
import ActionDialog from '../components/ActionDialog.jsx'
import { downloadDnsGuide } from '../services/domainGuide.js'
import { AUTOKOM_DOMAIN_SUFFIX, managedDomainHost, managedDomainPrefix } from '../services/managedDomain.js'

/** Domains are backed by real Django and broker state: no demo or guessed public URLs. */
export default function LiveSites({ navigate, projectId = null }) {
  const [projects, setProjects] = useState([])
  const [detail, setDetail] = useState(null)
  const [domains, setDomains] = useState([])
  const [query, setQuery] = useState('')
  const [hostname, setHostname] = useState('')
  const [domainType, setDomainType] = useState('SUBDOMAIN')
  const [dnsSetup, setDnsSetup] = useState(null)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [domainToDelete, setDomainToDelete] = useState(null)
  const [editingDomain, setEditingDomain] = useState(null)
  const [editHostname, setEditHostname] = useState('')
  const [availability, setAvailability] = useState({ state: 'idle', hostname: '', message: '' })

  async function refreshDomains(info) {
    const actual = info || detail
    if (!actual) return
    const result = await projectsApi.domains(actual.project.id, actual.environment.id)
    setDomains(result?.domains || [])
    setDnsSetup(result?.dns_setup || null)
    // Re-fetch authoritative URL: only backend can declare a domain publicly active.
    const updated = await projectsApi.detail(actual.project.id)
    setDetail(old => old ? { ...old, project: updated.project } : old)
  }

  useEffect(() => {
    let alive = true
    setLoading(true); setError(''); setNotice(''); setDomains([])
    const request = projectId ? getRemoteProjectDetail(projectId) : listRemoteProjectRecords()
    request.then(result => {
      if (!alive) return
      if (projectId) { setDetail(result); setDomains(result.domains || []); setDnsSetup(result.dnsSetup || null) }
      else setProjects(result)
    }).catch(err => { if (alive) setError(errorText(err)) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [projectId])

  useEffect(() => {
    if (!projectId || !detail?.environment?.id) return
    let alive=true
    const timer=window.setInterval(() => {
      projectsApi.domains(detail.project.id,detail.environment.id).then(response => {
        if (!alive) return
        setDomains(response?.domains || [])
        setDnsSetup(response?.dns_setup || null)
        return projectsApi.detail(detail.project.id).then(data => {
          if (alive && data?.project) setDetail(old => old ? {...old,project:data.project} : old)
        })
      }).catch(() => {})
    },20000)
    return () => {alive=false;window.clearInterval(timer)}
  },[projectId,detail?.environment?.id])

  // Advisory availability check: validation runs in Django AND against live
  // Cloudflare DNS, not from a local list of already-loaded projects.
  useEffect(() => {
    if (!editingDomain || !detail?.environment?.id) return
    const selected = domains.find(item => item.id === editingDomain)
    if (!selected || selected.domain_type !== 'SUBDOMAIN') return
    let host
    try {
      host = managedDomainHost(editHostname, dnsSetup?.managed_suffix || AUTOKOM_DOMAIN_SUFFIX)
    } catch (err) {
      setAvailability({state: 'unavailable', hostname: '', message: errorText(err)})
      return
    }
    if (host === selected.hostname) {
      setAvailability({state: 'unchanged', hostname: host, message: 'Saisissez un autre préfixe.'})
      return
    }
    const controller = new AbortController()
    let cancelled = false
    setAvailability({state: 'checking', hostname: host, message: 'Vérification de disponibilité…'})
    const timer = window.setTimeout(() => {
      projectsApi.domainAvailability(detail.project.id, detail.environment.id, selected.id,
        host, {signal: controller.signal})
        .then(result => {
          if (cancelled) return
          const available = result?.available === true
          setAvailability({state: available ? 'available' : 'unavailable', hostname: host,
            message: available ? 'Préfixe disponible dans AUTOKOM et le DNS géré.' : 'Ce préfixe est déjà utilisé ou réservé.'})
        })
        .catch(err => {
          if (!cancelled) setAvailability({state: 'error', hostname: host,
            message: 'Vérification indisponible : réessayez avant de confirmer.'})
        })
    }, 600)
    return () => {cancelled = true; window.clearTimeout(timer); controller.abort()}
  }, [editingDomain, editHostname, detail?.project?.id, detail?.environment?.id, dnsSetup?.managed_suffix,
      domains])

  const visible = useMemo(() => projects.filter(project =>
    `${project.name} ${project.repository} ${project.publicSite?.hostname || ''}`.toLowerCase().includes(query.toLowerCase().trim())), [projects, query])

  async function perform(action, success) {
    if (busy || !detail) return
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await action()
      await refreshDomains()
      setNotice(typeof success === 'function' ? success(result) : success)
    } catch (err) { setError(errorText(err)) }
    finally { setBusy(false) }
  }

  const managedAlreadyReserved = domains.some(domain => domain.domain_type === 'SUBDOMAIN')
  const createDomainType = managedAlreadyReserved ? 'CUSTOM' : domainType

  function beginEdit(domain) {
    setError(''); setNotice('')
    setAvailability({state: 'idle', hostname: '', message: ''})
    setEditingDomain(domain.id)
    setEditHostname(domain.domain_type === 'SUBDOMAIN'
      ? managedDomainPrefix(domain.hostname) : domain.hostname)
  }

  function cancelEdit() {
    setEditingDomain(null)
    setEditHostname('')
    setAvailability({state: 'idle', hostname: '', message: ''})
  }

  async function editDomain(event, domain) {
    event.preventDefault()
    if (editingDomain !== domain.id || busy) return
    let host
    try {
      host = domain.domain_type === 'SUBDOMAIN'
        ? managedDomainHost(editHostname, dnsSetup?.managed_suffix || AUTOKOM_DOMAIN_SUFFIX)
        : editHostname.trim().toLowerCase().replace(/\.$/, '')
      if (host === domain.hostname) {
        cancelEdit()
        return
      }
      if (domain.domain_type === 'SUBDOMAIN' &&
          (availability.state !== 'available' || availability.hostname !== host)) {
        setError('Le nouveau préfixe doit être disponible et vérifié avant modification.')
        return
      }
    } catch (err) { setError(errorText(err)); return }
    await perform(async () => {
      if (domain.domain_type === 'SUBDOMAIN') {
        await projectsApi.renameDomain(detail.project.id, detail.environment.id, domain.id, host)
      } else {
        // Custom DNS belongs to the user. Reserve a replacement without
        // deleting or interrupting the original domain.
        await projectsApi.createDomain(detail.project.id, detail.environment.id, host, 'CUSTOM')
      }
      cancelEdit()
    }, result => domain.domain_type === 'SUBDOMAIN'
      ? (result?.previous_retained
          ? 'Nouveau domaine principal disponible en HTTPS. L’ancienne adresse reste active comme alias, à retirer après vérification.'
          : (domain.https ? 'Nouveau domaine actif en HTTPS. Ancienne adresse retirée après validation.'
              : 'Préfixe réservé. DNS et HTTPS seront configurés à la publication.'))
      : 'Domaine de remplacement réservé. Vérifiez DNS et HTTPS avant de retirer l’ancienne adresse.')
  }

  async function addDomain(event) {
    event.preventDefault()

    let host
    try {
      host = createDomainType === 'SUBDOMAIN'
        ? managedDomainHost(hostname, dnsSetup?.managed_suffix || AUTOKOM_DOMAIN_SUFFIX)
        : hostname.trim().toLowerCase().replace(/\.$/, '')
      if (!host) return
    } catch (err) { setError(errorText(err)); return }
    await perform(async () => {
      await projectsApi.createDomain(detail.project.id, detail.environment.id, host, createDomainType)
      setHostname('')
    }, 'Domaine réservé. AUTOKOM prépare automatiquement DNS et HTTPS pour les sous-domaines gérés.')
  }

  function guide(domain) {
    try { downloadDnsGuide(domain, dnsSetup); setError('') }
    catch (err) { setError(errorText(err)) }
  }

  if (projectId) return <>
    <div className="ak-back-line"><button className="button-quiet" type="button" onClick={() => navigate('/sites')}><ArrowLeft size={17}/> Tous les sites</button></div>
    <section className="page-hero ak-workspace-hero"><div><h1>{detail?.project?.name || 'Site & domaines'}</h1><p>Gérer les adresses publiques et les certificats HTTPS.</p></div><button className="button-outline" type="button" onClick={() => navigate(`/projects/${projectId}`)}>Détails du projet <ArrowRight size={16}/></button></section>
    {loading && <p role="status"><LoaderCircle size={17} className="ak-spin"/> Chargement…</p>}
    {error && <p className="feedback error" role="alert">{error}</p>}
    {notice && <p className="feedback success" role="status">{notice}</p>}
    {detail && <>
      <ProjectPublicSite site={detail.project.public_site}/>
      <section className="ak-workspace-sheet ak-site-detail">
        <div className="ak-workspace-sheet-heading"><div><h2>Domaines configurés</h2><p className="ak-section-subtitle">AUTOKOM active automatiquement les sous-domaines gérés après publication. Une adresse devient cliquable une fois HTTPS réellement actif.</p></div><span className="ak-project-state">{domains.length} domaine(s)</span></div>
        {!domains.length && <p className="gh-muted">Aucun domaine réservé.</p>}
        {domains.map(domain => {
          const url = publicSiteUrl(domain)
          return <article className="ak-domain-entry" key={domain.id}>
            <div className="ak-domain-entry-head"><div><strong>{domain.hostname}</strong> {domain.is_primary && <span className="ak-project-state">Principal</span>}<p className="ak-section-subtitle">DNS : {domain.status} · HTTPS : {domain.https ? 'Actif' : 'Activation automatique en cours'}</p>{domain.activation_error_code && <p className="ak-domain-diagnostic">Diagnostic : {domain.activation_error_code}. {domain.activation_error_code === 'AUTOKOM_DOMAIN_OLD_ALIAS_CLEANUP_PENDING' ? 'Retrait de l’ancienne adresse à finaliser depuis la gestion des domaines.' : 'Nouvelle tentative automatique programmée.'}</p>}</div>
              <div className="ak-domain-entry-actions">
                <button type="button" className="button-outline" disabled={!dnsSetup?.ingress_ipv4} onClick={() => guide(domain)} title="Guide avec l'adresse IP publique actuelle"><Download size={15}/> Guide DNS</button>
                {url && <a href={url} className="button-outline" target="_blank" rel="noopener noreferrer"><ExternalLink size={15}/> Ouvrir</a>}
              </div>
            </div>
            {!domain.https && domain.verification?.type === 'TXT' && <div className="ak-domain-proof"><strong>Preuve DNS requise :</strong> TXT {domain.verification.hostname} = {domain.verification.value}<p>Enregistrement A : {domain.hostname} → {dnsSetup?.ingress_ipv4 || 'IP publique indisponible'}. Le proxy DNS doit rester désactivé.</p></div>}
            <div className="ak-domain-entry-actions">
              {!domain.https && <button type="button" className="button-outline" disabled={busy} onClick={() => perform(() => projectsApi.verifyDomain(detail.project.id, detail.environment.id, domain.id), 'Vérification DNS/HTTPS demandée.')}><ShieldCheck size={15}/> Activer HTTPS</button>}
              {domain.https && !domain.is_primary && <button type="button" className="button-outline" disabled={busy} onClick={() => perform(() => projectsApi.primaryDomain(detail.project.id, detail.environment.id, domain.id), 'Adresse principale modifiée.')}><CheckCircle2 size={15}/> Définir comme principal</button>}
              <button type="button" className="button-outline" disabled={busy || editingDomain === domain.id} onClick={() => beginEdit(domain)}><Pencil size={15}/> Modifier</button>
              <button type="button" className="button-quiet ak-danger-action" disabled={busy || domain.is_primary || domains.length <= 1}
                title={domain.is_primary ? 'Définissez un autre domaine principal avant suppression.' : domains.length <= 1 ? 'Un domaine minimum : ajoutez une autre adresse avant de supprimer le dernier.' : 'Suppression définitive contrôlée'}
                onClick={() => setDomainToDelete(domain)}><Trash2 size={15}/> Supprimer</button>
            </div>
            {editingDomain === domain.id && <form className="ak-domain-inline-edit" onSubmit={event => editDomain(event, domain)}>
              <label className="ak-domain-field" htmlFor={`edit-domain-${domain.id}`}>
                {domain.domain_type === 'SUBDOMAIN' ? 'Modifier le préfixe de cette adresse' : 'Nouvelle adresse externe de remplacement'}
              </label>
              {domain.domain_type === 'SUBDOMAIN' ? <>
                <div className={`ak-managed-host-input ak-availability-${availability.state}`}>
                  <input id={`edit-domain-${domain.id}`} autoFocus value={editHostname} onChange={event => setEditHostname(event.target.value.toLowerCase())}
                    maxLength={63} required placeholder="nouveau-prefixe" aria-label="Nouveau préfixe AUTOKOM"
                    aria-invalid={availability.state === 'unavailable'} aria-describedby={`edit-availability-${domain.id}`}/>
                  <span className="ak-availability-icon" aria-hidden="true">
                    {availability.state === 'checking' ? <LoaderCircle className="ak-spin" size={17}/> :
                     availability.state === 'available' ? <CheckCircle2 size={17}/> :
                     availability.state === 'unavailable' ? <XCircle size={17}/> : null}
                  </span>
                  <span className="ak-managed-host-suffix">.{AUTOKOM_DOMAIN_SUFFIX}</span>
                </div>
                <p id={`edit-availability-${domain.id}`} className={`ak-domain-availability-message ak-availability-${availability.state}`} role="status" aria-live="polite">
                  {availability.message || 'Saisissez un nouveau préfixe pour vérifier sa disponibilité.'}
                </p>
              </> : <input id={`edit-domain-${domain.id}`} autoFocus value={editHostname} onChange={event => setEditHostname(event.target.value)}
                maxLength={253} required aria-label="Nouveau domaine externe" />}
              <p className="ak-section-subtitle">{domain.domain_type === 'SUBDOMAIN'
                ? (domain.https
                    ? 'Le nouveau domaine sera activé et vérifié en HTTPS avant la bascule. L’ancien reste disponible tant que le nouveau ne fonctionne pas.'
                    : 'AUTOKOM vérifie le préfixe et conserve toute adresse active jusqu’à la mise en service de la nouvelle.')
                : 'L’ancien domaine reste inchangé. Activez DNS/HTTPS du nouveau domaine avant de retirer l’ancien.'}</p>
              <div className="ak-domain-entry-actions">
                <button type="submit" className="button-primary" disabled={busy || !editHostname.trim() ||
                  (domain.domain_type === 'SUBDOMAIN' && availability.state !== 'available')}>
                  {busy ? <LoaderCircle size={15} className="ak-spin"/> : <CheckCircle2 size={15}/>}
                  {busy ? 'Vérification DNS et HTTPS…' : 'Enregistrer'}</button>
                <button type="button" className="button-outline" onClick={cancelEdit} disabled={busy}><X size={15}/> Annuler</button>
              </div>
            </form>}
          </article>
        })}
        <ActionDialog open={Boolean(domainToDelete)} title="Supprimer définitivement ce domaine ?"
          description={domainToDelete ? `${domainToDelete.hostname} sera retiré du projet, du reverse proxy AUTOKOM et du DNS géré s'il appartient à AUTOKOM. Ce retrait ne supprime pas les enregistrements de votre fournisseur DNS externe. Cette action est irréversible.` : ''}
          variant="warning" confirmLabel="Supprimer définitivement" requiredText={domainToDelete?.hostname || ''} busy={busy}
          onClose={() => setDomainToDelete(null)}
          onConfirm={() => { const candidate = domainToDelete; setDomainToDelete(null); if (candidate) void perform(() => projectsApi.deleteDomain(detail.project.id, detail.environment.id, candidate.id), 'Domaine supprimé définitivement.') }}/>
        <h3 id="ak-domain-form">Ajouter une adresse</h3>
        <p className="ak-section-subtitle">{managedAlreadyReserved
          ? 'Ce projet possède déjà un sous-domaine AUTOKOM : utilisez Modifier directement sur le domaine. Vous pouvez encore ajouter un domaine externe.'
          : 'Les sous-domaines AUTOKOM sont créés automatiquement. Les domaines externes exigent votre configuration DNS.'}</p>
        <div className="ak-domain-instructions">
          <strong>Informations DNS</strong>
          <p>Adresse IPv4 publique d'AUTOKOM : <code>{dnsSetup?.ingress_ipv4 || 'Indisponible'}</code> · Suffixe géré : {dnsSetup?.managed_suffix || 'indisponible'}.</p>
          <p>Type A : pointe le nom vers l'IP du serveur. Type TXT : prouve que le domaine externe vous appartient. CNAME n'est pas utilisé pour cette validation.</p>
        </div>
        <form onSubmit={addDomain} className="ak-domain-manager-form ak-domain-form-details">
          <label className="ak-domain-field">Type d'adresse
            <select value={createDomainType} onChange={event => {setDomainType(event.target.value);setHostname('')}} aria-label="Type du domaine">
              {!managedAlreadyReserved && <option value="SUBDOMAIN">Sous-domaine AUTOKOM (géré automatiquement)</option>}
              <option value="CUSTOM">Domaine personnel (DNS externe)</option>
            </select>
          </label>
          <div className="ak-domain-field"><label htmlFor="ak-domain-host">{createDomainType === 'SUBDOMAIN' ? 'Préfixe du sous-domaine' : 'Nom de domaine complet'}</label>
            <details className="ak-domain-info"><summary><Info size={15}/> Comment configurer le domaine ?</summary>
              {createDomainType === 'CUSTOM' ? <p>Créez les enregistrements A et TXT chez votre fournisseur DNS, puis AUTOKOM vérifiera le nom avant d'activer HTTPS.</p>
                : <p>Seul le préfixe change. Le suffixe {AUTOKOM_DOMAIN_SUFFIX} est fixe, et AUTOKOM configure son DNS.</p>}
            </details>
            {createDomainType === 'SUBDOMAIN' ? <div className="ak-managed-host-input"><input id="ak-domain-host" value={hostname} onChange={event => setHostname(event.target.value.toLowerCase())} maxLength={63} required placeholder="mon-portfolio" aria-label="Préfixe du sous-domaine AUTOKOM"/><span>.{AUTOKOM_DOMAIN_SUFFIX}</span></div>
              : <input id="ak-domain-host" value={hostname} onChange={event => setHostname(event.target.value)} maxLength={253} required placeholder="www.monsite.mg ou monsite.mg" />}
          </div>
          <div className="ak-domain-form-actions">
            <button type="submit" className="button-primary" disabled={busy || !hostname.trim()}>{busy ? <LoaderCircle size={15} className="ak-spin"/> : <Plus size={15}/>} Ajouter le domaine</button>
            <button type="button" className="button-outline" disabled={!dnsSetup?.ingress_ipv4 || !hostname.trim()} onClick={() => {try {const host=createDomainType === 'SUBDOMAIN' ? managedDomainHost(hostname) : hostname.trim().toLowerCase();guide({hostname:host,domain_type:createDomainType})}catch(err){setError(errorText(err))}}}><Download size={15}/> Télécharger le guide DNS</button>
          </div>
        </form>
      </section>
    </>}
  </>
  return <>
    <section className="page-hero ak-sites-hero"><div><h1>Sites &amp; domaines</h1><p>Adresses publiques réellement activées, DNS et HTTPS.</p></div><button className="button-primary" onClick={() => navigate('/deploy')} type="button"><Rocket size={17}/> Nouveau projet</button></section>
    <section className="ak-workspace-sheet ak-sites-simple-list">
      <div className="ak-workspace-toolbar"><div className="search-field"><Search size={17}/><input value={query} onChange={event => setQuery(event.target.value)} placeholder="Rechercher un projet ou domaine" aria-label="Rechercher un site ou projet"/></div></div>
      {error && <p role="alert" className="feedback error">{error}</p>}
      {loading ? <div className="ak-workspace-empty" role="status"><LoaderCircle className="ak-spin" size={19}/> Chargement des domaines AUTOKOM…</div> : visible.length ? <div className="ak-project-list">{visible.map(project => <div className="ak-project-row" key={project.id}>
        <span className="ak-project-row-symbol"><Globe2 size={20}/></span><button type="button" className="ak-project-row-main" onClick={() => navigate(`/sites/${project.id}`)}><strong>{project.name}</strong><small><GitBranch size={13}/>{project.repository} · {project.branch}</small></button>
        <div className="ak-project-row-public">{publicSiteUrl(project.publicSite) ? <a href={publicSiteUrl(project.publicSite)} target="_blank" rel="noopener noreferrer">{project.publicSite.hostname} ↗</a> : <span>{project.publicSite?.hostname || 'Domaine non défini'} · {siteStateLabel(project.publicSite)}</span>}</div>
        <button className="button-outline" type="button" onClick={() => navigate(`/sites/${project.id}`)}>Gérer <ArrowRight size={16}/></button>
      </div>)}</div> : <div className="ak-workspace-empty"><Globe2 size={29}/><h2>Aucun projet</h2><p>Les sites apparaîtront ici après création du premier projet.</p></div>}
    </section>
  </>
}
