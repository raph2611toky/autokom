import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {stackApi} from '../src/services/stackApi.js'
import {publicationServices,publicationKind,publicationPath} from '../src/services/stackHistory.js'

const file = name => readFileSync(new URL(`../src/${name}`,import.meta.url),'utf8')
const project = '11111111-1111-4111-8111-111111111111'
const grouped = '22222222-2222-4222-8222-222222222222'

test('les deux services réussis apparaissent comme une publication groupée avec leur état réel', async () => {
  const calls = []
  const previous = globalThis.fetch
  globalThis.fetch = async (url, options) => {
    calls.push({url,method:options.method})
    return {ok:true,status:200,json:async()=>({success:true,data:{count:6,runs:[
      {id:grouped,kind:'stack',status:'SUCCEEDED',backend_status:'SUCCEEDED',frontend_status:'SUCCEEDED',trigger_type:'MANUAL',created_at:'2026-11-09T13:38:00Z'},
      {id:'older',kind:'single',status:'SUCCEEDED',created_at:'2026-11-08T10:00:00Z'},
    ]}})}
  }
  try {
    const feed = await stackApi.history(project,1,10)
    assert.equal(feed.count,6)
    assert.equal(feed.runs[0].kind,'stack')
    assert.equal(publicationServices(feed.runs[0]),'Backend : Réussi · Frontend : Réussi')
    assert.equal(publicationServices(feed.runs[1]),'')
    assert.equal(publicationKind(feed.runs[0]),'Frontend + Backend')
    assert.equal(publicationPath(project,feed.runs[0]),`/projects/${project}/stack/deployments/${grouped}`)
    assert.equal(calls.length,1)
    assert.equal(calls[0].method,'GET')
  } finally {globalThis.fetch = previous}
})

test('états des services ne sont jamais extrapolés de la réussite d’un seul job', () => {
  assert.equal(publicationServices({kind:'stack',backend_status:'PREPARED',frontend_status:'PENDING'}),
    'Backend : Prêt à activer · Frontend : En attente')
  assert.equal(publicationServices({kind:'stack',backend_status:'FAILED',frontend_status:'PENDING'}),
    'Backend : Échec · Frontend : En attente')
})

test('fiche projet et historique utilisent TOUJOURS le flux consolidé, sans indicateur de mode comme précondition', () => {
  const projectPage = file('pages/LiveProjects.jsx')
  const history = file('pages/LiveRuns.jsx')
  assert.match(projectPage,/Promise\.all\(\[getRemoteProjectDetail\(projectId\), stackApi\.detail\(projectId\), stackApi\.history\(projectId, 1, 10\)\]\)/)
  assert.doesNotMatch(projectPage,/if \(!enabled\) return \{info, enabled\}/)
  assert.doesNotMatch(projectPage,/stackEnabled \? stackApi\.history/)
  assert.match(projectPage,/window\.addEventListener\('focus', refresh\)/)
  assert.match(projectPage,/publicationServices\(run\)/)
  assert.match(projectPage,/navigate\(publicationPath\(project\.id, active\)\)/)
  const runHistory = history.slice(history.indexOf('function RunHistory('))
  assert.match(runHistory,/stackApi\.history\(projectId,page,PAGE_SIZE\)/)
  assert.doesNotMatch(runHistory,/stackEnabled/)
  assert.match(runHistory,/publicationServices\(run\)/)
  assert.match(runHistory,/window\.addEventListener\('focus', refresh\)/)
  assert.match(runHistory,/window\.setInterval\(refresh, 5000\)/)
  assert.doesNotMatch(runHistory,/deploymentsApi\.list/)
})
