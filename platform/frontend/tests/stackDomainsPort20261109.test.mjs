import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { stackHosts, frontendApiVariable, withoutManagedFields } from '../src/services/stackDomains.js'
const here=path.dirname(fileURLToPath(import.meta.url))
const read=p=>fs.readFileSync(path.join(here,'../src',p),'utf8')

test('URL publiques calculées avant .env, backend personnalisable et hostname distinct',()=>{
  assert.deepEqual(stackHosts('', '', 'portfolio.auto.ikom.mg'),{
    frontend:'portfolio.auto.ikom.mg',backend:'backend.portfolio.auto.ikom.mg',valid:true})
  assert.deepEqual(stackHosts('site.example.com','api.example.com','old.auto.ikom.mg'),{
    frontend:'site.example.com',backend:'api.example.com',valid:true})
  assert.equal(stackHosts('site.example.com','site.example.com','').valid,false)
  assert.equal(stackHosts('a..b','api.example.com','').valid,false)
})
test('public frontend API build-time and backend reserved vars are not editable',()=>{
  assert.deepEqual(frontendApiVariable('next_server','api.example.com'),{
    key:'NEXT_PUBLIC_API_URL',value:'https://api.example.com/api'})
  assert.deepEqual(frontendApiVariable('vite','api.example.com'),{
    key:'VITE_API_URL',value:'https://api.example.com/api'})
  const imported = [{key:'PORT',value:'4000'},{key:'FRONTEND_ORIGIN',value:'http://localhost:3000'},
    {key:'DB_PATH',value:'./data/app.db'},{key:'NODE_ENV',value:'development'}]
  assert.deepEqual(withoutManagedFields(imported,'backend'),[{key:'DB_PATH',value:'./data/app.db'}])
  assert.deepEqual(withoutManagedFields([{key:'NEXT_PUBLIC_API_URL',value:'http://localhost:4000/api'},
    {key:'NEXT_PUBLIC_SITE_NAME',value:'App'}],'frontend'),[{key:'NEXT_PUBLIC_SITE_NAME',value:'App'}])
})
test('domain editing, port 8000 and DNS check run before stack launch',()=>{
  const view=read('pages/StackDeploy.jsx')
  const api=read('services/stackApi.js')
  assert.match(view,/Domaine HTTPS/)
  assert.match(view,/PORT=8000/)
  assert.match(view,/stackApi\.preflightDomains\(projectId\)/)
  assert.ok(view.indexOf('stackApi.preflightDomains(projectId)') < view.indexOf('stackApi.launch(projectId,key)'))
  assert.match(view,/sans compte|domaine externe|Domaine externe/i)
  assert.match(api,/stack\/domains\/preflight\//)
})

test('DNS target is read-only from backend settings and validation is server-enforced',()=>{
  const api=read('services/stackApi.js')
  const page=read('pages/StackDeploy.jsx')
  assert.match(api,/domainSettings: projectId => api\.get/)
  assert.match(page,/DNS A attendu/)
  assert.match(page,/ingressIPv4/)
  assert.match(page,/stackApi\.preflightDomains\(projectId\)/)
})
