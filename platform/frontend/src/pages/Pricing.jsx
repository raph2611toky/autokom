import { useEffect, useState } from 'react'
import { ArrowRight, Check, CheckCircle2, CircleHelp, GitBranch, ShieldCheck, Server, Workflow } from 'lucide-react'
import { billingApi } from '../services/billingApi.js'
import { API_AVAILABLE } from '../config/runtime.js'
import { useAuth } from '../context/AuthContext.jsx'
import '../styles/pricing.css'

const configurations = [
  { id: 'frontend', title: 'Frontend uniquement', standard: '30 jours', ha: '15 jours',
    description: 'React, Vite, site statique ou autre profil frontend pris en charge.' },
  { id: 'stack', title: 'Frontend + backend', standard: '15 jours', ha: '7 jours',
    description: 'Même dépôt GitHub ; backend ajouté sans perdre la configuration frontend.' },
]
const questions = [
  { question: 'Dois-je payer pour publier ?', answer: 'Non. L’offre gratuite de lancement est disponible sans paiement. Votre durée commence à la première mise en ligne réussie.' },
  { question: 'Puis-je changer de dépôt GitHub ?', answer: 'Avant le premier déploiement, oui. Après la première publication, le compte GitHub reste lié au dépôt publié, même si le projet est supprimé.' },
  { question: 'Puis-je ajouter mon backend ensuite ?', answer: 'Oui, dans le même dépôt. L’échéance est recalculée à 15 jours depuis la première publication, ou 7 jours avec haute disponibilité ; elle n’est jamais remise à zéro.' },
  { question: 'Que se passe-t-il à expiration ?', answer: 'Les accès publics sont suspendus. Les conteneurs restent conservés pendant 24 heures ; la suppression définitive dépendra de la disponibilité d’un parcours de renouvellement.' },
]
export function activePricingPlan(subscription) {
  if (subscription?.launch_offer) return 'launch'
  return subscription?.active ? subscription?.plan : 'free'
}
export default function Pricing({ navigate }) {
  const [subscription, setSubscription] = useState(null)
  const { status } = useAuth()
  useEffect(() => {
    if (!API_AVAILABLE || status !== 'authenticated') { setSubscription(null); return }
    let live = true
    billingApi.account().then(result => { if (live) setSubscription(result?.subscription || null) })
      .catch(() => { if (live) setSubscription(null) })
    return () => { live = false }
  }, [status])
  const launch = subscription?.launch_offer
  return <div className="ak-pricing-page">
    <section className="ak-pricing-hero" aria-labelledby="ak-pricing-title">
      <div className="ak-pricing-heading">
        <h1 id="ak-pricing-title">Offre gratuite de lancement</h1>
        <p>Déployez votre application avec DevOps ou DevSecOps, avec ou sans haute disponibilité. Aucun paiement n’est demandé pour cette offre temporaire.</p>
      </div>
    </section>
    <div className="ak-pricing-cards">
      {configurations.map(item => <article key={item.id} className="ak-pricing-card" aria-label={item.title}>
        <div className="ak-pricing-card-top"><div><h2>{item.title}</h2><p>{item.description}</p></div></div>
        <div className="ak-pricing-amount"><strong>Gratuit</strong></div>
        <span className="ak-pricing-price-caption">{item.standard} sans haute disponibilité · {item.ha} avec haute disponibilité</span>
        <ul className="ak-pricing-benefits">
          {['DevOps et DevSecOps', 'Domaines AUTOKOM', 'Logs de publication', 'Option haute disponibilité sur deux workers'].map(feature =>
            <li key={feature}><Check size={16} aria-hidden="true"/><span>{feature}</span></li>)}
        </ul>
        <button className="ak-pricing-select is-primary" type="button" onClick={() => navigate('/deploy')}>
          Choisir mon dépôt <ArrowRight size={18}/>
        </button>
      </article>)}
    </div>
    {launch && <section className="ak-pricing-compare" aria-label="Mon offre">
      <h2>{launch.unlimited ? 'Accès administrateur permanent' : 'Mon offre de lancement'}</h2>
      <p>DevOps et DevSecOps accessibles. {launch.unlimited ? 'Aucune date d’expiration.' : 'Durée liée au premier déploiement réussi.'}</p>
      {launch.projects?.filter(project => project.expires_at).map(project =>
        <p key={project.project_id}><strong>{project.repository}</strong> : {project.state === 'active' ? 'active' : 'échéance atteinte'} · échéance {new Date(project.expires_at).toLocaleDateString('fr-FR')}</p>)}
    </section>}
    <p className="ak-pricing-disclaimer">Offre gratuite à durée limitée. Le paiement en ligne n’est pas encore disponible. À l’expiration, l’accès public peut être suspendu ; la conservation et la suppression des ressources suivent les conditions publiées.</p>
    <section className="ak-pricing-assurances" aria-label="Principes AUTOKOM">
      <div><GitBranch size={22}/><strong>GitHub intégré</strong><p>Un seul dépôt publié par compte GitHub pendant l’offre.</p></div>
      <div><ShieldCheck size={22}/><strong>Pipeline sécurisé</strong><p>Utilisez les contrôles DevOps ou DevSecOps.</p></div>
      <div><Workflow size={22}/><strong>Publication suivie</strong><p>Consultez les étapes et journaux de déploiement.</p></div>
    </section>
    <section className="ak-pricing-faq" aria-labelledby="ak-pricing-faq-title">
      <div className="ak-pricing-section-head"><h2 id="ak-pricing-faq-title"><CircleHelp size={24}/> Questions fréquentes</h2></div>
      <div className="ak-pricing-questions">{questions.map(item => <details key={item.question}><summary>{item.question}</summary><p>{item.answer}</p></details>)}</div>
    </section>
  </div>
}
