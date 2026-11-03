import { useState } from 'react'
import { ArrowRight, Check, CheckCircle2, CircleHelp, GitBranch, ShieldCheck, Server, Workflow } from 'lucide-react'
import { formatAr, getBillingPeriod, PRICING_PLANS, rememberPlan } from '../config/pricing.js'
import '../styles/pricing.css'

const comparison = [
  { name: 'Connexion et sélection GitHub', values: ['Inclus', 'Inclus', 'Inclus'] },
  { name: 'Pipeline DevOps / DevSecOps', values: ['Inclus', 'Inclus', 'Inclus'] },
  { name: 'Sous-domaine AUTOKOM', values: ['Inclus', 'Inclus', 'Inclus'] },
  { name: 'Domaine personnalisé', values: ['—', 'Inclus', 'Inclus'] },
  { name: 'Profil de ressources', values: ['Minimal', 'Adapté', 'Adapté'] },
  { name: 'Haute disponibilité', values: ['—', '—', 'Jusqu’à 2 serveurs'] },
  { name: 'Pipelines et journaux par projet', values: ['Essentiels', 'Complets', 'Complets'] },
]
const questions = [
  { question: 'Puis-je commencer sans payer ?', answer: 'Oui. La formule Découverte est prévue pour lancer un premier projet avec des ressources minimales prédéfinies. Le volume des ressources et la durée d’utilisation seront précisés dans les conditions commerciales définitives.' },
  { question: 'Dois-je régler les commandes de construction ?', answer: 'Non. AUTOKOM analyse le dépôt et propose les réglages utiles. Les paramètres avancés ne sont nécessaires que si le projet utilise une structure particulière.' },
  { question: 'Comment fonctionne la haute disponibilité ?', answer: 'La formule Professionnel prévoit une option haute disponibilité sur deux serveurs distincts au maximum. Son activation dépendra de l’infrastructure réellement disponible et des contrôles effectués lors du déploiement.' },
  { question: 'Puis-je utiliser mon domaine ?', answer: 'Oui, dans les formules payantes proposées. La mise en ligne exigera ensuite la vérification du contrôle du domaine et la configuration du certificat HTTPS.' },
]

export default function Pricing({ navigate }) {
  const [period, setPeriod] = useState(getBillingPeriod)
  const choose = id => {
    rememberPlan(id, undefined, period)
    navigate('/deploy')
  }

  return (
    <div className="ak-pricing-page">
      <section className="ak-pricing-hero" aria-labelledby="ak-pricing-title">
        <div className="ak-pricing-heading">
          <h1 id="ak-pricing-title">Des tarifs qui suivent vos projets.</h1>
          <p>Commencez gratuitement. Choisissez ensuite le niveau de ressources et de disponibilité adapté à votre application.</p>
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
        {PRICING_PLANS.map(plan => (
          <article className={`ak-pricing-card ${plan.highlighted ? 'is-highlighted' : ''}`} key={plan.id}>
            <div className="ak-pricing-card-top">
              <div>
                <h2>{plan.name}</h2>
                <p>{plan.tagline}</p>
              </div>
              {plan.highlighted && <span className="ak-pricing-popular">Recommandée</span>}
            </div>
            <div className="ak-pricing-amount">
              <strong>{formatAr(period === 'annual' ? plan.annual : plan.monthly)}</strong>
              <span>/ {period === 'annual' ? 'an' : 'mois'}</span>
            </div>
            {period === 'annual' && plan.monthly > 0 ? <span className="ak-pricing-price-caption">Soit {formatAr(Math.round(plan.annual / 12))} par mois, facturé à l’année</span> : <span className="ak-pricing-price-caption">{plan.monthly ? 'Sans paiement immédiat' : 'Accès gratuit, sans paiement'}</span>}
            <button className={`ak-pricing-select ${plan.highlighted ? 'is-primary' : ''}`} type="button" onClick={() => choose(plan.id)}>{plan.cta}<ArrowRight size={18}/></button>
            <div className="ak-pricing-specs">
              <div><Server size={17}/><span>{plan.servers}</span></div>
              <div><Workflow size={17}/><span>{plan.capacity}</span></div>
            </div>
            <ul className="ak-pricing-benefits">
              {plan.benefits.map(benefit => <li key={benefit}><Check size={16} aria-hidden="true"/><span>{benefit}</span></li>)}
            </ul>
          </article>
        ))}
      </div>
      <p className="ak-pricing-disclaimer">Grille commerciale proposée pour AUTOKOM par IKOM, en cours de validation. Montants et quotas indicatifs, hors frais éventuels de domaine et taxes à définir. Aucune commande ni facturation n'est effectuée depuis cette interface.</p>

      <section className="ak-pricing-compare" aria-labelledby="ak-pricing-compare-title">
        <div className="ak-pricing-section-head"><h2 id="ak-pricing-compare-title">Comparer les offres</h2><p>Les capacités essentielles sont incluses dès la première formule.</p></div>
        <div className="ak-pricing-table-scroll" role="region" aria-label="Comparatif des tarifs" tabIndex={0}>
          <table className="ak-pricing-table">
            <thead><tr><th scope="col">Fonctionnalités</th>{PRICING_PLANS.map(plan=><th scope="col" key={plan.id}>{plan.name}</th>)}</tr></thead>
            <tbody>{comparison.map(row=><tr key={row.name}><th scope="row">{row.name}</th>{row.values.map((value,i)=><td key={PRICING_PLANS[i].id}>{value === 'Inclus' ? <span className="ak-pricing-included"><CheckCircle2 size={16}/> Inclus</span> : value}</td>)}</tr>)}</tbody>
          </table>
        </div>
      </section>

      <section className="ak-pricing-assurances" aria-label="Principes AUTOKOM">
        <div><GitBranch size={22}/><strong>GitHub intégré</strong><p>Choisissez votre dépôt, votre branche et votre dossier source.</p></div>
        <div><ShieldCheck size={22}/><strong>Infrastructure protégée</strong><p>La sécurité de l’infrastructure AUTOKOM reste toujours active.</p></div>
        <div><Workflow size={22}/><strong>Déploiement suivi</strong><p>Retrouvez le pipeline et les journaux dans chaque fiche de projet.</p></div>
      </section>

      <section className="ak-pricing-faq" aria-labelledby="ak-pricing-faq-title">
        <div className="ak-pricing-section-head"><h2 id="ak-pricing-faq-title"><CircleHelp size={24}/> Questions fréquentes</h2></div>
        <div className="ak-pricing-questions">{questions.map(item=><details key={item.question}><summary>{item.question}</summary><p>{item.answer}</p></details>)}</div>
      </section>
    </div>
  )
}
