/** Uniquement les statuts renvoyés par Django. Aucun faux état de publication. */
export const isPublishing = status => ['QUEUED','RUNNING'].includes(String(status || '').toUpperCase())
export function sortRecentRuns(runs) {
  return [...(Array.isArray(runs) ? runs : [])].sort((a,b) => {
    const x = Date.parse(a?.created_at || '') || 0
    const y = Date.parse(b?.created_at || '') || 0
    return y-x || String(b?.id || '').localeCompare(String(a?.id || ''))
  })
}
export function canPublish(detail) {
  const project = detail?.project
  const cfg = detail?.configurations?.find(item => !item.is_draft)
  if (!project || !detail?.environment?.id || project.status !== 'ACTIVE' || detail.environment.is_active === false) return false
  if (!cfg || cfg.is_draft) return false
  return !(detail.runs || []).some(run => isPublishing(run.status))
}
