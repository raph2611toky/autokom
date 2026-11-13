import { useEffect, useState } from 'react'
import { ArrowRight, Check, CheckCircle2, CircleHelp, GitBranch, ShieldCheck, Workflow } from 'lucide-react'
import { formatAr, getBillingPeriod, PRICING_PLANS } from '../config/pricing.js'
import { billingApi } from '../services/billingApi.js'
import { API_AVAILABLE } from '../config/runtime.js'
import { useAuth } from '../context/AuthContext.jsx'
import '../styles/pricing.css'

const questions = [
  { question: 'Dois-je payer pour publier ?', answer: 'Non. L’offre gratuite de lancement est disponible sans paiement. Votre durée commence à la première mise en ligne réussie.' },
  { question: 'Puis-je changer de dépôt GitHub ?', answer: 'Avant le premier déploiement, oui. Après la première publication, le compte GitHub reste lié au dépôt publié, même si le projet est supprimé.' },
  { question: 'Puis-je ajouter mon backend ensuite ?', answer: 'Oui, dans le même dépôt. L’échéance est recalculée à 15 jours depuis la première publication, ou 7 jours avec haute disponibilité ; elle n’est jamais remise à zéro.' },
  { question: 'Que se passe-t-il à expiration ?', answer: 'Les accès publics sont suspendus. Les conteneurs restent conservés pendant 24 heures ; la suppression définitive dépendra de la disponibilité d’un parcours de renouvellement.' },
]
export function activePricingPlan(subscription) {
  // Preserve the public helper's contract for legacy UI/tests while treating
  // the temporary launch entitlement as free, not as a paid subscription.
  if (!subscription || typeof subscription !== 'object') return null
  if (subscription.launch_offer) return 'free'
  const plan = subscription.active ? subscription.plan : 'free'
  return PRICING_PLANS.some(item => item.id === plan) ? plan : null
}
export default function Pricing({ navigate }) {
  const [period, setPeriod] = useState(getBillingPeriod)
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
  const activePlan = activePricingPlan(subscription)
  return <div className="ak-pricing-page">
    <section className="ak-pricing-hero" aria-labelledby="ak-pricing-title">
      <div className="ak-pricing-heading">
        <h1 id="ak-pricing-title">Offre gratuite de lancement</h1>
        <p>Déployez votre application avec DevOps ou DevSecOps, avec ou sans haute disponibilité. Aucun paiement n’est demandé pour cette offre temporaire.</p>
      </div>
      <fieldset className="ak-pricing-switch">
        <legend>Facturation</legend>
        <div className="ak-pricing-switch-inner">
          <button type="button" className={period === 'monthly' ? 'is-current' : ''} aria-pressed={period === 'monthly'} onClick={() => setPeriod('monthly')}>Mensuelle</button>
          <button type="button" className={period === 'annual' ? 'is-current' : ''} aria-pressed={period === 'annual'} onClick={() => setPeriod('annual')}>Annuelle <span>2 mois offerts</span></button>
        </div>
      </fieldset>
    </section>
    <div className="ak-pricing-cards">
      {PRICING_PLANS.map(plan => {
        const isActive = activePlan === plan.id
        return <article key={plan.id} className={`ak-pricing-card ${plan.highlighted && !isActive ? 'is-highlighted' : ''} ${isActive ? 'is-active-subscription' : ''}`} aria-label={plan.name}>
          <div className="ak-pricing-card-top"><div><h2>{plan.name}</h2><p>{plan.tagline}</p></div>
            {isActive ? <span className="ak-pricing-current"><CheckCircle2 size={15}/> Offre actuelle</span> :
              plan.highlighted && <span className="ak-pricing-popular">Recommandée</span>}
          </div>
          <div className="ak-pricing-amount"><strong>{plan.id === 'free' ? 'Gratuit' : formatAr(period === 'annual' ? plan.annual : plan.monthly)}</strong>
            {plan.id !== 'free' && <span>/ {period === 'annual' ? 'an' : 'mois'}</span>}
          </div>
          <span className="ak-pricing-price-caption">{plan.id === 'free' ? 'Accès gratuit, sans paiement' : 'Tarif indicatif · souscription à venir'}</span>
          {plan.id === 'free' ? <button className="ak-pricing-select is-primary" type="button" onClick={() => navigate('/deploy')}>
            Choisir mon dépôt <ArrowRight size={18}/>
          </button> : <button className="ak-pricing-select" type="button" disabled>Souscription bientôt disponible</button>}
          <ul className="ak-pricing-benefits">
            {(plan.id === 'free' ? ['DevOps et DevSecOps', 'Un dépôt GitHub et des domaines AUTOKOM',
              'Journaux et suivi des publications', 'Frontend uniquement · 30 jours sans HA ou 15 jours avec HA',
              'Frontend + backend · 15 jours sans HA ou 7 jours avec HA'] : plan.benefits).map(feature =>
                <li key={feature}><Check size={16} aria-hidden="true"/><span>{feature}</span></li>)}
          </ul>
        </article>
      })}
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
