/** Liaison strictement conforme aux endpoints Django AUTOKOM.
 * En mode API, aucune liste et aucun pipeline simulé ne remplace une erreur backend.
 */
import { projectsApi } from './projectsApi.js'
import { deploymentsApi } from './deploymentsApi.js'
import { normalizeFrontendEnv, publicEnvForFramework } from './frontendEnv.js'
import { availableFrontendProfiles, frontendProfile, supportedFrontend, BACKEND_PROFILES } from './sourceAnalysis.js'

export function errorText(error) {
  const raw = String(error?.message || '')
  if (/Service de contrôle AUTOKOM indisponible|Jenkins ou AUTOKOM|Compte de service Jenkins/.test(raw)) {
    const code = raw.match(/AUTOKOM_[A-Z0-9_]{3,80}/)?.[0]
    return 'La publication est temporairement indisponible. Votre projet est conservé.' + (code ? ` Diagnostic : ${code}.` : ' Réessayez plus tard.')
  }

  const errors = error?.errors
  if (errors && typeof errors === 'object') {
    const details = Object.entries(errors).flatMap(([field, value]) =>
      (Array.isArray(value) ? value : [value]).filter(Boolean).map(v => `${field} : ${String(v)}`))
    if (details.length) return details.slice(0, 4).join(' · ')
  }
  return error?.message || 'Opération impossible.'
}

/** Les diagnostics GitHub de la sélection courante ont priorité sur les brouillons UI.
 * Ne jamais envoyer une configuration DOCKERFILE gratuite par accident.
 */
export function verifiedDeploymentForm(form, analysis) {
  if (!analysis || !analysis.available || analysis.confidence === 'low') {
    throw new Error('Analyse du dossier GitHub incomplète : sélectionnez le dossier du projet puis patientez.');
  }
  const candidate = form.advancedBuild?.profile
  const profile = availableFrontendProfiles(analysis).includes(candidate) ? candidate : frontendProfile(analysis)
  if (['vite', 'npm_static', 'next_server', 'next_export', 'node_express', 'node_nest'].includes(profile) && !analysis.hasNpmLockfile) throw new Error('package-lock.json absent du dossier source.')
  if (!supportedFrontend(analysis, profile)) {
    throw new Error('Source non prise en charge : vérifier le profil et les fichiers de dépendances du dossier sélectionné.')
  }
  const selected = { ...form, frontendProfile: profile, detectedKind: analysis.applicationType,
    detectedFramework: analysis.framework, detectedVite: Boolean(analysis.isVite),
    hasNpmLockfile: Boolean(analysis.hasNpmLockfile), hasDockerfile: Boolean(analysis.hasDockerfile) }
  return selected
}

/** Only update the selected environment's public frontend variables.
 * Do not delete unrelated runtime variables or secret references.
 */
export async function syncFrontendVariables(projectId, envId, rows, analysis) {
  const desired = publicEnvForFramework(rows, analysis)
  const original = (await projectsApi.variables(projectId, envId))?.variables || []
  const prefix = analysis?.profile === 'next_export' || analysis?.profile === 'next_server' ? 'NEXT_PUBLIC_' : analysis?.profile === 'vite' || analysis?.isVite ? 'VITE_' : ''
  const publicKeys = prefix ? key => key.startsWith(prefix) : key => /^(?:VITE_|REACT_APP_|VUE_APP_)/.test(key)
  const previous = new Map(original.filter(v => publicKeys(v.key) && !v.secret_id).map(v => [v.key, v]))
  const expected = new Set(desired.map(v => v.key))
  for (const row of desired) {
    const item = previous.get(row.key)
    if (!item) await projectsApi.createVariable(projectId, envId, row)
    else if (item.value !== row.value) await projectsApi.updateVariable(projectId, envId, item.id, row)
  }
  for (const item of previous.values()) {
    if (!expected.has(item.key)) await projectsApi.deleteVariable(projectId, envId, item.id)
  }
}

