/** Liaison strictement conforme aux endpoints Django AUTOKOM.
 * En mode API, aucune liste et aucun pipeline simulé ne remplace une erreur backend.
 */
import { projectsApi } from './projectsApi.js'
import { deploymentsApi } from './deploymentsApi.js'

export function errorText(error) {
  const errors = error?.errors
  if (errors && typeof errors === 'object') {
    const details = Object.entries(errors).flatMap(([field, value]) =>
      (Array.isArray(value) ? value : [value]).filter(Boolean).map(v => `${field} : ${String(v)}`))
    if (details.length) return details.slice(0, 4).join(' · ')
  }
  return error?.message || 'Opération impossible.'
}

export function toBackendConfiguration(form) {
  const advanced = form.advancedBuild || {}
  const selectedDirectory = (form.workdir || './').trim()
  const path = selectedDirectory === './' ? '' : selectedDirectory.replace(/^\.\//, '').replace(/\/$/, '')
  if (path.startsWith('/') || path.split('/').includes('..') || path.includes('\\')) throw new Error('Dossier source Git invalide.')
  // AUTO demeure disponible en brouillon ; le runner actuel exige DOCKERFILE pour finaliser.
  const dockerfile = String(advanced.dockerfile || 'Dockerfile').trim()
  const portValue = Number(advanced.appPort || 8000)
  if (!Number.isSafeInteger(portValue) || portValue < 1 || portValue > 65535) throw new Error('Port interne invalide.')
  const mode = (form.pipelineMode || 'devops').toUpperCase()
  if (!['DEVOPS', 'DEVSECOPS'].includes(mode)) throw new Error('Mode de pipeline invalide.')
  return {
    mode, branch: form.branch, root_directory: path,
    build_method: 'DOCKERFILE', dockerfile_path: dockerfile, runtime_type: 'OTHER',
    build_command: String(advanced.buildCommand || ''),
    start_command: String(advanced.startCommand || ''),
    output_directory: String(advanced.outputDir || ''),
    internal_port: portValue,
    auto_deploy: Boolean(form.autoDeploy), auto_deploy_branch: form.autoDeploy ? form.branch : '',
  }
}

export function backendProjectRecord(p) {
  return {
    id: String(p.id), name: p.name, repository: p.repository_name || '', branch: p.default_branch || '',
    workdir: './', environment: 'production', mode: 'devops', offer: 'free', pricingPlan: 'free',
    domainMode: 'autokom', domain: '', updatedAt: p.updated_at || p.created_at || '',
    status: p.status, live: true, preview: null, draft: null,
  }
}

export async function listRemoteProjectRecords() {
  const all = []
  for (let page = 1; page <= 10; page++) {
    const result = await projectsApi.list(page)
    const items = Array.isArray(result?.projects) ? result.projects : []
    all.push(...items.map(backendProjectRecord))
    if (items.length < 50 || all.length >= (result?.total ?? Infinity)) break
  }
  return all
}

export async function getRemoteProjectDetail(id) {
  const detail = await projectsApi.detail(id)
  const project = detail?.project
  if (!project) throw new Error('Projet absent de la réponse Django.')
  const environments = detail.environments || []
  const env = environments.find(item => item.slug === 'production') || environments[0]
  if (!env) throw new Error('Environnement du projet introuvable.')
  const [configsResponse, runsResponse] = await Promise.all([
    projectsApi.configurations(project.id, env.id),
    deploymentsApi.list(project.id, env.id),
  ])
  return { project, environment: env, configurations: configsResponse?.configurations || [], runs: runsResponse?.runs || [] }
}

/** Création d'un projet puis mise à jour de son unique configuration initiale.
 * `onCreated` est appelé immédiatement après le POST : un échec ultérieur peut être repris
 * sans créer un second projet GitHub avec le même nom.
 */
export async function saveRemoteProject(form, { onCreated = () => {} } = {}) {
  const configurationValues = toBackendConfiguration(form)
  let projectId = form.backendProjectId || ''
  let envId = form.backendEnvironmentId || ''
  if (!projectId && form.pricingPlan && form.pricingPlan !== 'free') {
    throw new Error('La souscription aux offres payantes n’est pas encore raccordée au backend. Sélectionnez l’offre gratuite avant de créer ce projet.')
  }
  if (!projectId) {
    if (!form.organizationId || !form.githubInstallationId || !form.githubRepositoryId) {
      throw new Error('Sélectionnez votre organisation et un dépôt GitHub autorisé avant d’enregistrer.')
    }
    const created = await projectsApi.create({
      organization_id: form.organizationId,
      installation_id: Number(form.githubInstallationId),
      repository_id: Number(form.githubRepositoryId),
      name: form.projectName.trim(),
      ...(form.domainMode === 'custom' && form.domain.trim() ? { domain: form.domain.trim() } : {}),
    })
    projectId = created?.project?.id
    envId = created?.environment_id
    if (!projectId || !envId) throw new Error('Réponse de création de projet incomplète.')
    onCreated({ backendProjectId: projectId, backendEnvironmentId: envId })
  }
  const requestedKind = String(form.environment || 'production').toUpperCase()
  if (!['PRODUCTION', 'STAGING', 'DEVELOPMENT'].includes(requestedKind)) throw new Error('Environnement invalide.')
  const environments = (await projectsApi.environments(projectId))?.environments || []
  let selected = environments.find(env => env.kind === requestedKind)
  if (!selected) {
    if (requestedKind === 'PRODUCTION') throw new Error('Environnement de production introuvable.')
    const name = requestedKind === 'STAGING' ? 'Préproduction' : 'Développement'
    const response = await projectsApi.createEnvironment(projectId, { name, kind: requestedKind })
    selected = response?.environment
  }
  if (!selected?.id) throw new Error('Environnement demandé introuvable.')
  envId = selected.id
  onCreated({ backendProjectId: projectId, backendEnvironmentId: envId })
  // L’interface ne doit jamais présenter une offre non accordée comme activée.
  const offers = await projectsApi.offers(projectId, envId)
  if (form.pricingPlan && form.pricingPlan !== 'free') {
    throw new Error('La souscription et l’attribution du plan payant ne sont pas encore raccordées à Django. Le projet est conservé ; choisissez l’offre gratuite autorisée pour continuer.')
  }
  if (offers?.entitlement?.resource_plan) configurationValues.resource_plan = offers.entitlement.resource_plan
  let configs = (await projectsApi.configurations(projectId, envId))?.configurations || []
  let draft = configs.find(item => item.is_draft)
  if (!draft) {
    const result = await projectsApi.createConfiguration(projectId, envId, configurationValues)
    draft = result?.configuration
  } else {
    const result = await projectsApi.updateConfiguration(projectId, envId, draft.id, configurationValues)
    draft = result?.configuration
  }
  if (!draft?.id) throw new Error('Le backend n’a pas confirmé la configuration.')
  return { projectId, environmentId: envId, configurationId: draft.id, configuration: draft }
}

export async function prepareRemoteDeployment(ids) {
  if (!ids?.projectId || !ids?.environmentId || !ids?.configurationId) throw new Error('Enregistrez d’abord le projet.')
  await projectsApi.finalizeConfiguration(ids.projectId, ids.environmentId, ids.configurationId)
  return deploymentsApi.preview(ids.projectId, ids.environmentId)
}

export function remoteProjectToForm(detail) {
  const project = detail.project
  const env = detail.environment
  const cfg = detail.configurations[0] || {}
  return {
    backendProjectId: project.id, backendEnvironmentId: env.id,
    projectName: project.name, repositorySlug: project.repository_name,
    repository: project.repository_url, githubRepositoryId: project.repository_external_id,
    branch: cfg.branch || project.default_branch || 'main', workdir: cfg.root_directory || './',
    pipelineMode: (cfg.mode || 'DEVOPS').toLowerCase(),
    environment: env.slug,
    autoDeploy: Boolean(cfg.auto_deploy),
    advancedBuild: { dockerfile: cfg.dockerfile_path || 'Dockerfile', buildCommand: cfg.build_command || '',
      startCommand: cfg.start_command || '', outputDir: cfg.output_directory || '', appPort: cfg.internal_port || 8000 },
  }
}
