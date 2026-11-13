import { Cpu, HardDrive, Server } from 'lucide-react'
import { estimateResources } from '../services/resourceEstimate.js'

const fr = new Intl.NumberFormat('fr-FR')
export default function ResourceEstimate({ components, highAvailability }) {
  const estimate = estimateResources(components, { highAvailability })
  if (!estimate) return null
  const { total, services, replicas } = estimate
  return <section className="ak-stack-component ak-resource-estimate" aria-label="Ressources recommandées">
    <div className="ak-stack-inline">
      <h3>Ressources recommandées</h3>
      <span className="ak-stack-common-tag">{replicas} {replicas === 1 ? 'réplique par service' : 'répliques par service'}</span>
    </div>
    <div className="ak-review-list">
      <div><Cpu size={17}/><span>Processeur</span><strong>{fr.format(total.cpu / 1000)} vCPU</strong></div>
      <div><Server size={17}/><span>Mémoire</span><strong>{fr.format(total.ram)} Mio</strong></div>
      <div><HardDrive size={17}/><span>Stockage</span><strong>{fr.format(total.disk)} Gio</strong></div>
    </div>
    <p className="ak-stack-subtle">{services.map(s => s.label ? `${s.label} · ${s.basis}` : s.basis).join(' + ')}</p>
    <p className="ak-stack-subtle">Estimation de départ calculée à partir de la configuration du projet. Les quotas de l’offre restent inchangés.</p>
  </section>
}
