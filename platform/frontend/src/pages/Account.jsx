import { useEffect, useState } from 'react'
import { ArrowRight, UserCircle2, ShieldCheck } from 'lucide-react'
import { API_AVAILABLE } from '../config/runtime.js'
import { billingApi } from '../services/billingApi.js'
import { useAuth } from '../context/AuthContext.jsx'
import '../styles/subscription.css'

export default function Account({ navigate }) {
  const { user } = useAuth()
  const [subscription, setSubscription] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!API_AVAILABLE) return
    let live = true
    billingApi.account().then(result => { if (live) setSubscription(result?.subscription || null) })
      .catch(exc => { if (live) setError(exc?.message || "Impossible de charger l'offre du compte.") })
    return () => { live = false }
  }, [])
  const launch = subscription?.launch_offer
  return <section className="ak-subscription-sheet">
    <div className="ak-subscription-header"><h1>Mon compte</h1><p>Profil et droits AUTOKOM.</p></div>
    <div className="ak-subscription-summary"><h2><UserCircle2 size={21}/> Profil</h2>
      <p>{user?.full_name || user?.name || 'Compte AUTOKOM'}</p><p>{user?.email || ''}</p></div>
    <div className="ak-subscription-summary"><h2><ShieldCheck size={21}/> Mon offre</h2>
      {error ? <p role="alert" className="feedback error">{error}</p> : !subscription ?
        <p role="status">Chargement des droits du compte…</p> : launch ? <>
        <strong className="ak-subscription-current">{launch.offer}</strong>
        <p>DevOps et DevSecOps · option haute disponibilité de deux instances.</p>
        {launch.unlimited ? <p>Autorisation administrateur sans expiration.</p> :
          <p>Durée déclenchée à la première publication réussie du dépôt.</p>}
        {launch.projects?.map(project => <p key={project.project_id}>
          {project.repository} — {project.expires_at ?
            `Échéance : ${new Date(project.expires_at).toLocaleDateString('fr-FR')} (${project.state})` :
            project.state === 'unlimited' ? 'Accès permanent' : 'En attente de première publication'}
        </p>)}
      </> : <>
        <strong className="ak-subscription-current">{subscription.plan_name}</strong>
        <p>{subscription.max_instances} instance(s) autorisée(s) · HA {subscription.ha_available ? 'disponible' : 'indisponible'}</p>
      </>}
      <button type="button" className="button-outline" onClick={() => navigate('/pricing')}>
        Voir les offres <ArrowRight size={16}/>
      </button>
    </div>
  </section>
}
