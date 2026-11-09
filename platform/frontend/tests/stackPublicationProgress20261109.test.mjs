import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { stackApi } from '../src/services/stackApi.js'
import { publicationPath, publicationKind } from '../src/services/stackHistory.js'
import { publicationLabel } from '../src/services/deploymentPresentation.js'
import { isPublishing } from '../src/services/deploymentHistory.js'

const project = '11111111-1111-4111-8111-111111111111'
const run = '22222222-2222-4222-8222-222222222222'
const file = path => readFileSync(new URL(`../src/${path}`, import.meta.url),'utf8')

test('la publication groupée possède sa propre route historique et ne mélange pas les runs simples', () => {
  assert.equal(publicationPath(project,{id:run,kind:'stack'}),`/projects/${project}/stack/deployments/${run}`)
  assert.equal(publicationPath(project,{id:run,kind:'single'}),`/projects/${project}/deployments/${run}`)
  assert.equal(publicationKind({kind:'stack'}),'Frontend + Backend')
  assert.equal(publicationKind({kind:'single'}),'Application')
  assert.match(file('App.jsx'),/stack\\\/deployments\\\/[^/]+/)
})

test('l’historique Django est interrogé sans POST ni réexécution CI, avec pagination bornée', async () => {
  const previous = globalThis.fetch
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push([url,options])
    return {ok:true,status:200,json:async()=>({success:true,data:{runs:[],count:0}})}
  }
  try {
    const result = await stackApi.history(project,2,20)
    assert.equal(result.count,0)
    assert.match(calls[0][0],/\/projects\/11111111-1111-4111-8111-111111111111\/stack\/history\/\?page=2&page_size=20/)
    assert.equal(calls[0][1].method,'GET')
    assert.throws(()=>stackApi.history(project,1,100),/Taille de page/)
  } finally {globalThis.fetch=previous}
})

test('les statuts groupe et services sont affichés sans déclarer PREPARED publiquement en ligne', () => {
  assert.equal(isPublishing('PREPARING'),true)
  assert.equal(isPublishing('ACTIVATING'),true)
  assert.equal(isPublishing('SUCCEEDED'),false)
  assert.equal(publicationLabel('PREPARED')[0],'Prêt à activer')
  assert.equal(publicationLabel('ACTIVATING')[0],'Activation HTTPS')
})

test('l’étape 3 utilise le composant partagé avec l’historique et conserve les journaux Django par service', () => {
  const stack = file('pages/StackDeploy.jsx')
  const component = file('components/StackPublicationProgress.jsx')
  const projectPage = file('pages/LiveProjects.jsx')
  const history = file('pages/LiveRuns.jsx')
  assert.match(stack,/<StackPublicationProgress projectId=\{projectId\} runId=\{deployment\.id\}/)
  assert.match(component,/SIDES = \['backend', 'frontend'\]/)
  assert.match(component,/deploymentsApi\.detail\(projectId, child\.environment_id, child\.run_id\)/)
  assert.match(component,/deploymentsApi\.logs\(projectId, child\.environment_id, child\.run_id, cursors\.current\[side\]\[mode\], mode\)/)
  assert.match(component,/stackApi\.publication\(projectId,runId\)/)
  assert.match(component,/role="tablist"/)
  assert.match(component,/phaseOrder\.map/)
  assert.match(component,/technicalTimeline\(selectedLogs\)/)
  assert.match(component,/interval\(refresh,5000\)/i)
  assert.match(projectPage,/stackApi\.history\(projectId, 1, 10\)/)
  assert.match(history,/stackApi\.history\(projectId,page,PAGE_SIZE\)/)
  assert.match(projectPage,/publicationPath\(project\.id, run\)/)
  assert.match(history,/publicationPath\(projectId,run\)/)
  assert.doesNotMatch(component,/autokom.*launch\(/i)
})
