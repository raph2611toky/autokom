import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { deploymentsApi } from '../src/services/deploymentsApi.js'

const proj='11111111-1111-4111-8111-111111111111'
const env='22222222-2222-4222-8222-222222222222'
const run='33333333-3333-4333-8333-333333333333'

test('technical and summary modes are both available with authorized Django endpoints', async () => {
  const old=globalThis.fetch
  const calls=[]
  globalThis.fetch=async path=>{
    calls.push(path)
    return {ok:true,status:200,json:async()=>({success:true,data:{logs:[],next_after:0}})}
  }
  try {
    await deploymentsApi.logs(proj,env,run,0)
    await deploymentsApi.logs(proj,env,run,5,'simple')
    assert.match(calls[0],/mode=technical$/)
    assert.match(calls[1],/after=5&mode=simple$/)
    assert.throws(()=>deploymentsApi.logs(proj,env,run,5,'raw'),/Mode de journal/)
  } finally {globalThis.fetch=old}
})
test('journal UI has two modes and stage filtering but no direct Jenkins API', () => {
  const view=readFileSync(new URL('../src/pages/LiveRuns.jsx',import.meta.url),'utf8')
  assert.match(view,/useState\('simple'\)/)
  assert.match(view,/Technique<\/button>/)
  assert.match(view,/Synthèse<\/button>/)
  assert.match(view,/Filtrer par étape/)
  assert.match(view,/item\.stage_key === stageFilter/)
  assert.doesNotMatch(view,/jenkins\.ik0m\.tech|\/job\/autokom-client-deploy/)
})
