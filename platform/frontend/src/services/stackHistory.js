import { publicationLabel } from './deploymentPresentation.js'
/** Link a group to its own detail view; keep legacy single-service routes unchanged. */
export function publicationPath(projectId, item) {
  if (!item?.id) throw new TypeError('Publication sans identifiant.')
  return item.kind === 'stack'
    ? `/projects/${projectId}/stack/deployments/${item.id}`
    : `/projects/${projectId}/deployments/${item.id}`
}
export function publicationKind(item) {
  return item?.kind === 'stack' ? 'Frontend + Backend' : 'Application'
}

/** Affiche les états réellement persistés des deux runs, sans présumer que
 * la réussite Jenkins signifie que les deux hôtes HTTPS sont en ligne. */
export function publicationServices(item) {
  if (item?.kind !== 'stack') return ''
  const backend = publicationLabel(item.backend_status)[0]
  const frontend = publicationLabel(item.frontend_status)[0]
  return `Backend : ${backend} · Frontend : ${frontend}`
}
