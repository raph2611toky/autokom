import { api } from './api.js'
const uuid = value => {
  if (typeof value !== 'string' || !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(value)) throw new TypeError('Identifiant de projet invalide.')
  return value
}
export const stackApi = Object.freeze({
  detail: projectId => api.get(`/projects/${uuid(projectId)}/stack/`),
  configure: (projectId, payload) => api.put(`/projects/${uuid(projectId)}/stack/`, payload),
  launch: (projectId, idempotencyKey) => api.post(`/projects/${uuid(projectId)}/stack/launch/`, { idempotency_key:idempotencyKey }),
  publication: (projectId, runId) => api.get(`/projects/${uuid(projectId)}/stack/deployments/${uuid(runId)}/`),
})
