import test from 'node:test'
import assert from 'node:assert/strict'
import { SAMPLE_REPOSITORIES, getSampleDirectory, getSamplePackage, getSampleRepository, createPipelinePreview, PIPELINE_STAGE_NAMES } from '../src/services/simulationData.js'
import { detectSource } from '../src/services/sourceAnalysis.js'

test('le catalogue contient des branches, dépôts privés et sources parcourables', () => {
  assert.equal(SAMPLE_REPOSITORIES.length, 4)
  assert.ok(SAMPLE_REPOSITORIES.some(repo => repo.private))
  assert.ok(SAMPLE_REPOSITORIES.every(repo => repo.branches.includes(repo.defaultBranch)))
  assert.deepEqual(getSampleRepository('1002').branches, ['main','staging','feature/dashboard'])
  assert.ok(getSampleDirectory('1002','./').some(entry => entry.type === 'dir' && entry.path === 'apps'))
  assert.ok(getSampleDirectory('1002','apps').some(entry => entry.path === 'apps/web'))
})

test('l’analyse de source du monorepo reflète le dossier sélectionné', () => {
  const web = detectSource(getSampleDirectory('1002','apps/web'), getSamplePackage('1002','apps/web'))
  assert.equal(web.framework, 'React')
  assert.equal(web.build.outputDir, 'dist')
  const api = detectSource(getSampleDirectory('1002','apps/api'),getSamplePackage('1002','apps/api'))
  assert.equal(api.framework, 'Django')
})

test('les parcours de pipeline sont fictifs et dépendent du mode choisi', () => {
  const base = { project: {name:'Espace client'}, source:{ repository:'ikom-exemple/espace-client',branch:'staging',workdir:'apps/web'}, source_detection:{framework:'React'}, hosting:{offer:'free'}, pipeline:{mode:'devsecops'} }
  const preview = createPipelinePreview(base)
  assert.equal(preview.preview,true)
  assert.equal(preview.stages.length,PIPELINE_STAGE_NAMES.devsecops.length)
  assert.ok(preview.stages.includes('Analyses de sécurité'))
  const devops = createPipelinePreview({ ...base, pipeline:{ mode:'devops' } })
  assert.ok(!devops.stages.includes('Analyses de sécurité'))
})
