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
