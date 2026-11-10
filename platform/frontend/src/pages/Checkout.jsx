import { useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, CheckCircle2, ShieldCheck } from 'lucide-react'
import { PRICING_PLANS, findPricingPlan, formatAr, getBillingPeriod } from '../config/pricing.js'
import { billingApi } from '../services/billingApi.js'
import { API_AVAILABLE } from '../config/runtime.js'
import '../styles/subscription.css'

export default function Checkout({ navigate, planId }) {
  const plan = PRICING_PLANS.find(item => item.id === planId && item.id !== 'free')
  const [period, setPeriod] = useState(getBillingPeriod)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [completed, setCompleted] = useState(false)
  const requestKey = useRef(null)
  const [accepted, setAccepted] = useState(false)
  if (!plan) return <section className="ak-subscription-sheet"><h1>Offre inconnue</h1><button onClick={() => navigate('/pricing')}>Voir les tarifs</button></section>
  async function confirm() {
    if (!accepted || busy || completed || !API_AVAILABLE) return
    setBusy(true); setError('')
    try {
      requestKey.current ||= globalThis.crypto?.randomUUID?.()
      if (!requestKey.current) throw new Error('Un navigateur HTTPS récent est requis.')
      const response = await billingApi.accountCheckout(plan.id, period, requestKey.current)
      if (response?.payment_real !== false || response?.amount_charged_ariary !== 0 ||
          response?.subscription?.plan !== plan.id || !response?.subscription?.active) {
        throw new Error('Le backend n’a pas confirmé la souscription de démonstration.')
      }
      setCompleted(true); requestKey.current = null
    } catch (exc) { setError(exc?.message || 'Souscription indisponible.') }
    finally { setBusy(false) }
  }
  return <section className="ak-subscription-sheet">
    <button type="button" className="button-quiet" onClick={() => navigate('/pricing')}><ArrowLeft size={17}/> Tarifs</button>
    <div className="ak-subscription-header"><h1>Confirmer votre abonnement</h1><p>Votre nouvelle offre sera enregistrée sur votre compte AUTOKOM et appliquée aux projets dont vous êtes propriétaire.</p></div>
    <div className="ak-subscription-summary"><h2>{plan.name}</h2><p>{plan.tagline}</p>
      <div className="ak-subscription-amount">{formatAr(period === 'annual' ? plan.annual : plan.monthly)} <span>/ {period === 'annual' ? 'an' : 'mois'}</span></div>
      <label className="ak-subscription-field">Périodicité <select value={period} disabled={busy || completed} onChange={event => setPeriod(event.target.value)}><option value="monthly">Mensuelle</option><option value="annual">Annuelle</option></select></label>
      <p><ShieldCheck size={17}/> {plan.servers} · {plan.id === 'pro' ? 'Haute disponibilité disponible sur demande' : 'Déploiement standard'}</p>
    </div>
    <div className="ak-subscription-disclosure" role="note"><strong>Paiement de démonstration</strong><p>Le prestataire de paiement réel n’est pas connecté. Aucune carte bancaire n’est demandée et aucun montant ne sera débité. La souscription est activée uniquement dans cet environnement de démonstration.</p></div>
    {completed ? <div className="ak-subscription-success" role="status"><CheckCircle2 size={20}/> Abonnement enregistré sur votre compte. <button type="button" className="button-primary" onClick={() => navigate('/account')}>Voir mon abonnement <ArrowRight size={17}/></button></div> : <>
      <label className="ak-subscription-consent"><input type="checkbox" checked={accepted} onChange={event => setAccepted(event.target.checked)}/> J’ai compris qu’il s’agit d’une simulation sans transaction financière.</label>
      {error && <p role="alert" className="feedback error">{error}</p>}
      <button type="button" className="button-primary" disabled={busy || !accepted || !API_AVAILABLE} onClick={confirm}>{busy ? 'Confirmation…' : 'Confirmer la souscription'} <ArrowRight size={17}/></button>
    </>}
  </section>
}
