/** Jeux de données fictifs utilisés UNIQUEMENT pour tester le parcours frontend.
 * Aucun appel réseau, authentification, dépôt ou déploiement réel. */
export const SAMPLE_REPOSITORIES = Object.freeze([
  { id: '1001', name: 'ikom-exemple/site-vitrine', private: false, defaultBranch: 'main', branches: ['main', 'develop'], summary: 'Site React et Vite' },
  { id: '1002', name: 'ikom-exemple/espace-client', private: true, defaultBranch: 'main', branches: ['main', 'staging', 'feature/dashboard'], summary: 'Monorepo frontend et API' },
  { id: '1003', name: 'ikom-exemple/api-django', private: true, defaultBranch: 'main', branches: ['main', 'develop'], summary: 'Service Django REST' },
  { id: '1004', name: 'ikom-exemple/page-statique', private: false, defaultBranch: 'main', branches: ['main'], summary: 'Site statique HTML' },
])

const file = name => ({ name, type: 'file' })
const dir = (name, path) => ({ name, path, type: 'dir' })
const viteFiles = [file('package.json'), file('vite.config.js'), dir('src', 'src'), dir('public', 'public')]
const djangoFiles = [file('manage.py'), file('requirements.txt'), dir('config', 'config'), dir('apps', 'apps')]
const directories = {
  '1001': {
    './': viteFiles,
    'src': [file('App.jsx'), file('main.jsx'), dir('components', 'src/components')],
    'src/components': [file('Header.jsx'), file('Footer.jsx')],
    'public': [file('logo.svg')],
  },
  '1002': {
    './': [file('README.md'), file('docker-compose.yml'), dir('apps', 'apps')],
    'apps': [dir('web', 'apps/web'), dir('api', 'apps/api')],
    'apps/web': [file('package.json'), file('vite.config.ts'), dir('src', 'apps/web/src'), dir('public', 'apps/web/public')],
    'apps/web/src': [file('App.jsx'), file('main.jsx')],
    'apps/web/public': [file('favicon.svg')],
    'apps/api': [file('manage.py'), file('requirements.txt'), dir('config', 'apps/api/config')],
    'apps/api/config': [file('settings.py'), file('urls.py')],
  },
  '1003': { './': djangoFiles, 'config': [file('settings.py'), file('urls.py')], 'apps': [dir('users', 'apps/users')], 'apps/users': [file('models.py'), file('views.py')] },
  '1004': { './': [file('index.html'), file('style.css'), dir('assets', 'assets')], 'assets': [file('logo.svg'), file('hero.png')] },
}

export function getSampleRepository(id) {
  return SAMPLE_REPOSITORIES.find(repo => repo.id === String(id)) || null
}
export function getSampleDirectory(repoId, path = './') {
  const tree = directories[String(repoId)] || {}
  return [...(tree[path] || [])]
}
export function getSamplePackage(repoId, path) {
  if (String(repoId) === '1001' && path === './') return { scripts: { build: 'vite build', test: 'vitest' }, dependencies: { react: '^19.0.0' } }
  if (String(repoId) === '1002' && path === 'apps/web') return { scripts: { build: 'vite build', test: 'vitest' }, dependencies: { react: '^19.0.0' } }
  return null
}

export const PIPELINE_STAGE_NAMES = Object.freeze({
  devops: ['Préparation de la source', 'Installation et construction', 'Tests', 'Publication', 'Contrôle de santé'],
  devsecops: ['Préparation de la source', 'Installation et construction', 'Tests', 'Analyses de sécurité', 'Publication', 'Contrôle de santé'],
})
export const PREVIEW_KEY = 'autokom.ui.pipeline.preview.v13'
export function createPipelinePreview(configuration) {
  const mode = configuration.pipeline.mode === 'devops' ? 'devops' : 'devsecops'
  return {
    preview: true,
    id: configuration.project.id || null,
    environment: configuration.project.environment || 'production',
    domainMode: configuration.hosting.domain_mode || 'autokom',
    domain: configuration.hosting.domain || '',
    createdAt: configuration.createdAt || '',
    name: configuration.project.name,
    repository: configuration.source.repository,
    branch: configuration.source.branch,
    workdir: configuration.source.workdir,
    framework: configuration.source_detection?.framework || 'Non déterminé',
    mode,
    offer: configuration.hosting.offer,
    pricingPlan: configuration.hosting.pricing_plan || null,
    billingPeriod: configuration.hosting.billing_period || 'monthly',
    stages: [...PIPELINE_STAGE_NAMES[mode]],
  }
}
