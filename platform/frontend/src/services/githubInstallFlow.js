/** Les installations GitHub ne sont jamais déduites d'un ID de query string non vérifié. */
export const INSTALL_HANDOFF = 'autokom.github.install-handoff'

export function verifiedInstallationUrl(raw) {
  const url = new URL(raw)
  if (url.protocol !== 'https:' || url.hostname !== 'github.com' ||
      !/^\/apps\/[A-Za-z0-9-]+\/installations\/new\/?$/.test(url.pathname)) {
    throw new Error('URL d’installation GitHub inattendue.')
  }
  return url.href
}

export function selectAuthorizedInstallation(items, previous = [], hintedId = '') {
  const ids = new Set(items.map(v => String(v.id)))
  const hint = String(hintedId || '')
  // A URL parameter is a hint only; it must match an installation returned by Django/GitHub.
  if (hint && ids.has(hint)) return hint
  const fresh = items.filter(v => !previous.map(String).includes(String(v.id)))
  if (fresh.length === 1) return String(fresh[0].id)
  return items.length === 1 ? String(items[0].id) : ''
}
