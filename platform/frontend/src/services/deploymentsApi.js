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
  list: (p, e) => api.get(`${prefix(p, e)}deployments/`),
  detail: (p, e, runId) => api.get(`${prefix(p, e)}deployments/${validId(runId)}/`),
  logs: (p, e, runId) => api.get(`${prefix(p, e)}deployments/${validId(runId)}/logs/`),
  cancel: (p, e, runId) => api.post(`${prefix(p, e)}deployments/${validId(runId)}/cancel/`, {}),
})
