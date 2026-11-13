import assert from 'node:assert/strict'
import { test } from 'node:test'
import { estimateResources } from '../src/services/resourceEstimate.js'
import { detectSource } from '../src/services/sourceAnalysis.js'

const valid = { available: true }

test('l’estimation suit le profil sélectionné, y compris le choix export ou SSR', () => {
  const exportStatic = estimateResources([{ profile:'next_export', analysis:valid }])
  const server = estimateResources([{ profile:'next_server', analysis:valid }])
  assert.ok(server.total.cpu > exportStatic.total.cpu)
  assert.ok(server.total.ram > exportStatic.total.ram)
})

test('les dépendances de production du dépôt modulent le budget Node', () => {
  const files = [{type:'file', name:'package.json'}]
  const small = detectSource(files, {dependencies:{express:'x'}, devDependencies:{vite:'x'}})
  const large = detectSource(files, {dependencies:Object.fromEntries(Array.from({length:30}, (_,i)=>['dep'+i,'x']))})
  assert.equal(small.productionDependencyCount,1)
  assert.equal(large.productionDependencyCount,30)
  const build = analysis => estimateResources([{ profile:'node_express', analysis }]).total
  assert.ok(build(large).ram > build(small).ram)
})

test('le récapitulatif additionne les services et les répliques demandées', () => {
  const parts = [{profile:'vite',analysis:valid},{profile:'python_django',analysis:valid}]
  const one = estimateResources(parts)
  const two = estimateResources(parts, {highAvailability:true})
  assert.equal(one.services.length,2)
  assert.equal(two.total.ram,2*one.total.ram)
  assert.equal(two.replicas,2)
})

test('pas de chiffre présenté avant une analyse valide', () => {
  assert.equal(estimateResources([{profile:'vite',analysis:null}]),null)
  assert.equal(estimateResources([{profile:'unknown',analysis:valid}]),null)
})
