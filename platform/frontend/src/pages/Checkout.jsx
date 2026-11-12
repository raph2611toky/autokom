import { ArrowLeft, ArrowRight, ShieldCheck } from 'lucide-react'
import '../styles/subscription.css'

// Public checkout is deliberately unavailable until a real payment provider is
// integrated. Never create a paid-looking entitlement from this page.
export default function Checkout({ navigate }) {
  return <section className="ak-subscription-sheet">
    <button type="button" className="button-quiet" onClick={() => navigate('/pricing')}>
      <ArrowLeft size={17}/> Les offres
    </button>
    <div className="ak-subscription-header">
      <h1>Offre gratuite de lancement</h1>
      <p>Le paiement en ligne n'est pas encore disponible. Vous pouvez publier votre application pendant une durée limitée, sans renseigner de moyen de paiement.</p>
    </div>
    <div className="ak-subscription-summary">
      <h2><ShieldCheck size={21}/> Fonctionnalités incluses</h2>
      <p>DevOps et DevSecOps, site frontend ou application frontend et backend, avec une option haute disponibilité.</p>
      <p>Durée : 30 jours pour un frontend seul, 15 jours avec backend. La haute disponibilité réduit ces durées à 15 et 7 jours.</p>
    </div>
    <button className="button-primary" type="button" onClick={() => navigate('/deploy')}>
      Configurer mon dépôt GitHub <ArrowRight size={17}/>
    </button>
  </section>
}
