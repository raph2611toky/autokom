import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { detectSource, frontendProfile } from '../src/services/sourceAnalysis.js'
import { parseStackEnv, stackEnvPayload } from '../src/services/stackEnv.js'
const here = path.dirname(fileURLToPath(import.meta.url))
const read = p => fs.readFileSync(path.join(here,'..','src',p),'utf8')

test('deux branches peuvent être configurées séparément sans dupliquer le dépôt', () => {
  const page=read('pages/StackDeploy.jsx')
  assert.match(page, /frontend\.branch/)
  assert.match(page, /backend\.branch/)
  assert.match(page, /root_directory/)
  assert.match(page, /gitApi\.directories/)
  assert.match(page, /stackApi\.configure/)
  assert.match(page, /stackApi\.launch/)
  assert.doesNotMatch(page, /projectsApi\.create\(/)
})

test('React Vite et Flask sont détectés sur des dossiers indépendants', () => {
  const react=detectSource([{type:'file',name:'package.json'},{type:'file',name:'package-lock.json'}, {type:'file',name:'vite.config.ts'}],{dependencies:{react:'^19.0.0'},devDependencies:{vite:'^7.0.0'},scripts:{build:'vite build'}})
  const flask=detectSource([{type:'file',name:'app.py'}, {type:'file',name:'requirements.txt'}])
  assert.equal(frontendProfile(react),'vite')
  assert.equal(frontendProfile(flask),'python_flask')
})

test('fichier env frontend uniquement public, env backend sépare secrets', () => {
  const frontend=parseStackEnv('VITE_API_URL=/__autokom_backend\nVITE_APP_NAME=Demo','frontend')
  const backend=parseStackEnv('DATABASE_URL=mysql://example\nAPP_MODE=production','backend')
  assert.equal(frontend.length,2)
  assert.deepEqual(stackEnvPayload(backend,'backend'),{variables:{APP_MODE:'production'},secrets:{DATABASE_URL:'mysql://example'}})
  assert.throws(()=>parseStackEnv('VITE_SECRET_TOKEN=x','frontend'))
  assert.throws(()=>parseStackEnv('DATABASE_URL=secret','frontend'))
})

test('interface indique les deux URL et la route interne avec retour de progression',()=>{
  const page=read('pages/StackDeploy.jsx')
  assert.match(page,/stackHosts\(frontend.hostname, backend.hostname, defaultHost\)/)
  assert.match(page,/__autokom_backend/)
  assert.match(page,/BACKEND_INTERNAL_URL/)
  assert.match(page,/deployment\.status/)
  const css=read('styles/stack-deploy.css')
  assert.match(css,/ak-stack/)
})

test('le bouton de publication historique conserve la publication simple ou redirige vers le mode groupé',()=>{
  const project=read('pages/LiveProjects.jsx')
  assert.match(project,/stackEnabled/)
  assert.match(project,/if \(stackEnabled\)/)
  assert.match(project,/deploymentsApi\.launch/)
})
