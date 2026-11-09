import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {detectSource} from '../src/services/sourceAnalysis.js'
import {companionSuggestion, detectedServiceKind, sourceIsSubdirectory} from '../src/services/serviceComposition.js'
import {existingServiceKind, existingFrontendStackDefaults} from '../src/services/stackEnv.js'

const react = detectSource([{type:'file',name:'package.json'}, {type:'file',name:'package-lock.json'}, {type:'file',name:'vite.config.ts'}], {dependencies:{react:'^19'},devDependencies:{vite:'^7'},scripts:{build:'vite build'}})
const flask = detectSource([{type:'file',name:'app.py'}, {type:'file',name:'requirements.txt'}])
const page = path => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('le dépôt racine ne suggère pas de deuxième service lors de la création', () => {
  for (const analysis of [react, flask]) {
    assert.equal(companionSuggestion({analysis,status:'ready',sourcePath:'./',isEditing:false}), null)
    assert.equal(companionSuggestion({analysis,status:'ready',sourcePath:'',isEditing:false}), null)
  }
})

test('une source détectée dans un sous-répertoire suggère seulement le service opposé', () => {
  assert.equal(detectedServiceKind(react), 'frontend')
  assert.equal(detectedServiceKind(flask), 'backend')
  assert.equal(companionSuggestion({analysis:react,status:'ready',sourcePath:'./mon-app',isEditing:false}), 'backend')
  assert.equal(companionSuggestion({analysis:flask,status:'ready',sourcePath:'api',isEditing:false}), 'frontend')
  assert.ok(sourceIsSubdirectory('frontend'))
  assert.equal(sourceIsSubdirectory('./'), false)
})

test('en édition le service manquant peut être proposé même si le premier service est à la racine', () => {
  assert.equal(companionSuggestion({analysis:react,status:'ready',sourcePath:'./',isEditing:true}), 'backend')
  assert.equal(companionSuggestion({analysis:flask,status:'ready',sourcePath:'./',isEditing:true}), 'frontend')
  assert.equal(companionSuggestion({analysis:flask,status:'loading',sourcePath:'api',isEditing:true}),null)
  assert.equal(companionSuggestion({analysis:null,status:'ready',sourcePath:'api',isEditing:true}),null)
})

test('conversion backend seul : le backend existant n’est pas recopié comme frontend', () => {
  const detail = {project:{default_branch:'main'},configurations:[{branch:'api',root_directory:'api',runtime_type:'PYTHON',settings:{frontend_profile:'python_flask'}}],variables:[{key:'FLASK_ENV',value:'production',secret_id:null},{key:'DATABASE_URL',value:'secret',secret_id:'id'}]}
  assert.equal(existingServiceKind(detail),'backend')
  assert.equal(existingFrontendStackDefaults(detail).profile,'python_flask')
  assert.deepEqual(existingFrontendStackDefaults(detail).variables,[{key:'FLASK_ENV',value:'production'}])
  const editor = page('pages/StackDeploy.jsx')
  assert.match(editor,/primaryKind==='frontend' \? singleService : null/)
  assert.match(editor,/primaryKind==='backend' \? singleService : null/)
})

test('le rendu monorepo importe toutes les icônes utilisées et ouvre les journaux à la fin', () => {
  assert.match(page('pages/StackDeploy.jsx'), /AlertTriangle, Layers3/)
  const runs=page('pages/LiveRuns.jsx')
  assert.match(runs,/viewport\.scrollTop = viewport\.scrollHeight/)
  assert.match(runs,/followLogTail\.current/)
  assert.match(runs,/el\.scrollHeight-el\.scrollTop-el\.clientHeight/)
})
