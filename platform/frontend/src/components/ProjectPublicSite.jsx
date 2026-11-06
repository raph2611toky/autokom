import { ExternalLink, Globe2, Copy, Settings2 } from 'lucide-react'
import { publicSiteUrl, siteStateLabel } from '../services/sitePresentation.js'

export default function ProjectPublicSite({ site, onManage, compact = false }) {
  const url = publicSiteUrl(site)
  return <div className={`ak-public-site ${compact ? 'compact' : ''}`}>
    <div className="ak-public-site-text">
      <strong><Globe2 size={16}/> Site public</strong>
      <div className="ak-public-site-address">{url ? <a href={url} target="_blank" rel="noopener noreferrer">{site.hostname}<ExternalLink size={15}/></a> : <span>{site?.hostname || 'Aucun domaine'}</span>}</div>
      <small>{siteStateLabel(site)}</small>
    </div>
    <div className="ak-public-site-actions">
      {url && <>
        <a className="button-outline" href={url} target="_blank" rel="noopener noreferrer"><ExternalLink size={15}/> Voir le site</a>
        <button className="button-outline" type="button" onClick={() => navigator.clipboard?.writeText(url)}><Copy size={15}/> Copier</button>
      </>}
      {onManage && <button className="button-outline" type="button" onClick={onManage}><Settings2 size={15}/> Domaines</button>}
    </div>
  </div>
}
