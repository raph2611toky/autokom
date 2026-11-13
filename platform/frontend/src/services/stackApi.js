import { api } from './api.js'
const uuid = value => {
  if (typeof value !== 'string' || !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(value)) throw new TypeError('Identifiant de projet invalide.')
  return value
}
export const stackApi = Object.freeze({
  detail: projectId => api.get(`/projects/${uuid(projectId)}/stack/`),
  configure: (projectId, payload) => api.put(`/projects/${uuid(projectId)}/stack/`, payload),
  domainSettings: projectId => api.get(`/projects/${uuid(projectId)}/stack/domains/preflight/`),
  preflightDomains: projectId => api.post(`/projects/${uuid(projectId)}/stack/domains/preflight/`, {}),
  launch: (projectId, idempotencyKey) => api.post(`/projects/${uuid(projectId)}/stack/launch/`, { idempotency_key:idempotencyKey }),
  restore: (p, runId, key) => api.post(`/projects/${uuid(p)}/stack/deployments/${uuid(runId)}/restore/`, {}, {headers:{'Idempotency-Key':key}}),
  publication: (projectId, runId) => api.get(`/projects/${uuid(projectId)}/stack/deployments/${uuid(runId)}/`),
  history: (projectId, page = 1, pageSize = 20) => {
    if (!Number.isSafeInteger(page) || page < 1 || page > 100000) throw new TypeError('Page invalide.')
    if (!Number.isSafeInteger(pageSize) || pageSize < 1 || pageSize > 50) throw new TypeError('Taille de page invalide.')
    return api.get(`/projects/${uuid(projectId)}/stack/history/?page=${page}&page_size=${pageSize}`)
  },
})
