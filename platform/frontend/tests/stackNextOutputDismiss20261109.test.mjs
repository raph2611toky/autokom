import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {stackOutputDirectory, existingFrontendStackDefaults} from '../src/services/stackEnv.js'

const read = file => readFileSync(new URL(`../src/${file}`, import.meta.url), 'utf8')

test('Next.js SSR: .next issu d’un ancien projet est normalisé en sortie interne non éditable', () => {
  const saved = existingFrontendStackDefaults({
    project:{default_branch:'main'}, configurations:[{
      is_draft:false,branch:'main',root_directory:'frontend',
      output_directory:'.next',settings:{frontend_profile:'next_server'},
    }], variables:[],
  })
  assert.equal(saved.output_directory,'.next')
  assert.equal(stackOutputDirectory(saved.profile,saved.output_directory),'')
  assert.equal(stackOutputDirectory('next_server','build'),'')
  assert.equal(stackOutputDirectory('next_server','dist'),'')
})

test('Les autres recettes utilisent une sortie propre, et seul npm_static peut choisir build/dist', () => {
  assert.equal(stackOutputDirectory('npm_static','dist'),'dist')
  assert.equal(stackOutputDirectory('npm_static','build'),'build')
  assert.equal(stackOutputDirectory('npm_static','.next'),'build')
  for (const profile of ['vite','next_export','html']) {
    assert.equal(stackOutputDirectory(profile,'.next'),'')
  }
  assert.equal(stackOutputDirectory('node_express','build','backend'),'')
})

test('La configuration normalize à chaque reprise, détection, changement de profil et publication', () => {
  const page=read('pages/StackDeploy.jsx')
  assert.match(page,/output_directory:stackOutputDirectory\(data\?\.profile,data\?\.output_directory,kind\)/)
  assert.match(page,/output_directory:stackOutputDirectory\(nextProfile,data\.output_directory,kind\)/)
  assert.match(page,/output_directory:stackOutputDirectory\(guessed,/)
  assert.match(page,/output_directory:stackOutputDirectory\(data\.profile,data\.output_directory,kind\)/)
  assert.match(page,/stackApi\.configure\(projectId,payload\)/)
})

test('Les notifications principale et .env ont une croix accessible et ne modifient pas les validations', () => {
  const page=read('pages/StackDeploy.jsx')
  const style=read('styles/stack-deploy.css')
  assert.match(page,/aria-label="Fermer la notification d’erreur" onClick=\{\(\)=>setError\(''\)\}/)
  assert.match(page,/aria-label="Fermer la notification d’information" onClick=\{\(\)=>setNotice\(''\)\}/)
  assert.match(page,/aria-label="Fermer l’erreur \.env" onClick=\{\(\)=>setError\(''\)\}/)
  assert.match(page,/\{error && <div className="feedback error ak-stack-dismissible" role="alert">/)
  assert.match(style,/\.ak-stack-dismiss:focus-visible/)
  assert.match(page,/const ready = readySource && hosts\.valid/)
})