export function toBackendConfiguration(form) {
  const advanced = form.advancedBuild || {}
  const rawSource = (form.workdir || './').trim()
  const path = rawSource === './' ? '' : rawSource.replace(/^\.\//, '').replace(/\/$/, '')
  if (path.startsWith('/') || (path && path.split('/').some(part => ['.', '..', ''].includes(part))) || path.includes('\\')) throw new Error('Dossier source Git invalide.')
  const profile = form.frontendProfile || advanced.profile || (form.detectedFramework === 'Next.js' ? 'next_server' : form.detectedKind === 'static' && form.detectedFramework === 'HTML / CSS / JavaScript' ? 'html' : form.detectedVite ? 'vite' : form.detectedFramework === 'React' || form.detectedFramework === 'Vue' ? 'npm_static' : '')
  // Keep pre-existing draft behavior for manually supplied Dockerfiles. A free
  // plan can still never finalize a custom Dockerfile without a paid entitlement.
  if (!profile && !form.detectedFramework) return {
    mode: (form.pipelineMode || 'devops').toUpperCase(), branch: form.branch,
    root_directory: path, build_method: 'DOCKERFILE', runtime_type: 'OTHER',
    dockerfile_path: String(advanced.dockerfile || 'Dockerfile'),
    build_command: String(advanced.buildCommand || ''), start_command: String(advanced.startCommand || ''),
    output_directory: String(advanced.outputDir || ''), auto_deploy: Boolean(form.autoDeploy),
    high_availability_requested: Boolean(form.highAvailability),
    auto_deploy_branch: form.autoDeploy ? form.branch : '',
  }

  if (!['vite', 'npm_static', 'html', 'next_server', 'next_export', ...BACKEND_PROFILES].includes(profile)) throw new Error('Recette de déploiement non autorisée.')
  const isBackend = BACKEND_PROFILES.includes(profile)
  const isServer = profile === 'next_server' || isBackend
  const isHtml = profile === 'html'
  const output = isHtml || isBackend ? '' : isServer ? '.next' : profile === 'next_export' ? 'out' : profile === 'vite' ? 'dist' : String(advanced.outputDir || (form.detectedFramework === 'Vue' ? 'dist' : 'build')).trim()
  if (output && (!/^[A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.-]+)*$/.test(output) || output.split('/').includes('..'))) throw new Error('Dossier de sortie invalide.')
  if (profile === 'npm_static' && !['dist', 'build'].includes(output)) throw new Error('Dossier de sortie autorisé : dist ou build.')
  if (!isHtml && !isBackend && form.hasNpmLockfile === false) throw new Error('package-lock.json absent du dossier source.')
  const buildCommand = ['vite','npm_static','next_server','next_export'].includes(profile) ? String(advanced.buildCommand || '').trim() : ''
  if (buildCommand && !/^npm run [a-zA-Z][a-zA-Z0-9:_-]{0,63}$/.test(buildCommand)) throw new Error('Commande de build autorisée : npm run NOM.')
  const mode = (form.pipelineMode || 'devops').toUpperCase()
  if (!['DEVOPS', 'DEVSECOPS'].includes(mode)) throw new Error('Mode de pipeline invalide.')
  return {
    mode, branch: form.branch, root_directory: path,
    build_method: isServer ? 'AUTO' : 'STATIC', runtime_type: isBackend ? (profile.startsWith('python_') ? 'PYTHON' : profile.startsWith('node_') ? 'NODE' : profile.startsWith('java_') ? 'JAVA' : 'PHP') : isServer ? 'NODE' : 'STATIC',
    dockerfile_path: 'Dockerfile', build_command: buildCommand,
    start_command: profile === 'next_server' ? 'npm run start' : '', output_directory: output,
    settings: { frontend_profile: profile },
    healthcheck_path: String(advanced.healthcheckPath || '/').trim(),
    auto_deploy: Boolean(form.autoDeploy), auto_deploy_branch: form.autoDeploy ? form.branch : '',
    high_availability_requested: Boolean(form.highAvailability),
  }
}

