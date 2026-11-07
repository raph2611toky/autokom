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
  list: (pageNumber = 1) => api.get(`/projects/?page=${page(pageNumber)}&page_size=50`),
  organizations: () => api.get('/projects/organizations/'),
  create: values => api.post('/projects/create/', values),
  detail: projectId => api.get(`/projects/${uuid(projectId)}/`),
  update: (projectId, values) => api.patch(`/projects/${uuid(projectId)}/update/`, values),
  archive: projectId => api.post(`/projects/${uuid(projectId)}/archive/`, {}),
  restore: projectId => api.post(`/projects/${uuid(projectId)}/restore/`, {}),
  deleteProject: (projectId, confirmName) => api.post(`/projects/${uuid(projectId)}/delete/`, {confirm_name:confirmName}),
  environments: projectId => api.get(`/projects/${uuid(projectId)}/environments/`),
  createEnvironment: (projectId, values) => api.post(`/projects/${uuid(projectId)}/environments/create/`, values),
  configurations: (projectId, envId) => api.get(`/projects/${uuid(projectId)}/environments/${uuid(envId)}/configurations/`),
  createConfiguration: (projectId, envId, values) => api.post(`/projects/${uuid(projectId)}/environments/${uuid(envId)}/configurations/create/`, values),
  updateConfiguration: (projectId, envId, configId, values) => api.patch(`/projects/${uuid(projectId)}/environments/${uuid(envId)}/configurations/${uuid(configId)}/update/`, values),
  finalizeConfiguration: (projectId, envId, configId) => api.post(`/projects/${uuid(projectId)}/environments/${uuid(envId)}/configurations/${uuid(configId)}/finalize/`, {}),
  offers: (projectId, envId) => api.get(`/projects/${uuid(projectId)}/environments/${uuid(envId)}/configurations/offers/`),
  domains: (projectId, envId) => api.get(`/projects/${uuid(projectId)}/environments/${uuid(envId)}/domains/`),
  createDomain: (projectId, envId, hostname, domainType) => {
    if (!['SUBDOMAIN', 'CUSTOM'].includes(domainType)) throw new TypeError('Type de domaine invalide.')
    return api.post(`/projects/${uuid(projectId)}/environments/${uuid(envId)}/domains/create/`,
      {hostname, domain_type:domainType})
  },
  verifyDomain: (projectId, envId, domainId) => api.post(`/projects/${uuid(projectId)}/environments/${uuid(envId)}/domains/${uuid(domainId)}/verify/`, {}),
  primaryDomain: (projectId, envId, domainId) => api.post(`/projects/${uuid(projectId)}/environments/${uuid(envId)}/domains/${uuid(domainId)}/primary/`, {}),
  deactivateDomain: (projectId, envId, domainId) => api.post(`/projects/${uuid(projectId)}/environments/${uuid(envId)}/domains/${uuid(domainId)}/deactivate/`, {}),
})
export const gitApi = Object.freeze({
  start: () => api.post('/projects/git/github/start/', {}),
  status: () => api.get('/projects/git/github/status/'),
  installations: () => api.get('/projects/git/github/installations/'),
  repositories: (installationId, pageNumber = 1) => api.get(`/projects/git/github/repositories/?installation_id=${positive(installationId)}&page=${page(pageNumber)}`),
  branches: (installationId, repositoryId, pageNumber = 1) => api.get(`/projects/git/github/branches/?installation_id=${positive(installationId)}&repository_id=${positive(repositoryId)}&page=${page(pageNumber)}`),
  directories: (installationId, repositoryId, branch, directory = '') => {
    if (typeof branch !== 'string' || !branch || branch.length > 255 || branch.includes('..')) throw new TypeError('Branche invalide.')
    if (typeof directory !== 'string' || directory.startsWith('/') || directory.includes('..') || directory.includes('\\')) throw new TypeError('Dossier invalide.')
    const params = new URLSearchParams({ installation_id: String(positive(installationId)), repository_id: String(positive(repositoryId)), branch, path: directory })
    return api.get(`/projects/git/github/directories/?${params.toString()}`)
  },
  disconnect: () => api.post('/projects/git/github/disconnect/', {}),
})
