import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { api } from '../src/services/api.js'
import { toBackendConfiguration, errorText, saveRemoteProject, prepareRemoteDeployment,
  listRemoteProjectRecords, getRemoteProjectDetail } from '../src/services/projectWorkflow.js'

const UUIDS = {
  project: '11111111-1111-4111-8111-111111111111',
  env: '22222222-2222-4222-8222-222222222222',
  cfg: '33333333-3333-4333-8333-333333333333',
  org: '44444444-4444-4444-8444-444444444444',
}
const baseForm = { organizationId: UUIDS.org, githubInstallationId: 123, githubRepositoryId: 456,
  projectName: 'Application test', branch: 'main', workdir: './', pipelineMode: 'devops',
  environment: 'production', pricingPlan: 'free', autoDeploy: true, advancedBuild: {} }
const savedFetch = globalThis.fetch
const savedDocument = globalThis.document
function fakeResponse(value, code = 200) {
  return { ok: code < 400, status: code, json: async () => ({ success: code < 400, data: value, errors: null }) }
}
function mocked(handlers) {
  const calls = []
  globalThis.document = { cookie: 'csrftoken=csrf-test' }
  globalThis.fetch = async (path, options) => {
    calls.push({ path, options })
    const key = `${options.method} ${path}`
    const result = handlers[key]
    if (!result) throw new Error(`Endpoint inattendu: ${key}`)
    return fakeResponse(typeof result === 'function' ? result(options) : result)
  }
  return calls
}
function restore() {
  globalThis.fetch = savedFetch
  if (savedDocument === undefined) delete globalThis.document
  else globalThis.document = savedDocument
}

test('format Django : Dockerfile, pas de données UI fictives ni de serveur lancé', () => {
  const data = toBackendConfiguration(baseForm)
  assert.equal(data.mode, 'DEVOPS')
  assert.equal(data.build_method, 'DOCKERFILE')
  assert.equal(data.dockerfile_path, 'Dockerfile')
  assert.equal(data.root_directory, '')
  assert.equal(data.auto_deploy, true)
  assert.equal(Object.hasOwn(data, 'internal_port'), false)
  assert.equal(data.runtime_type, 'OTHER')
  assert.ok(!Object.hasOwn(data, 'services'))
  assert.throws(() => toBackendConfiguration({ ...baseForm, workdir: '../secrets' }), /Dossier/)
  assert.equal(Object.hasOwn(toBackendConfiguration({ ...baseForm, advancedBuild: { appPort: 90000 } }), 'internal_port'), false)
  assert.match(errorText({ errors: { branch: ['Branche Git invalide.'] } }), /branch : Branche Git invalide/)
})

test('enregistrer : création projet + configuration du brouillon, aucun lancement Jenkins', async () => {
  const created = []
  const pathBase = `/api/projects/${UUIDS.project}/environments/${UUIDS.env}`
  const calls = mocked({
    'POST /api/projects/create/': options => {
      const body = JSON.parse(options.body)
      assert.equal(body.organization_id, UUIDS.org)
      assert.equal(body.repository_id, 456)
      return { project: { id: UUIDS.project }, environment_id: UUIDS.env }
    },
    [`GET /api/projects/${UUIDS.project}/environments/`]: { environments: [{ id: UUIDS.env, kind: 'PRODUCTION', slug: 'production' }] },
    [`GET ${pathBase}/configurations/offers/`]: { entitlement: { mode: 'DEVOPS', resource_plan: UUIDS.org } },
    [`GET ${pathBase}/configurations/`]: { configurations: [{ id: UUIDS.cfg, is_draft: true }] },
    [`PATCH ${pathBase}/configurations/${UUIDS.cfg}/update/`]: options => {
      const body = JSON.parse(options.body)
      assert.equal(body.mode, 'DEVOPS')
      assert.equal(body.resource_plan, UUIDS.org)
      return { configuration: { id: UUIDS.cfg } }
    },
  })
  try {
    const saved = await saveRemoteProject(baseForm, { onCreated: ids => created.push(ids) })
    assert.equal(saved.projectId, UUIDS.project)
    assert.equal(saved.configurationId, UUIDS.cfg)
    assert.ok(created.some(ids => ids.backendProjectId === UUIDS.project))
    assert.ok(calls.every(c => !c.path.includes('/deployments/launch/')))
    assert.ok(calls.every(c => c.options.credentials === 'same-origin'))
    assert.ok(calls.every(c => !c.options.headers.Authorization))
  } finally { restore() }
})

