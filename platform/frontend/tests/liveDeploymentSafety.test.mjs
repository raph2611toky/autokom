import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { detectSource } from '../src/services/sourceAnalysis.js'
import { verifiedDeploymentForm, toBackendConfiguration } from '../src/services/projectWorkflow.js'

const file = name => ({ name, type: 'file' })
const base = { projectName: 'portfolio', branch: 'main', workdir: 'portfolio-app',
  pricingPlan: 'free', pipelineMode: 'devops', autoDeploy: false, advancedBuild: {} }

// Le serveur GitHub renvoie les noms et des métadonnées allowlistées, pas le code.
test('Vite avec package.json et lockfile => STATIC sans Dockerfile client', () => {
  const source = detectSource([file('package.json'), file('package-lock.json'), file('index.html')], {
    dependencies: { react: '*'}, devDependencies: { vite: '*' }, scripts: { build: 'configured' }
  })
  assert.equal(source.applicationType, 'static')
  assert.equal(source.framework, 'React')
  const form = verifiedDeploymentForm(base, source)
  const config = toBackendConfiguration(form)
  assert.equal(config.build_method, 'STATIC')
  assert.equal(config.runtime_type, 'STATIC')
  assert.equal(config.output_directory, 'dist')
  assert.equal(Object.hasOwn(config, 'internal_port'), false)
  assert.equal(config.root_directory, 'portfolio-app')
})

test('ne crée jamais un brouillon DOCKERFILE gratuit par détection absente', () => {
  assert.throws(() => verifiedDeploymentForm(base, null), /Analyse du dossier GitHub incomplète/)
  const serverSource = detectSource([file('package.json'), file('package-lock.json')], {
    dependencies: { next: '*' }, scripts: { build: 'configured', start: 'configured' }
  })
  assert.throws(() => verifiedDeploymentForm(base, serverSource), /nécessitent une offre adaptée/)
})

test('Vite gratuit sans lockfile est refusé avant Django', () => {
  const source = detectSource([file('package.json'), file('vite.config.js')], {
    dependencies: { vue: '*' }, devDependencies: { vite: '*' }, scripts: { build: 'configured' }
  })
  assert.throws(() => verifiedDeploymentForm(base, source), /package-lock.json absent/)
})

test('publication en un clic, sans confirmation doublonnée ni détails internes', () => {
  const deploy = readFileSync(new URL('../src/pages/Deploy.jsx', import.meta.url), 'utf8')
  const github = readFileSync(new URL('../src/components/LiveGitHubConnector.jsx', import.meta.url), 'utf8')
  const projects = readFileSync(new URL('../src/pages/LiveProjects.jsx', import.meta.url), 'utf8')
  assert.match(deploy, /setTimeout\(\(\) => setNotice\(null\), 5000\)/)
  assert.match(deploy, /'Déployer'/)
  assert.doesNotMatch(deploy, /window\.confirm\('Lancer réellement le pipeline Jenkins/)
  assert.match(deploy, /deploymentsApi\.launch\(ids\.projectId/)
  assert.doesNotMatch(deploy, /Mode connecté : les projets/)
  assert.match(github, /Chargement du dossier GitHub/)
  assert.match(github, /<ExternalLink size=\{15\}/)
  const runDetails = readFileSync(new URL('../src/pages/LiveRuns.jsx', import.meta.url), 'utf8')
  assert.match(projects, /Déployer maintenant/)
  assert.match(projects, /Publications récentes/)
  assert.match(runDetails, /run\.stages/)
  assert.match(runDetails, /deploymentsApi\.logs/)
})
