/** Contrats existants de Django `apps/deployments/runs/urls.py`.
 * Le déploiement via ces routes n'est pas encore branché aux maquettes React.
 */
import { api } from './api.js'
const validId = id => {
  if (typeof id !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) throw new TypeError('Identifiant invalide.')
  return id
}
const prefix = (projectId, environmentId) => `/projects/${validId(projectId)}/environments/${validId(environmentId)}/`
export const deploymentsApi = Object.freeze({
  preview: (p, e) => api.get(`${prefix(p, e)}deployments/preview/`),
  launch: (p, e, idempotencyKey) => {
    const key = idempotencyKey || globalThis.crypto?.randomUUID?.()
    if (!key || !/^[a-zA-Z0-9-]{8,128}$/.test(key)) throw new TypeError('Clé d’idempotence invalide.')
    return api.post(`${prefix(p, e)}deployments/launch/`, {}, { headers: { 'Idempotency-Key': key } })
  },
  list: (p, e, page = 1, pageSize = 10) => {
    if (!Number.isSafeInteger(page) || page < 1 || page > 100000) throw new TypeError('Page invalide.')
    if (!Number.isSafeInteger(pageSize) || pageSize < 1 || pageSize > 50) throw new TypeError('Taille de page invalide.')
    return api.get(`${prefix(p, e)}deployments/?page=${page}&page_size=${pageSize}`)
  },
  detail: (p, e, runId) => api.get(`${prefix(p, e)}deployments/${validId(runId)}/`),
  logs: (p, e, runId, after = 0, mode = 'technical') => {
    if (!Number.isSafeInteger(after) || after < 0) throw new TypeError('Curseur de journal invalide.')
    if (!['technical','simple'].includes(mode)) throw new TypeError('Mode de journal invalide.')
    return api.get(`${prefix(p, e)}deployments/${validId(runId)}/logs/?after=${after}&mode=${mode}`)
  },
  cancel: (p, e, runId) => api.post(`${prefix(p, e)}deployments/${validId(runId)}/cancel/`, {}),
})
