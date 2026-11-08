import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { detectSource, frontendProfile, availableFrontendProfiles, supportedFrontend, recommendFrontendCommands } from '../src/services/sourceAnalysis.js'
import { toBackendConfiguration, verifiedDeploymentForm } from '../src/services/projectWorkflow.js'
import { publicEnvForFramework, parseFrontendEnv } from '../src/services/frontendEnv.js'
const files = names => names.map(name => ({name,type:'file'}))
const base = {branch:'main',workdir:'./',projectName:'portfolio',pricingPlan:'free',pipelineMode:'devops',autoDeploy:false,advancedBuild:{}}
const packageJson=(deps,scripts={build:'tool build',start:'next start'})=>({dependencies:deps,scripts})

test('HTML statique sans package.json est autorisé',()=>{
  const detection=detectSource(files(['index.html','style.css']))
  assert.equal(frontendProfile(detection),'html')
  assert.equal(supportedFrontend(detection,'html'),true)
  const cfg=toBackendConfiguration(verifiedDeploymentForm(base,detection))
  assert.deepEqual([cfg.build_method,cfg.output_directory,cfg.settings.frontend_profile],['STATIC','','html'])
})
test('React + Vite avec TypeScript produit dist',()=>{
  const analysis=detectSource(files(['package.json','package-lock.json','vite.config.ts','tsconfig.json']),packageJson({react:'19',vite:'7'}))
  const cfg=toBackendConfiguration(verifiedDeploymentForm(base,analysis))
  assert.equal(cfg.settings.frontend_profile,'vite')
  assert.equal(cfg.output_directory,'dist')
})
test('React sans Vite : script npm et dossier build modifiables sans shell',()=>{
  const analysis=detectSource(files(['package.json','package-lock.json','src']),packageJson({react:'19','react-scripts':'5'},{build:'react-scripts build'}))
  assert.equal(frontendProfile(analysis),'npm_static')
  const cfg=toBackendConfiguration(verifiedDeploymentForm({...base,advancedBuild:{buildCommand:'npm run build',outputDir:'build'}},analysis))
  assert.equal(cfg.build_method,'STATIC');assert.equal(cfg.output_directory,'build')
  assert.throws(()=>toBackendConfiguration({...base,frontendProfile:'npm_static',hasNpmLockfile:true,advancedBuild:{buildCommand:'npm run build; rm -rf /'}}),/Commande/)
})
test('Vue CLI sans Vite : sortie dist',()=>{
  const analysis=detectSource(files(['package.json','package-lock.json']),packageJson({vue:'3','@vue/cli-service':'5'}))
  const cfg=toBackendConfiguration(verifiedDeploymentForm(base,analysis))
  assert.equal(cfg.output_directory,'dist')
})
test('Next.js SSR vs export : serveur private NODE vs sortie out',()=>{
  const analysis=detectSource(files(['package.json','package-lock.json']),packageJson({next:'16',react:'19'}))
  assert.deepEqual(availableFrontendProfiles(analysis),['next_server','next_export'])
  const ssr=toBackendConfiguration(verifiedDeploymentForm(base,analysis))
  assert.equal(ssr.build_method,'AUTO');assert.equal(ssr.runtime_type,'NODE');assert.equal(ssr.settings.frontend_profile,'next_server')
  const staticCfg=toBackendConfiguration(verifiedDeploymentForm({...base,advancedBuild:{profile:'next_export'}},analysis))
  assert.equal(staticCfg.build_method,'STATIC');assert.equal(staticCfg.output_directory,'out')
})
test('Les variables publiques sont cloisonnées par framework',()=>{
  assert.deepEqual(publicEnvForFramework(parseFrontendEnv('NEXT_PUBLIC_URL=https://example.com'),{framework:'Next.js'}),[{key:'NEXT_PUBLIC_URL',value:'https://example.com'}])
  assert.throws(()=>publicEnvForFramework([{key:'NEXT_PUBLIC_TOKEN',value:'abc'}],{framework:'Next.js'}),/secret/i)
  assert.throws(()=>publicEnvForFramework([{key:'VITE_URL',value:'abc'}],{framework:'Next.js'}),/incompatible/)
})
test('Un projet existant peut modifier son dossier et relancer sa détection',()=>{
  const src=readFileSync(new URL('../src/pages/Deploy.jsx',import.meta.url),'utf8')
  assert.match(src,/aria-label="Dossier source"/)
  assert.match(src,/setAnalysisStatus\('loading'\)/)
  assert.match(src,/\[step > 0, sourceKey/)
  assert.match(src,/role="status" aria-live="polite"/)
  assert.match(src,/launchStage/)
})
test('Aide .env accessible depuis i sans avertissement envahissant',()=>{
  const src=readFileSync(new URL('../src/components/FrontendEnvEditor.jsx',import.meta.url),'utf8')
  assert.match(src,/ak-env-help/);assert.match(src,/summary title=/)
  assert.doesNotMatch(src,/ak-env-warning/)
})

test('Changement de dossier Next vers React choisit une recette compatible sans profil périmé',()=>{
  const analysis=detectSource(files(['package.json','package-lock.json','vite.config.js']),packageJson({react:'19',vite:'7'}))
  const checked=verifiedDeploymentForm({...base,advancedBuild:{profile:'next_server'}},analysis)
  assert.equal(checked.frontendProfile,'vite')
  assert.equal(toBackendConfiguration(checked).build_method,'STATIC')
})
