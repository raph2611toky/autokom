import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { detectSource, frontendProfile, recommendFrontendCommands, supportedFrontend } from '../src/services/sourceAnalysis.js'
import { toBackendConfiguration, verifiedDeploymentForm } from '../src/services/projectWorkflow.js'
const file = (...names) => names.map(name => ({name, type:'file'}))
function detect(names, pkg) { return detectSource(file(...names), pkg) }

test('Python Django, Flask, FastAPI and WSGI profiles', () => {
  assert.equal(frontendProfile(detect(['requirements.txt','manage.py'])), 'python_django')
  assert.equal(frontendProfile(detect(['requirements.txt','main.py'])), 'python_fastapi')
  assert.equal(frontendProfile(detect(['requirements.txt','app.py'])), 'python_flask')
  assert.equal(frontendProfile(detect(['requirements.txt','wsgi.py'])), 'python_wsgi')
})
test('Node Express and Nest profiles need package-lock', () => {
  const express = detect(['package.json','package-lock.json'],{dependencies:{express:'*'},scripts:{start:'configured'}})
  const nest = detect(['package.json','package-lock.json'],{dependencies:{'@nestjs/core':'*'},scripts:{build:'configured','start:prod':'configured'}})
  assert.equal(frontendProfile(express),'node_express')
  assert.equal(frontendProfile(nest),'node_nest')
  assert.equal(supportedFrontend(express),true)
  assert.equal(supportedFrontend(detect(['package.json'],{dependencies:{express:'*'},scripts:{start:'configured'}})),false)
})
test('Java Spring Boot Maven/Gradle and PHP variants detected', () => {
  assert.equal(frontendProfile(detect(['pom.xml'])),'java_spring_maven')
  assert.equal(frontendProfile(detect(['build.gradle.kts'])),'java_spring_gradle')
  assert.equal(frontendProfile(detect(['artisan','composer.json','composer.lock'])),'php_laravel')
  assert.equal(frontendProfile(detect(['index.php'])),'php_plain')
})
test('Unrecognized Go and Docker projects are not accepted', () => {
  assert.equal(supportedFrontend(detect(['go.mod'])),false)
  assert.equal(supportedFrontend(detect(['Dockerfile'])),false)
})
test('Backend config uses fixed AUTO recipe, correct runtime and no client shell', () => {
  const analysis = detect(['requirements.txt','manage.py'])
  const form = {advancedBuild:{},workdir:'backend',branch:'main',pipelineMode:'devops',hasNpmLockfile:false}
  const chosen = verifiedDeploymentForm(form,analysis)
  const cfg=toBackendConfiguration(chosen)
  assert.equal(cfg.build_method,'AUTO')
  assert.equal(cfg.runtime_type,'PYTHON')
  assert.equal(cfg.root_directory,'backend')
  assert.equal(cfg.start_command,'')
  assert.equal(cfg.settings.frontend_profile,'python_django')
})
test('Clipboard UI awaits actual write and confirms for 3s',()=>{
  const copy=fs.readFileSync(new URL('../src/components/CopyButton.jsx',import.meta.url),'utf8')
  assert.match(copy,/await navigator\.clipboard\.writeText\(text\)/)
  assert.match(copy,/setState\('success'\)/)
  assert.match(copy,/3000/)
  assert.match(copy,/clearTimeout/)
  assert.match(copy,/ak-copy-\$\{state\}/)
})
test('Source folder rejects missing paths and disables submission',()=>{
  const deploy=fs.readFileSync(new URL('../src/pages/Deploy.jsx',import.meta.url),'utf8')
  assert.match(deploy,/setSourceInvalid\(true\)/)
  assert.match(deploy,/setTimeout\(\(\) => setSourceInvalid\(false\), 3000\)/)
  assert.match(deploy,/flashInvalidSource/)
  assert.match(deploy,/aria-invalid=/)
  assert.match(deploy,/analysisStatus !== 'ready'/)
  assert.match(deploy,/step === 1 && !staticReady/)
})
