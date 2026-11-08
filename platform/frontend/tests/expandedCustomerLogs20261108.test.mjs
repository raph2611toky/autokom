import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { technicalTimeline } from '../src/services/deploymentLogTimeline.js'

test('summary preserves old technical chronological display', () => {
  const items=[
    {sequence:1,level:'INFO',message:'AUTOKOM_STAGE:release:RUNNING'},
    {sequence:2,level:'TECH',message:'AUTOKOM_TECH:release:Publication contrôlée démarrée.'},
  ]
  assert.deepEqual(technicalTimeline(items).map(x=>x.sequence),[1,2])
})
test('technical file view includes safe docker diagnostics from the backend', () => {
  const row={sequence:8,level:'DETAIL',message:'AUTOKOM_TRACE:release:application:ModuleNotFoundError: No module named \'flask\''}
  const result=technicalTimeline([row])
  assert.equal(result[0].channel,'application')
  assert.equal(result[0].stage_key,'release')
  assert.equal(result[0].isDetail,true)
  assert.match(result[0].display,/ModuleNotFoundError/)
  assert.deepEqual(technicalTimeline([{...row,message:'SECRET=do-not-print'}]),[])
})
test('frontend offers same timeline and authenticated log export with existing project route', () => {
  const component=readFileSync(new URL('../src/pages/LiveRuns.jsx',import.meta.url),'utf8')
  assert.match(component,/mode === 'technical'|logMode==='technical'/)
  assert.match(component,/logs\/download\//)
  assert.match(component,/Télécharger \.log/)
  assert.doesNotMatch(component,/jenkins\.ikom\.mg\/job/)
})
