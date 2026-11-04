/** Routes existantes dans Django (apps/projects/project/urls.py).
 * Adaptateurs préparés sans toucher aux pages actuellement simulées.
 */
import { api } from './api.js'
const positive = value => {
  const number = Number(value)
  if (!Number.isSafeInteger(number) || number < 1 || number > 999999999999) throw new TypeError('Identifiant numérique invalide.')
  return number
}
const page = value => {
  const n = Number(value)
  if (!Number.isSafeInteger(n) || n < 1 || n > 10000) throw new TypeError('Page invalide.')
  return n
}
const uuid = value => {
  if (typeof value !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)) throw new TypeError('UUID invalide.')
  return value
}
export const projectsApi = Object.freeze({
  list: () => api.get('/projects/'),
  organizations: () => api.get('/projects/organizations/'),
  create: values => api.post('/projects/create/', values),
  detail: projectId => api.get(`/projects/${uuid(projectId)}/`),
  update: (projectId, values) => api.patch(`/projects/${uuid(projectId)}/update/`, values),
  archive: projectId => api.post(`/projects/${uuid(projectId)}/archive/`, {}),
  environments: projectId => api.get(`/projects/${uuid(projectId)}/environments/`),
  createEnvironment: (projectId, values) => api.post(`/projects/${uuid(projectId)}/environments/create/`, values),
})
export const gitApi = Object.freeze({
  start: () => api.post('/projects/git/github/start/', {}),
  status: () => api.get('/projects/git/github/status/'),
  installations: () => api.get('/projects/git/github/installations/'),
  repositories: (installationId, pageNumber = 1) => api.get(`/projects/git/github/repositories/?installation_id=${positive(installationId)}&page=${page(pageNumber)}`),
  branches: (installationId, repositoryId, pageNumber = 1) => api.get(`/projects/git/github/branches/?installation_id=${positive(installationId)}&repository_id=${positive(repositoryId)}&page=${page(pageNumber)}`),
  disconnect: () => api.post('/projects/git/github/disconnect/', {}),
})
