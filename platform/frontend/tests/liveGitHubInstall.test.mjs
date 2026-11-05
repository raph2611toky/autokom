import test from 'node:test'
import assert from 'node:assert/strict'
import { selectAuthorizedInstallation, verifiedInstallationUrl } from '../src/services/githubInstallFlow.js'
import { detectSource } from '../src/services/sourceAnalysis.js'
import { toBackendConfiguration } from '../src/services/projectWorkflow.js'

const plan = { workdir: 'portfolio-app', branch: 'main', pipelineMode: 'devops', autoDeploy: true,
  detectedKind: 'static', detectedVite: true, detectedFramework: 'React', hasDockerfile: false, advancedBuild: {} }

test('installation GitHub unique présélectionnée, ID externe non fiable rejeté', () => {
  const installs = [{ id: 33, account: 'alice' }, { id: 44, account: 'bob' }]
  assert.equal(selectAuthorizedInstallation([{ id: 33 }]), '33')
  assert.equal(selectAuthorizedInstallation(installs, ['33']), '44')
  assert.equal(selectAuthorizedInstallation(installs, [], '44'), '44')
  assert.equal(selectAuthorizedInstallation(installs, [], '999'), '')
  assert.throws(() => verifiedInstallationUrl('https://github.com.attacker.com/apps/test/installations/new'))
  assert.throws(() => verifiedInstallationUrl('https://github.com/login/oauth/authorize'))
  assert.match(verifiedInstallationUrl('https://github.com/apps/autokom-deploy/installations/new'), /^https:\/\/github\.com/)
})

test('React et Vue sous Vite = sources, dist est produit au build', () => {
  for (const name of ['react', 'vue']) {
    const result = detectSource([
      {name:'package.json',type:'file'},{name:'package-lock.json',type:'file'},
      {name:'vite.config.js',type:'file'},{name:'src',type:'dir'}],
    {dependencies:{[name]:'*'},devDependencies:{vite:'*'},scripts:{build:'configured'}})
    assert.equal(result.isVite,true)
    assert.equal(result.applicationType,'static')
  }
  const cfg = toBackendConfiguration(plan)
  assert.equal(cfg.build_method,'STATIC')
  assert.equal(cfg.runtime_type,'STATIC')
  assert.equal(cfg.output_directory,'dist')
  assert.equal(cfg.internal_port,8080)
  assert.equal(cfg.root_directory,'portfolio-app')
})

test('Next.js et backend sans Dockerfile ne sont jamais déclarés prêts sur le plan gratuit', () => {
  const next=detectSource([{name:'package.json',type:'file'}],{dependencies:{next:'*',react:'*'},scripts:{build:'configured',start:'configured'}})
  assert.equal(next.framework,'Next.js')
  assert.equal(next.applicationType,'service')
  assert.throws(()=>toBackendConfiguration({...plan,detectedKind:'service',detectedFramework:'Next.js'}),/Dockerfile|offre backend/)
})

test('inscription GitHub prépare la continuation vers la liaison des dépôts', async () => {
  const { startGitHubAccountAuth, GITHUB_FINISH_SETUP_KEY, GITHUB_BROWSER_BINDING_KEY } = await import('../src/services/githubAuthFlow.js')
  const data = new Map(); let target = ''
  await startGitHubAccountAuth('signup', {
    start: async () => ({authorization_url:'https://github.com/login/oauth/authorize?client_id=test',browser_binding:'a'.repeat(48)}),
    storage: {setItem:(k,v)=>data.set(k,v),getItem:k=>data.get(k),removeItem:k=>data.delete(k)},
    redirect:href => {target=href},
  })
  assert.equal(data.get(GITHUB_FINISH_SETUP_KEY), '1')
  assert.equal(data.get(GITHUB_BROWSER_BINDING_KEY), 'a'.repeat(48))
  assert.match(target, /^https:\/\/github\.com\/login\/oauth\/authorize/)
})
