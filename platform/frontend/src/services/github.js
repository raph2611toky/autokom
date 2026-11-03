/** Validation locale des chemins GitHub, sans appel réseau en mode simulation. */
export function repoSlug(repo) {
  const slug = typeof repo === 'string' ? repo : repo?.full_name
  if (!/^[\w.-]+\/[\w.-]+$/.test(slug || '')) throw new Error('Nom de dépôt invalide.')
  return slug
}
export function normalizeDirectoryPath(value) {
  const path = String(value || '').trim().replace(/^\.\//, '').replace(/^\/+|\/+$/g, '')
  if (!path) return './'
  if (path.length > 350 || path.split('/').some(part => !part || part === '..' || part === '.') || /[\\\x00-\x1f]/.test(path)) throw new Error('Chemin source invalide.')
  return path
}
