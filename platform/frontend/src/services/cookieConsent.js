/** Consentement versionné. Aucune mesure d'audience ni publicité facultative n'est chargée. */
export const COOKIE_CONSENT_KEY = 'autokom-cookie-consent-v1'
export const COOKIE_CONSENT_VERSION = 1
export function readCookieConsent(storage = typeof localStorage === 'undefined' ? null : localStorage) {
  try {
    const record = JSON.parse(storage?.getItem(COOKIE_CONSENT_KEY) || 'null')
    if (record?.version === COOKIE_CONSENT_VERSION && typeof record.optional === 'boolean' && Number.isFinite(record.savedAt) && record.savedAt > Date.now() - 180 * 86400000) return record
  } catch { /* Un navigateur sans stockage affiche de nouveau le panneau. */ }
  return null
}
export function saveCookieConsent(optional, storage = typeof localStorage === 'undefined' ? null : localStorage) {
  const record = { version: COOKIE_CONSENT_VERSION, essential: true, optional: Boolean(optional), savedAt: Date.now() }
  try { storage?.setItem(COOKIE_CONSENT_KEY, JSON.stringify(record)) } catch { /* Choix non durable si stockage désactivé. */ }
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent('autokom:cookie-consent', { detail: { optional: record.optional } }))
  return record
}
