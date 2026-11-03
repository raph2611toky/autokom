import test from 'node:test'
import assert from 'node:assert/strict'
import { normalizeDirectoryPath, repoSlug } from '../src/services/github.js'
import { detectSource } from '../src/services/sourceAnalysis.js'

test('GitHub : refuser un dépôt non identifié', () => {
  assert.equal(repoSlug('ikom/frontend'), 'ikom/frontend')
  assert.throws(() => repoSlug('https://other.site/evil'))
})
test('GitHub : dossiers sûrs et sélection source', () => {
  assert.equal(normalizeDirectoryPath('./'), './')
  assert.equal(normalizeDirectoryPath('apps/web/'), 'apps/web')
  assert.equal(normalizeDirectoryPath('.github/workflows'), '.github/workflows')
  assert.throws(() => normalizeDirectoryPath('../../secret'))
})
test('détection React Vite avec build automatique', () => {
  const result = detectSource([{type:'file',name:'package.json'},{type:'file',name:'vite.config.ts'}],{scripts:{build:'vite build'},dependencies:{react:'^19.0.0'}})
  assert.equal(result.framework, 'React')
  assert.equal(result.build.outputDir, 'dist')
  assert.equal(result.build.buildCommand, 'npm run build')
})
test('détection Django sans saisir les commandes', () => {
  const result = detectSource([{type:'file',name:'manage.py'},{type:'file',name:'requirements.txt'}])
  assert.equal(result.framework, 'Django')
  assert.equal(result.build.testCommand, 'python manage.py test')
})

test('la validation frontend exige le dépôt, la branche et le dossier', async () => {
  const { validateProjectSelection } = await import('../src/config/deploymentProfiles.js')
  assert.match(validateProjectSelection({}), /GitHub/)
  const valid = { githubRepositoryId: '123', repositorySlug: 'ikom/site', branch: 'main', workdir: 'frontend', projectName: 'site' }
  assert.equal(validateProjectSelection(valid), '')
})
