import { useEffect, useState } from 'react'
import { Cookie, ShieldCheck, SlidersHorizontal, X } from 'lucide-react'
import { readCookieConsent, saveCookieConsent } from '../services/cookieConsent.js'
import '../styles/cookie-consent.css'

/** Les cookies HttpOnly de connexion/CSRF restent indispensables, indépendants du choix. */
export default function CookieConsent({ visible = true }) {
  const [choice, setChoice] = useState(readCookieConsent)
  const [reopened, setReopened] = useState(false)
  const [details, setDetails] = useState(false)
  useEffect(() => {
    const open = () => setReopened(true)
    window.addEventListener('autokom:cookie-open', open)
    return () => window.removeEventListener('autokom:cookie-open', open)
  }, [])
  if (!visible || (choice && !reopened)) return null

  function decide(optional) {
    setChoice(saveCookieConsent(optional))
    setReopened(false)
    setDetails(false)
  }

  return (
    <aside className="lp-cookie-banner" role="region" aria-label="Préférences de confidentialité">
      <div className="lp-cookie-head"><span className="lp-cookie-icon"><Cookie size={22} aria-hidden="true" /></span><div><strong>Votre confidentialité, vos choix.</strong><p>Cookies et préférences AUTOKOM</p></div>{reopened && <button className="lp-cookie-close" type="button" aria-label="Fermer" onClick={() => setReopened(false)}><X size={18} /></button>}</div>
      <p className="lp-cookie-summary">Nous utilisons les cookies indispensables à la connexion et à la sécurité. Aucun cookie de publicité ou de mesure d’audience facultatif n’est activé actuellement.</p>
      {details && <div className="lp-cookie-details"><p><ShieldCheck size={15} aria-hidden="true" /> <strong>Indispensables</strong> — session, protection CSRF et sécurité. Toujours actifs.</p><p><SlidersHorizontal size={15} aria-hidden="true" /> <strong>Facultatifs</strong> — aucun traceur facultatif n’est déployé pour le moment. Votre choix est mémorisé et reste modifiable.</p><p>Le thème choisi est mémorisé localement pour faciliter la navigation.</p></div>}
      <div className="lp-cookie-actions">
        <button className="lp-cookie-secondary" type="button" onClick={() => decide(false)}>Refuser les facultatifs</button>
        <button className="lp-cookie-primary" type="button" onClick={() => decide(true)}>Accepter</button>
        <button className="lp-cookie-more" type="button" aria-expanded={details} onClick={() => setDetails(current => !current)}>{details ? 'Masquer les détails' : 'En savoir plus'}</button>
      </div>
    </aside>
  )
}
