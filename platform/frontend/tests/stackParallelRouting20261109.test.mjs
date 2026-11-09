import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {publicationPath} from '../src/services/stackHistory.js'

const file = path => readFileSync(new URL(`../src/${path}`, import.meta.url),'utf8')
const projectId = '11111111-1111-4111-8111-111111111111'
const groupId = '22222222-2222-4222-8222-222222222222'

test('le routeur accepte réellement le détail de chaque publication groupée', () => {
  const app = file('App.jsx')
  const start = app.indexOf('function validRoute(path) {')
  const end = app.indexOf('\n}',start) + 2
  assert.ok(start >= 0 && end > start)
  // Execute the actual router predicate without mounting React.
  const validRoute = new Function('ROUTES', `${app.slice(start,end)}; return validRoute;`)(['/'])
  const target = publicationPath(projectId,{id:groupId,kind:'stack'})
  assert.equal(validRoute(target),true)
  assert.equal(validRoute(`/projects/${projectId}/history`),true)
  assert.equal(validRoute(`/projects/${projectId}/deployments/${groupId}`),true)
  assert.equal(validRoute(`/projects/${projectId}/stack/deployments/${groupId}/unexpected`),false)
  assert.equal(validRoute('/projects//stack/deployments/group'),false)
  assert.match(app,/StackPublicationProgress key=/)
})

test('les onglets conservent les références Jenkins et le diagnostic réel par service', () => {
  const progress=file('components/StackPublicationProgress.jsx')
  assert.match(progress,/current\?\.jenkins_reference/)
  assert.match(progress,/current\?\.error_message/)
  assert.match(progress,/child\?\.run_id/)
  assert.match(progress,/tab==='frontend'/)
  assert.doesNotMatch(progress,/En attente de la préparation du backend avant le lancement du frontend/)
})
