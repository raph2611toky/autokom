import test from 'node:test'
import assert from 'node:assert/strict'
import { technicalTimeline } from '../src/services/deploymentLogTimeline.js'

test('technical view contains summary logs and stage transitions in order', () => {
  const lines = [
    {sequence:4, level:'INFO', message:'AUTOKOM_LOG: Exécution terminée : FAILED'},
    {sequence:3, level:'TECH', message:'AUTOKOM_TECH:source:Référence d’identifiants du job introuvable : intervention administrateur nécessaire.'},
    {sequence:2, level:'INFO', message:'AUTOKOM_STAGE:source:RUNNING'},
    {sequence:1, level:'INFO', message:'AUTOKOM_LOG: Préparation du projet'},
  ]
  const result = technicalTimeline(lines)
  assert.deepEqual(result.map(x=>x.sequence),[1,2,3,4])
  assert.ok(result.some(x=>x.display.includes('Récupération du projet')))
  assert.ok(result.some(x=>x.display.includes('Référence d’identifiants')))
  assert.ok(result.some(x=>x.display.includes('Exécution terminée')))
  assert.ok(result.every(x=>x.stage_key==='source'))
})

test('unknown untrusted TECH messages are never displayed', () => {
  assert.deepEqual(technicalTimeline([{sequence:1,level:'TECH',message:'PRIVATE_KEY=abcd'}]),[])
})
