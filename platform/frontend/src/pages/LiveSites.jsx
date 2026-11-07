import { useEffect, useMemo, useState } from 'react'
import { ArrowLeft, ArrowRight, CheckCircle2, Download, ExternalLink, GitBranch, Globe2, Info, LoaderCircle, Plus, Rocket, Search, ShieldCheck } from 'lucide-react'
import { projectsApi } from '../services/projectsApi.js'
import { errorText, getRemoteProjectDetail, listRemoteProjectRecords } from '../services/projectWorkflow.js'
import { publicSiteUrl, siteStateLabel } from '../services/sitePresentation.js'
import ProjectPublicSite from '../components/ProjectPublicSite.jsx'
import { downloadDnsGuide } from '../services/domainGuide.js'

/** Domains are backed by real Django and broker state: no demo or guessed public URLs. */
export default function LiveSites({ navigate, projectId = null }) {
  const [projects, setProjects] = useState([])
  const [detail, setDetail] = useState(null)
  const [domains, setDomains] = useState([])
  const [query, setQuery] = useState('')
  const [hostname, setHostname] = useState('')
  const [domainType, setDomainType] = useState('CUSTOM')
  const [dnsSetup, setDnsSetup] = useState(null)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

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

  const visible = useMemo(() => projects.filter(project =>
    `${project.name} ${project.repository} ${project.publicSite?.hostname || ''}`.toLowerCase().includes(query.toLowerCase().trim())), [projects, query])

  async function perform(action, success) {
    if (busy || !detail) return
    setBusy(true); setError(''); setNotice('')
    try {
      await action()
      await refreshDomains()
      setNotice(success)
    } catch (err) { setError(errorText(err)) }
    finally { setBusy(false) }
  }

  async function addDomain(event) {
    event.preventDefault()
    const host = hostname.trim().toLowerCase().replace(/\.$/, '')
    if (!host) return
    await perform(async () => {
      await projectsApi.createDomain(detail.project.id, detail.environment.id, host, domainType)
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
            <div className="ak-domain-entry-head"><div><strong>{domain.hostname}</strong> {domain.is_primary && <span className="ak-project-state">Principal</span>}<p className="ak-section-subtitle">DNS : {domain.status} · HTTPS : {domain.https ? 'Actif' : 'Activation automatique en cours'}</p>{domain.activation_error_code && <p className="ak-domain-diagnostic">Diagnostic : {domain.activation_error_code}. Nouvelle tentative automatique programmée.</p>}</div>
              <div className="ak-domain-entry-actions">
                <button type="button" className="button-outline" disabled={!dnsSetup?.ingress_ipv4} onClick={() => guide(domain)} title="Guide avec l'adresse IP publique actuelle"><Download size={15}/> Guide DNS</button>
                {url && <a href={url} className="button-outline" target="_blank" rel="noopener noreferrer"><ExternalLink size={15}/> Ouvrir</a>}
              </div>
            </div>
            {!domain.https && domain.verification?.type === 'TXT' && <div className="ak-domain-proof"><strong>Preuve DNS requise :</strong> TXT {domain.verification.hostname} = {domain.verification.value}<p>Enregistrement A : {domain.hostname} → {dnsSetup?.ingress_ipv4 || 'IP publique indisponible'}. Le proxy DNS doit rester désactivé.</p></div>}
            <div className="ak-domain-entry-actions">
              {!domain.https && <button type="button" className="button-outline" disabled={busy} onClick={() => perform(() => projectsApi.verifyDomain(detail.project.id, detail.environment.id, domain.id), 'Vérification DNS/HTTPS demandée.')}><ShieldCheck size={15}/> Activer HTTPS</button>}
              {domain.https && !domain.is_primary && <button type="button" className="button-outline" disabled={busy} onClick={() => perform(() => projectsApi.primaryDomain(detail.project.id, detail.environment.id, domain.id), 'Adresse principale modifiée.')}><CheckCircle2 size={15}/> Définir comme principal</button>}
              {domain.https && !domain.is_primary && <button type="button" className="button-quiet ak-danger-action" disabled={busy} onClick={() => {
                if (window.confirm(`Désactiver ${domain.hostname} ? Le nom restera réservé pour éviter son usurpation.`)) perform(() => projectsApi.deactivateDomain(detail.project.id, detail.environment.id, domain.id), 'Domaine désactivé et réservé.')
              }}>Désactiver</button>}
            </div>
          </article>
        })}
        <h3>Ajouter ou changer d’adresse</h3>
        <p className="ak-section-subtitle">Ajoutez d’abord le nouveau domaine. Une fois HTTPS actif, définissez-le comme principal : l’ancienne adresse reste opérationnelle jusqu’à sa désactivation.</p>
        <div className="ak-domain-instructions">
          <strong>Informations DNS</strong>
          <p>Adresse IPv4 publique d'AUTOKOM : <code>{dnsSetup?.ingress_ipv4 || 'Indisponible'}</code> · Suffixe géré : {dnsSetup?.managed_suffix || 'indisponible'}.</p>
          <p>Type A : pointe le nom vers l'IP du serveur. Type TXT : prouve que le domaine externe vous appartient. CNAME n'est pas utilisé pour cette validation.</p>
        </div>
        <form onSubmit={addDomain} className="ak-domain-manager-form ak-domain-form-details">
          <label className="ak-domain-field">Type d'adresse
            <select value={domainType} onChange={event => setDomainType(event.target.value)} aria-label="Type du domaine">
              <option value="CUSTOM">Domaine personnel (DNS externe)</option>
              <option value="SUBDOMAIN">Sous-domaine AUTOKOM (géré automatiquement)</option>
            </select>
          </label>
          <div className="ak-domain-field"><label htmlFor="ak-domain-host">Nom de domaine complet</label>
            <details className="ak-domain-info"><summary><Info size={15}/> Comment configurer le domaine ?</summary>
              {domainType === 'CUSTOM' ? <p>Après ajout, créez chez votre fournisseur DNS un A vers l'IP affichée et le TXT de propriété indiqué dans la fiche. Ensuite AUTOKOM activera HTTPS.</p>
                : <p>Choisissez un nom sous {dnsSetup?.managed_suffix || 'le suffixe AUTOKOM'}. AUTOKOM gère lui-même son enregistrement A, sa validation et HTTPS.</p>}
            </details>
            <input id="ak-domain-host" value={hostname} onChange={event => setHostname(event.target.value)} maxLength={253} required placeholder={domainType === 'SUBDOMAIN' ? `exemple.${dnsSetup?.managed_suffix || 'auto.ikom.mg'}` : 'www.monsite.mg ou monsite.mg'} />
          </div>
          <div className="ak-domain-form-actions"><button type="submit" className="button-primary" disabled={busy || !hostname.trim()}>{busy ? <LoaderCircle size={15} className="ak-spin"/> : <Plus size={15}/>} Ajouter le domaine</button>
            <button type="button" className="button-outline" disabled={!dnsSetup?.ingress_ipv4 || !hostname.trim()} onClick={() => guide({hostname:hostname.trim().toLowerCase(),domain_type:domainType})}><Download size={15}/> Télécharger le guide DNS</button>
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
