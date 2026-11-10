import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {toBackendConfiguration,remoteProjectToForm} from '../src/services/projectWorkflow.js'

const form = {
  branch:'main',workdir:'./',projectName:'demo',pipelineMode:'devops',autoDeploy:true,
  detectedFramework:'React',detectedVite:true,hasNpmLockfile:true,
  frontendProfile:'vite',advancedBuild:{profile:'vite'},
}

test('HA demandée : persistée dans la configuration Vite, sans paramètre de replicas client',()=>{
  const cfg=toBackendConfiguration({...form,highAvailability:true})
  assert.equal(cfg.high_availability_requested,true)
  assert.equal(cfg.build_method,'STATIC')
  assert.equal(Object.hasOwn(cfg,'replicas'),false)
  assert.equal(Object.hasOwn(cfg,'internal_port'),false)
})

test('choix standard : FALSE explicite, pas de HA implicite',()=>{
  const cfg=toBackendConfiguration({...form,highAvailability:false})
  assert.equal(cfg.high_availability_requested,false)
})

test('Dockerfile personnalisé : demande HA conservée sans passer par settings libres',()=>{
  const cfg=toBackendConfiguration({branch:'main',workdir:'./',pipelineMode:'devops',
    highAvailability:true,advancedBuild:{}})
  assert.equal(cfg.high_availability_requested,true)
  assert.equal(cfg.build_method,'DOCKERFILE')
  assert.equal(Object.hasOwn(cfg.settings||{},'high_availability_requested'),false)
})

test('édition : choix conservé depuis Django, et non supposé activé',()=>{
  const fromServer=remoteProjectToForm({project:{id:'p',organization:'o',name:'portfolio',repository_name:'x/portfolio',
    repository_url:'https://github.com/x/portfolio', repository_external_id:123, default_branch:'main'},
    environment:{id:'e',slug:'production'},
    configurations:[{branch:'main',high_availability_requested:true,settings:{frontend_profile:'vite'}}],variables:[]})
  assert.equal(fromServer.highAvailability,true)
  assert.equal(fromServer.pricingPlan,'pro')
})

test('le formulaire stack transmet au backend une demande explicite sans créer de répliques locales',()=>{
  const source=readFileSync(new URL('../src/pages/StackDeploy.jsx', import.meta.url),'utf8')
  assert.match(source,/high_availability_requested:highAvailability/)
  assert.match(source,/setHighAvailability\(Boolean\(existing\?\.high_availability_requested\)\)/)
})
