import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {existingFrontendStackDefaults} from '../src/services/stackEnv.js'

const source = path => readFileSync(new URL(`../src/${path}`,import.meta.url),'utf8')

test('l’ajout du service manquant est contextuel, sans bouton global et sans création implicite', () => {
  const edit = source('pages/Deploy.jsx')
  assert.match(edit, /companionSuggestion\(/)
  assert.match(edit, /Ajouter un \{companionKind\}/)
  assert.match(edit, /navigate\(`\/projects\/\$\{editingProjectId\}\/stack`\)/)
  assert.match(edit, /const saved = await save\(\)/)
  assert.match(edit, /stackApi\.detail\(editProjectId\)/)
  const stack = source('pages/StackDeploy.jsx')
  assert.match(stack, /stackApi\.configure\(projectId,payload\)/)
  assert.doesNotMatch(stack, /projectsApi\.create\(/)
  assert.doesNotMatch(source('pages/LiveProjects.jsx'), />Frontend \+ Backend<\/button>/)
  assert.doesNotMatch(source('pages/LiveProjectSettings.jsx'), />Frontend \+ Backend<\/button>/)
})

test('la configuration du frontend existant est reprise depuis Django et ne copie aucun secret', () => {
  const detail={project:{default_branch:'main'},configurations:[
    {is_draft:true,branch:'temp',root_directory:'do-not-use'},
    {is_draft:false,branch:'frontend',root_directory:'frontend',output_directory:'dist',healthcheck_path:'/health',settings:{frontend_profile:'vite'}}
  ],variables:[{key:'VITE_API_URL',value:'https://api.example.com',secret_id:null},
    {key:'DATABASE_URL',value:'mysql://private',secret_id:'secret-id'},
    {key:'OTHER',value:'not-public',secret_id:null}]}
  assert.deepEqual(existingFrontendStackDefaults(detail),{
    branch:'frontend',root_directory:'frontend',profile:'vite',output_directory:'dist',healthcheck:'/health',
    variables:[{key:'VITE_API_URL',value:'https://api.example.com'}]
  })
  assert.equal(existingFrontendStackDefaults({project:{default_branch:'develop'},configurations:[],variables:[]}).branch,'develop')
})

test('une seule offre d’hébergement commune est affichée après les configurations frontend et backend', () => {
  const stack = source('pages/StackDeploy.jsx')
  assert.match(stack, /Offre d’hébergement commune/)
  assert.match(stack, /ak-stack-grid.*ComponentCard kind="frontend"/s)
  assert.match(stack, /ComponentCard kind="backend"/)
  assert.match(stack, /ak-stack-plan-list/)
  assert.match(stack, /existingFrontendStackDefaults\(detail\)/)
})

test('Technique défile dans son conteneur, Synthèse garde son affichage existant', () => {
  const runs=source('pages/LiveRuns.jsx')
  const css=source('styles/sites.css')
  assert.match(runs,/tabIndex=\{logMode==='technical'\?0:undefined\}/)
  assert.match(runs,/ak-log-console-line/)
  assert.match(css,/\.ak-run-detail-sheet \.ak-log-console\.ak-log-file\s*\{/)
  assert.match(css,/overflow-y:\s*auto/)
  assert.match(css,/block-size:\s*min\(/)
})