test('création environnement de préproduction réelle à partir des endpoints Django', async () => {
  const pathBase = `/api/projects/${UUIDS.project}/environments/${UUIDS.env}`
  const calls = mocked({
    [`GET /api/projects/${UUIDS.project}/environments/`]: { environments: [{ id: UUIDS.org, kind: 'PRODUCTION', slug: 'production' }] },
    [`POST /api/projects/${UUIDS.project}/environments/create/`]: { environment: { id: UUIDS.env, kind: 'STAGING' } },
    [`GET ${pathBase}/configurations/offers/`]: { entitlement: { mode: 'DEVOPS', resource_plan: UUIDS.org } },
    [`GET ${pathBase}/configurations/`]: { configurations: [] },
    [`POST ${pathBase}/configurations/create/`]: { configuration: { id: UUIDS.cfg } },
  })
  try {
    const saved = await saveRemoteProject({ ...baseForm, backendProjectId: UUIDS.project, environment: 'staging' })
    assert.equal(saved.environmentId, UUIDS.env)
    assert.ok(calls.find(c => c.path.endsWith('/environments/create/')))
  } finally { restore() }
})

test('prévisualisation finalise puis GET ; ne lance jamais Jenkins implicitement', async () => {
  const prefix = `/api/projects/${UUIDS.project}/environments/${UUIDS.env}`
  const calls = mocked({
    [`POST ${prefix}/configurations/${UUIDS.cfg}/finalize/`]: { configuration: { id: UUIDS.cfg, is_draft: false } },
    [`GET ${prefix}/deployments/preview/`]: { configuration_id: UUIDS.cfg, mode: 'DEVOPS' },
  })
  try {
    const preview = await prepareRemoteDeployment({ projectId: UUIDS.project, environmentId: UUIDS.env, configurationId: UUIDS.cfg })
    assert.equal(preview.mode, 'DEVOPS')
    assert.equal(calls.length, 2)
    assert.ok(!calls.some(c => c.path.includes('launch')))
  } finally { restore() }
})

test('Mes projets utilise la pagination Django, jamais les brouillons localStorage', async () => {
  const calls = mocked({
    'GET /api/projects/?page=1&page_size=50': { projects: Array.from({ length: 50 }, (_, i) => ({ id: `project-${i}`, name: `Projet ${i}`, status: 'ACTIVE' })), total: 51 },
    'GET /api/projects/?page=2&page_size=50': { projects: [{ id: 'project-51', name: 'Projet 51', status: 'ARCHIVED' }], total: 51 },
  })
  try {
    const records = await listRemoteProjectRecords()
    assert.equal(records.length, 51)
    assert.equal(records[50].status, 'ARCHIVED')
    assert.equal(calls.length, 2)
  } finally { restore() }
})

test('Détails réels : configurations et derniers runs Django', async () => {
  const p = `/api/projects/${UUIDS.project}`
  mocked({
    [`GET ${p}/`]: { project: { id: UUIDS.project, name: 'Test', repository_name: 'org/test' }, environments: [{ id: UUIDS.env, slug: 'production' }] },
    [`GET ${p}/environments/${UUIDS.env}/configurations/`]: { configurations: [{ id: UUIDS.cfg, is_draft: true }] },
    [`GET ${p}/environments/${UUIDS.env}/deployments/?page=1&page_size=10`]: { runs: [{ id: 'deployment-1', status: 'QUEUED' }], count: 37 },
  })
  try {
    const details = await getRemoteProjectDetail(UUIDS.project)
    assert.equal(details.configurations[0].is_draft, true)
    assert.equal(details.runs[0].status, 'QUEUED')
    assert.equal(details.runCount, 37)
  } finally { restore() }
})

test('inscription : redirection et messages serveur, vérification avec renvoi', () => {
  const register = readFileSync(new URL('../src/pages/Register.jsx', import.meta.url), 'utf8')
  const verification = readFileSync(new URL('../src/pages/VerifyEmail.jsx', import.meta.url), 'utf8')
  assert.match(register, /navigate\('\/verify-email'\)/)
  assert.match(register, /setServerErrors/)
  assert.match(register, /error\?\.errors/)
  assert.doesNotMatch(register, /finally\s*\{\s*setForm\(/)
  assert.match(verification, /resendEmail\(email\)/)
})

test('API GitHub directories : encode les chemins, refuse la traversée et transmet le cookie', async () => {
  const { gitApi } = await import('../src/services/projectsApi.js')
  const calls = mocked({
    'GET /api/projects/git/github/directories/?installation_id=7&repository_id=45&branch=feature%2Ftest&path=src%2Fmy+folder': { directories: [] },
  })
  try {
    const dirs = await gitApi.directories(7, 45, 'feature/test', 'src/my folder')
    assert.deepEqual(dirs.directories, [])
    assert.equal(calls[0].options.credentials, 'same-origin')
    assert.throws(() => gitApi.directories(7, 45, 'main', '../root'), /Dossier/)
  } finally { restore() }
})
