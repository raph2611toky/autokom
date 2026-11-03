import { useMemo, useState } from 'react'
import { Check, ExternalLink, ShieldAlert } from 'lucide-react'
import { SERVICE_GROUPS, SERVICE_TIER_LABELS, resolveCapability } from '../config/serviceCatalog.js'

const FILTERS = [{ id: 'all', label: 'Toutes' }, { id: 'libre', label: 'Libres' }, { id: 'quota', label: 'Offre gratuite' }, { id: 'payant', label: 'Payantes' }]

export default function ServiceCatalog({ services, onChange, capabilities, capabilitiesLoaded }) {
  const [filter, setFilter] = useState('all')
  const groups = useMemo(() => SERVICE_GROUPS.map((group) => ({ ...group, options: group.options.filter((item) => filter === 'all' || item.tier === filter) })).filter((group) => group.options.length), [filter])

  return <section className="service-catalog" aria-labelledby="services-heading">
    <div className="services-heading"><div><h3 id="services-heading">Services du projet</h3><p>Choisissez vos préférences. L’activation est soumise aux capacités du serveur, aux quotas et aux politiques de l’administrateur.</p></div></div>
    <div className="service-filter" role="group" aria-label="Filtrer les services par type d’offre">{FILTERS.map((item) => <button type="button" key={item.id} className={filter === item.id ? 'active' : ''} aria-pressed={filter === item.id} onClick={() => setFilter(item.id)}>{item.label}</button>)}</div>
    {groups.map((group) => <div className="service-group" key={group.id}>
      <div className="service-group-heading"><h4>{group.title}</h4><p>{group.summary}</p></div>
      <div className="service-options">{group.options.map((item) => {
        const selected = services[group.id] === item.id
        const status = resolveCapability(capabilities, item.capability)
        return <div key={`${group.id}-${item.id}`} className={`service-option ${selected ? 'selected' : ''}`}>
          <button type="button" className="service-option-select" aria-pressed={selected} onClick={() => onChange(group.id, item.id)}>
            <span className="service-choice">{selected && <Check size={13}/>}</span>
            <span className="service-option-main"><strong>{item.name}</strong><span className={`service-tier tier-${item.tier}`}>{SERVICE_TIER_LABELS[item.tier]}</span><small>{item.detail}</small></span>
          </button>
          <div className="service-option-footer"><span className={`service-availability status-${status}`}>
            {status === 'not-required' ? 'Aucune intégration requise' : !capabilitiesLoaded ? 'Disponibilité non vérifiée' : status === 'healthy' ? 'Déclaré disponible' : status === 'disabled' ? 'Désactivé par l’admin' : status === 'unknown' ? 'Non déclaré par AUTOKOM' : 'Non disponible'}
          </span>{item.url && <a href={item.url} target="_blank" rel="noopener noreferrer" aria-label={`Offre et conditions ${item.name}`}>Conditions <ExternalLink size={13}/></a>}</div>
        </div>
      })}</div>
    </div>)}
    <div className="service-legal"><ShieldAlert size={18}/><span>Le choix d’une offre payante ne crée aucun abonnement, paiement ou service. Aucun prix n’est garanti par AUTOKOM. Des coûts d’hébergement peuvent exister même pour les logiciels libres. Les services requis par la politique de sécurité restent obligatoires.</span></div>
  </section>
}
