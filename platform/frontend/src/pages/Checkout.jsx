import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, ShieldCheck } from 'lucide-react'
import { findPricingPlan, formatAr, getBillingPeriod } from '../config/pricing.js'
import { billingApi } from '../services/billingApi.js'
import '../styles/subscription.css'

export default function Checkout({ navigate, planId }) {
  const plan = findPricingPlan(planId)
  const [period] = useState(getBillingPeriod)
  const [allowed, setAllowed] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const attempt = useRef({ key: null, pending: false })
  useEffect(() => {
    let live = true
    setAllowed(null)
    billingApi.account().then(result => {
      if (live) setAllowed(result?.subscription?.can_subscribe === true)
    }).catch(() => {
      if (live) {
        setAllowed(false)
        setError('Impossible de vérifier la disponibilité de la souscription.')
      }
    })
    return () => { live = false }
  }, [planId])

  async function activate() {
    if (allowed !== true || plan.id === 'free' || attempt.current.pending) return
    attempt.current.pending = true
    setBusy(true)
    setError('')
    try {
      const key = attempt.current.key || globalThis.crypto.randomUUID()
      attempt.current.key = key
      const result = await billingApi.accountCheckout(plan.id, period, key)
      if (result?.subscription?.plan !== plan.id || result?.subscription?.active !== true ||
          result.payment_real !== false || result.amount_charged_ariary !== 0) {
        throw new Error('La souscription n’a pas été confirmée.')
      }
      navigate('/account')
    } catch (exc) {
      setError(exc?.message || 'La souscription n’a pas pu être activée.')
    } finally {
      attempt.current.pending = false
      setBusy(false)
    }
  }
  return <section className="ak-subscription-sheet">
    <button type="button" className="button-quiet" onClick={() => navigate('/pricing')}>
      <ArrowLeft size={17}/> Les offres
    </button>
    <div className="ak-subscription-header"><h1>Souscrire à {plan.name}</h1></div>
    {allowed === null ? <p role="status">Vérification de ton compte…</p> : allowed && plan.id !== 'free' ? <>
      <div className="ak-subscription-summary">
        <h2><ShieldCheck size={21}/> Récapitulatif de l’abonnement</h2>
        <p>{formatAr(period === 'annual' ? plan.annual : plan.monthly)} {period === 'annual' ? 'par an' : 'par mois'} · tarif de l’offre</p>
        <p><strong>Montant débité maintenant {formatAr(0)}</strong></p>
        <p>Aucun moyen de paiement n’est demandé et aucun débit n’est effectué.</p>
      </div>
      <button className="button-primary" type="button" disabled={busy} onClick={activate}>
        {busy ? 'Activation en cours…' : 'Activer mon abonnement'} <ArrowRight size={17}/>
      </button>
    </> : <p>La souscription n’est pas disponible pour ce compte.</p>}
    {error && <p className="feedback error" role="alert">{error}</p>}
  </section>
}
