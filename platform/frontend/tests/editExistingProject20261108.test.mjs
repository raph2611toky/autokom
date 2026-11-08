import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { remoteProjectToForm, saveRemoteProject, prepareRemoteDeployment } from '../src/services/projectWorkflow.js'
import { deploymentsApi } from '../src/services/deploymentsApi.js'

const projectId = '11111111-1111-4111-8111-111111111111'
const environmentId = '22222222-2222-4222-8222-222222222222'
const oldConfigId = '33333333-3333-4333-8333-333333333333'
const newConfigId = '44444444-4444-4444-8444-444444444444'
const deploymentId = '55555555-5555-4555-8555-555555555555'
const orgId = '66666666-6666-4666-8666-666666666666'
const source = {project: {
  id: projectId, organization:orgId, name:'nexttest', status:'ACTIVE',
  repository_name:'raph2611toky/nexttest', repository_url:'https://github.com/raph2611toky/nexttest',
  repository_external_id:'456', github_installation_id:987, default_branch:'main',
}, environment:{id:environmentId, slug:'production'}, configurations:[{
  id:oldConfigId, version:2, is_draft:false, branch:'main', root_directory:'',mode:'DEVOPS',
  settings:{frontend_profile:'next_server'},build_command:'npm run build',auto_deploy:false,
}], variables:[]}

const savedFetch=globalThis.fetch
const savedDocument=globalThis.document
function mockEndpoints(handler) {
  const calls=[]
  globalThis.document={cookie:'csrftoken=test'}
  globalThis.fetch=async(url,init)=> {
    calls.push({url,method:init.method,body:init.body ? JSON.parse(init.body) : undefined})
    const content=handler(init.method,url,init.body ? JSON.parse(init.body):undefined)
    return {ok:true,status:200,json:async()=>({success:true,data:content,errors:null})}
  }
  return calls
}
function reset() {globalThis.fetch=savedFetch;if (savedDocument===undefined)delete globalThis.document;else globalThis.document=savedDocument}

test('configuration existante : dépôt et installation GitHub restaurés à partir de Django',()=>{
  const form=remoteProjectToForm(source)
  assert.equal(form.backendProjectId,projectId)
  assert.equal(form.githubInstallationId,987)
  assert.equal(form.githubRepositoryId,'456')
  assert.equal(form.organizationId,orgId)
  assert.equal(form.advancedBuild.profile,'next_server')
  assert.equal(form.branch,'main')
})

test('éditer /frontend : crée une nouvelle version dans le même projet et démarre un vrai run',async()=>{
  const prefix=`/api/projects/${projectId}/environments/${environmentId}`
  let createdVersion=false
  const calls=mockEndpoints((method,url,payload)=>{
    if(method==='GET' && url===`/api/projects/${projectId}/environments/`)return {environments:[{id:environmentId,kind:'PRODUCTION'}]}
    if(method==='GET' && url===`${prefix}/configurations/offers/`)return {entitlement:{resource_plan:orgId}}
    if(method==='GET' && url===`${prefix}/configurations/`)return {configurations:[{id:oldConfigId,is_draft:false,version:2}]}
    if(method==='POST' && url===`${prefix}/configurations/create/`){
      assert.equal(payload.root_directory,'frontend')
      assert.equal(payload.settings.frontend_profile,'next_server')
      createdVersion=true
      return {configuration:{id:newConfigId,version:3,is_draft:true}}
    }
    if(method==='POST' && url===`${prefix}/configurations/${newConfigId}/finalize/`)return {configuration:{id:newConfigId,is_draft:false}}
    if(method==='GET' && url===`${prefix}/deployments/preview/`)return {ready:true}
    if(method==='POST' && url===`${prefix}/deployments/launch/`)return {deployment:{id:deploymentId}}
    throw new Error(`unexpected ${method} ${url}`)
  })
  try{
    const edited={...remoteProjectToForm(source),workdir:'frontend', detectedKind:'node',detectedFramework:'Next.js',hasNpmLockfile:true}
    const saved=await saveRemoteProject(edited)
    assert.equal(saved.projectId,projectId)
    assert.equal(saved.configurationId,newConfigId)
    assert.equal(createdVersion,true)
    await prepareRemoteDeployment(saved)
    const launched=await deploymentsApi.launch(projectId,environmentId,'edition-non-repetee')
    assert.equal(launched.deployment.id,deploymentId)
    assert.equal(calls.some(c=>c.url==='/api/projects/create/'),false)
    assert.equal(calls.filter(c=>c.url.endsWith('/deployments/launch/')).length,1)
  }finally{reset()}
})

test('routage et UI : éditeur lié à un ID, sélection GitHub verrouillée, bouton enregistrer',()=>{
  const app=readFileSync(new URL('../src/App.jsx',import.meta.url),'utf8')
  const deploy=readFileSync(new URL('../src/pages/Deploy.jsx',import.meta.url),'utf8')
  const projects=readFileSync(new URL('../src/pages/LiveProjects.jsx',import.meta.url),'utf8')
  const settings=readFileSync(new URL('../src/pages/LiveProjectSettings.jsx',import.meta.url),'utf8')
  const projectSerializer=readFileSync(new URL('../../backend/apps/projects/project/serializers.py',import.meta.url),'utf8')
  assert.match(app,/configure\$\/\.test\(route\)/)
  assert.match(app,/editProjectId=\{projectFromPath\(route\)\}/)
  assert.match(projects,/navigate\(`\/projects\/\$\{p.id\}\/configure`\)/)
  assert.match(deploy,/Dépôt GitHub du projet \(non modifiable\)/)
  assert.match(deploy,/editingProjectId \? 'Enregistrer' : 'Déployer'/)
  assert.match(deploy,/navigate\(editingProjectId \? `\/projects\/\$\{ids.projectId\}\/deployments\//)
  assert.match(settings,/Voir les déploiements/)
  assert.match(projectSerializer,/github_installation_id/)
})