export function backendProjectRecord(p) {
  return {
    id: String(p.id), name: p.name, repository: p.repository_name || '', branch: p.default_branch || '',
    workdir: './', environment: 'production', mode: 'devops', offer: 'free', pricingPlan: 'free',
    domainMode: 'autokom', domain: '', updatedAt: p.updated_at || p.created_at || '',
    status: p.status, publicSite: p.public_site || null, live: true, preview: null, draft: null,
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
  const [configsResponse, runsResponse, domainsResponse, variablesResponse] = await Promise.all([
    projectsApi.configurations(project.id, env.id),
    deploymentsApi.list(project.id, env.id, 1, 10),
    projectsApi.domains(project.id, env.id),
    projectsApi.variables(project.id, env.id),
  ])
  return { project, environment: env, configurations: configsResponse?.configurations || [], runs: runsResponse?.runs || [], domains: domainsResponse?.domains || [], variables: variablesResponse?.variables || [], dnsSetup: domainsResponse?.dns_setup || null, runCount: runsResponse?.count ?? (runsResponse?.runs || []).length }
}

/** Création d'un projet puis mise à jour de son unique configuration initiale.
 * `onCreated` est appelé immédiatement après le POST : un échec ultérieur peut être repris
 * sans créer un second projet GitHub avec le même nom.
 */
export async function saveRemoteProject(form, { onCreated = () => {} } = {}) {
  const configurationValues = toBackendConfiguration(form)
  let projectId = form.backendProjectId || ''
  let envId = form.backendEnvironmentId || ''
  // Les offres payantes ne sont jamais accordees par le navigateur : Django valide
  // les droits ACTIFS avant toute finalisation. Une demande HA reste un brouillon.
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
  // Ne pas confondre selection UI et entitlement. Seul offers.entitlement accorde
  // les droits serveur ; une option HA non active bloque sa finalisation.
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
  if (form.frontendEnvTouched && !BACKEND_PROFILES.includes(form.frontendProfile || form.advancedBuild?.profile)) {
    await syncFrontendVariables(projectId, envId, form.frontendEnv || [], {
      isVite: form.detectedVite, applicationType: form.detectedKind, framework: form.detectedFramework, profile: form.frontendProfile || form.advancedBuild?.profile || (form.detectedFramework === 'Next.js' ? 'next_server' : form.detectedVite ? 'vite' : 'npm_static'),
    })
  }
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
  const granted = detail.offers?.entitlement
  const code = granted?.resource_plan_code || ''
  const paid = Boolean(granted?.status === 'ACTIVE' && (code.endsWith('-test') || code !== 'starter'))
  const grantedPlan = paid ? (Number(granted?.max_instances) >= 2 ? 'pro' : 'essential')
    : (cfg.high_availability_requested ? 'pro' : 'free')
  return {
    backendProjectId: project.id, backendEnvironmentId: env.id,
    organizationId: project.organization, githubInstallationId: project.github_installation_id || '',
    pricingPlan: grantedPlan, resourcePlan: grantedPlan === 'free' ? 'free' : 'adapted', domainMode: 'autokom',
    highAvailability: Boolean(cfg.high_availability_requested),
    projectName: project.name, repositorySlug: project.repository_name,
    repository: project.repository_url, githubRepositoryId: project.repository_external_id,
    branch: cfg.branch || project.default_branch || 'main', workdir: cfg.root_directory || './',
    pipelineMode: (cfg.mode || 'DEVOPS').toLowerCase(),
    environment: env.slug,
    autoDeploy: Boolean(cfg.auto_deploy),
    frontendEnv: (detail.variables || []).filter(item => /^(?:VITE_|REACT_APP_|VUE_APP_|NEXT_PUBLIC_)/.test(item.key) && !item.secret_id).map(item => ({key:item.key, value:item.value})),
    frontendEnvTouched: false,
    advancedBuild: { healthcheckPath: cfg.healthcheck_path || '/', profile: cfg.settings?.frontend_profile || '', dockerfile: cfg.dockerfile_path || 'Dockerfile', buildCommand: cfg.build_command || '',
      startCommand: cfg.start_command || '', outputDir: cfg.output_directory || '', appPort: cfg.internal_port || 8000 },
  }
}
