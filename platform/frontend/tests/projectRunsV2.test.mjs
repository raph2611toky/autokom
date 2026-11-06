import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { deploymentsApi } from '../src/services/deploymentsApi.js'
import { canPublish, isPublishing, sortRecentRuns } from '../src/services/deploymentHistory.js'
const proj = '11111111-1111-4111-8111-111111111111'
const env = '22222222-2222-4222-8222-222222222222'
const run = '33333333-3333-4333-8333-333333333333'
const savedFetch = globalThis.fetch
const savedDocument = globalThis.document
function mockFetch() {
  const calls = []
  globalThis.document = { cookie:'csrftoken=test' }
  globalThis.fetch = async (path,opts) => {
    calls.push({path,opts})
    const data = path.includes('launch') ? {deployment:{id:run},enqueued:true} : path.includes('logs') ? {logs:[],next_after:0} : {runs:[],count:0}
    return {ok:true,status:202,json:async () => ({success:true,data})}
  }
  return calls
}
function restore() {
  globalThis.fetch = savedFetch
  if(savedDocument === undefined) delete globalThis.document
  else globalThis.document = savedDocument
}

test('les publications les plus récentes sont affichées en premier sans mutation de l’API', () => {
  const raw = [
    {id:'a',created_at:'2026-11-01T10:00:00Z'},
    {id:'c',created_at:'2026-11-05T10:00:00Z'},
    {id:'b',created_at:'2026-11-03T10:00:00Z'},
  ]
  assert.deepEqual(sortRecentRuns(raw).map(x=>x.id),['c','b','a'])
  assert.equal(raw[0].id,'a')
})
test('publication manuelle désactivée si un run actif, un brouillon ou un projet archivé', () => {
  const detail = {project:{status:'ACTIVE'},environment:{id:env,is_active:true},configurations:[{is_draft:false}],runs:[]}
  assert.equal(canPublish(detail),true)
  assert.equal(isPublishing('RUNNING'),true)
  assert.equal(canPublish({...detail,runs:[{status:'QUEUED'}]}),false)
  assert.equal(canPublish({...detail,configurations:[{is_draft:true}]}),false)
  assert.equal(canPublish({...detail,configurations:[{is_draft:true},{is_draft:false}]}),true)
  assert.equal(canPublish({...detail,project:{status:'ARCHIVED'}}),false)
  assert.equal(canPublish({...detail,environment:{id:env,is_active:false}}),false)
})
test('les API respectent les limites Django et ne permettent pas de charger toutes les publications en un appel', async () => {
  const calls = mockFetch()
  try {
    await deploymentsApi.list(proj,env,1,10)
    await deploymentsApi.list(proj,env,2,20)
    assert.match(calls[0].path,/page=1&page_size=10/)
    assert.match(calls[1].path,/page=2&page_size=20/)
    assert.throws(()=>deploymentsApi.list(proj,env,1,100),/Taille de page/)
  } finally {restore()}
})
test('le lancement est un POST protégé par CSRF et clé idempotente, sans appel implicite', async () => {
  const calls = mockFetch()
  try {
    await deploymentsApi.launch(proj,env,'11111111-1111-4111-8111-111111111111')
    assert.equal(calls[0].opts.method,'POST')
    assert.equal(calls[0].opts.credentials,'same-origin')
    assert.equal(calls[0].opts.headers['Idempotency-Key'],'11111111-1111-4111-8111-111111111111')
    assert.equal(calls[0].opts.headers['X-CSRFToken'],'test')
  } finally {restore()}
})
test('navigation autonome : dix récents, historique complet, page de logs, détails fermés', () => {
  const projects = readFileSync(new URL('../src/pages/LiveProjects.jsx',import.meta.url),'utf8')
  const runs = readFileSync(new URL('../src/pages/LiveRuns.jsx',import.meta.url),'utf8')
  const app = readFileSync(new URL('../src/App.jsx',import.meta.url),'utf8')
  assert.match(projects,/slice\(0, 10\)/)
  assert.match(projects,/detail\.runCount/)
  assert.match(projects,/<details className="ak-workspace-sheet ak-project-config-fold">/)
  assert.match(projects,/deploymentsApi\.preview\(/)
  assert.match(projects,/deploymentsApi\.launch\(/)
  assert.doesNotMatch(projects,/window\.confirm\(.*[Pp]ublier/)
  assert.match(runs,/const PAGE_SIZE = 20/)
  assert.match(runs,/deploymentsApi\.detail\(/)
  assert.match(runs,/deploymentsApi\.logs\(/)
  assert.match(app,/LiveRuns/)
  assert.match(app,/history\|deployments/)
})
