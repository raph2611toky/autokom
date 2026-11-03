import test from 'node:test'
import assert from 'node:assert/strict'
import { createPipelinePreview } from '../src/services/simulationData.js'
import { DRAFTS_KEY, PREVIEWS_KEY, getDrafts, getPreviews, getProjectRecords, getProjectRecord, storePreview, removeProject } from '../src/services/projectWorkspace.js'

const memory = new Map()
globalThis.localStorage = {
  getItem(key) { return memory.has(key) ? memory.get(key) : null },
  setItem(key,value) { memory.set(key,String(value)) },
  removeItem(key) { memory.delete(key) },
}

const config = (id,name,mode='devsecops') => ({
  project:{id,name,environment:'production'},
  source:{repository:`test/${name}`,branch:'main',workdir:'src'},
  source_detection:{framework:'React'},
  hosting:{offer:'free',domain_mode:'autokom'},
  pipeline:{mode},
})

test('un projet peut être enregistré comme brouillon sans pipeline', () => {
  memory.clear()
  localStorage.setItem(DRAFTS_KEY,JSON.stringify([{id:'p1', projectName:'alpha',repositorySlug:'test/alpha',branch:'main',workdir:'src'}]))
  const project=getProjectRecord('p1')
  assert.equal(project.name,'alpha')
  assert.equal(project.preview,null)
})

test('chaque projet conserve son propre pipeline et ne voit pas celui des autres', () => {
  memory.clear()
  localStorage.setItem(DRAFTS_KEY,JSON.stringify([
    {id:'p1',projectName:'alpha',repositorySlug:'test/alpha',branch:'main',updatedAt:'2026-11-03T10:00:00.000Z'},
    {id:'p2',projectName:'beta',repositorySlug:'test/beta',branch:'develop',updatedAt:'2026-11-03T11:00:00.000Z'},
  ]))
  storePreview(createPipelinePreview(config('p1','alpha')))
  storePreview(createPipelinePreview(config('p2','beta','devops')))
  const list=getProjectRecords()
  assert.equal(list.length,2)
  assert.equal(list[0].id,'p2')
  assert.ok(getProjectRecord('p1').preview.stages.includes('Analyses de sécurité'))
  assert.ok(!getProjectRecord('p2').preview.stages.includes('Analyses de sécurité'))
  assert.equal(getProjectRecord('p1').preview.repository,'test/alpha')
})

test('supprimer un projet retire son aperçu sans effacer les autres', () => {
  removeProject('p1')
  assert.deepEqual(getDrafts().map(d=>d.id),['p2'])
  assert.deepEqual(getPreviews().map(p=>p.id),['p2'])
  assert.equal(getProjectRecord('p1'),null)
})

test('un aperçu V13 peut être récupéré pour son projet et supprimé sans réapparaître', () => {
  memory.clear()
  const old = createPipelinePreview(config(null,'legacy'))
  localStorage.setItem('autokom.ui.pipeline.preview.v13',JSON.stringify(old))
  localStorage.setItem(DRAFTS_KEY,JSON.stringify([{id:'a-legacy',projectName:'legacy',repositorySlug:'test/legacy'}]))
  assert.equal(getProjectRecord('a-legacy').preview.name,'legacy')
  removeProject('a-legacy')
  assert.equal(getPreviews().length,0)
  assert.equal(localStorage.getItem('autokom.ui.pipeline.preview.v13'),null)
})
