/** An URL is exposed only when the control plane confirmed DNS + HTTPS.
 * Never synthesize a clickable link from a PENDING hostname.
 */
export function publicSiteUrl(site) {
  if (!site || site.status !== 'ACTIVE' || site.https !== true || typeof site.url !== 'string') return null
  try {
    const url = new URL(site.url)
    if (url.protocol !== 'https:' || url.username || url.password || url.port ||
        url.pathname !== '/' || url.search || url.hash ||
        !/^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/.test(url.hostname)) return null
    return url.origin + '/'
  } catch { return null }
}
export function siteStateLabel(site) {
  if (publicSiteUrl(site)) return 'HTTPS actif'
  if (site?.activation_error_code && /^AUTOKOM_[A-Z0-9_]{3,80}$/.test(site.activation_error_code))
    return `Activation HTTPS en attente · ${site.activation_error_code}`
  if (site?.hostname) return 'Adresse réservée · DNS/HTTPS en attente'
  return 'Aucun domaine configuré'
}
