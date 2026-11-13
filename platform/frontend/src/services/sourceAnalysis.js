/** Read-only hints from GitHub listing. The isolated runner revalidates the source. */
export const SERVER_PROFILES = Object.freeze({
  'FastAPI': 'python_fastapi',
  'Flask': 'python_flask',
  'Django': 'python_django',
  'Python WSGI': 'python_wsgi',
  'Express': 'node_express',
  'NestJS': 'node_nest',
  'Spring Boot (Maven)': 'java_spring_maven',
  'Spring Boot (Gradle)': 'java_spring_gradle',
  'PHP': 'php_plain',
  'Laravel': 'php_laravel',
  'Symfony': 'php_symfony',
})
export const BACKEND_PROFILES = Object.freeze(Object.values(SERVER_PROFILES))

export function detectSource(files, packageJson = null) {
  const names = new Set((files || []).filter(f => f.type === 'file').map(f => String(f.name).toLowerCase()))
  const directories = new Set((files || []).filter(f => f.type === 'dir').map(f => String(f.name).toLowerCase()))
  const scripts = packageJson?.scripts || {}
  const deps = { ...packageJson?.dependencies, ...packageJson?.devDependencies }
  const has = name => names.has(name)
  let framework = 'Non déterminé', runtime = '', applicationType = 'application', build = {}, resources = { cpuLimit: '0.5', ramLimitMb: '512', storageGb: '2' }, confidence = 'medium'
  if (has('package.json')) {
    runtime = 'Node.js'
    framework = deps.next ? 'Next.js' : deps['@nestjs/core'] ? 'NestJS' : deps.express ? 'Express' : deps.nuxt ? 'Nuxt' : deps['@angular/core'] ? 'Angular' : deps.vue ? 'Vue' : deps.react ? 'React' : deps.svelte ? 'Svelte' : 'Node.js'
    const viteCandidate = Boolean(has('vite.config.js') || has('vite.config.ts') || deps.vite)
    const serverApp = Boolean(deps.next || deps.nuxt || deps['@nestjs/core'] || deps.express)
    applicationType = (viteCandidate && !serverApp) || (scripts.build && !scripts.start && !serverApp) ? 'static' : 'service'
    build = { installCommand: has('pnpm-lock.yaml') ? 'pnpm install --frozen-lockfile' : has('yarn.lock') ? 'yarn install --frozen-lockfile' : 'npm ci', buildCommand: scripts.build ? 'npm run build' : '', testCommand: scripts.test ? 'npm test' : '', outputDir: deps.next || deps.nuxt ? '' : viteCandidate ? 'dist' : '', startCommand: scripts.start ? 'npm run start' : '', appPort: serverApp ? '8080' : '' }
    confidence = deps.react || deps.vue || deps.next || deps.nuxt || deps.svelte || deps.express || deps['@nestjs/core'] ? 'high' : 'medium'
  } else if (has('manage.py')) {
    runtime = 'Python'; framework = 'Django'; applicationType = 'service'
    build = { installCommand: 'pip install -r requirements.txt', startCommand: 'gunicorn projet.wsgi:application', testCommand: 'python manage.py test' }
    confidence = 'high'
  } else if (has('requirements.txt')) {
    runtime = 'Python'; framework = has('main.py') ? 'FastAPI' : has('app.py') ? 'Flask' : has('wsgi.py') ? 'Python WSGI' : 'Python'
    applicationType = 'service'; confidence = 'medium'
    build = { installCommand: 'pip install -r requirements.txt', startCommand: framework === 'FastAPI' ? 'uvicorn main:app' : 'gunicorn app:app' }
  } else if (has('artisan') && has('composer.json')) {
    runtime = 'PHP'; framework = 'Laravel'; applicationType = 'service'; confidence = 'high'; build = { installCommand: 'composer install', appPort: '8080' }
  } else if (has('composer.json') && (has('symfony.lock') || directories.has('bin') || directories.has('config'))) {
    runtime = 'PHP'; framework = 'Symfony'; applicationType = 'service'; confidence = 'medium'; build = { installCommand: 'composer install', appPort: '8080' }
  } else if (has('index.php')) {
    runtime = 'PHP'; framework = 'PHP'; applicationType = 'service'; confidence = 'high'
  } else if (has('pom.xml')) {
    runtime = 'Java'; framework = 'Spring Boot (Maven)'; applicationType = 'service'; confidence = 'medium'; resources.ramLimitMb = '1024'; build = { buildCommand: 'mvn package', appPort: '8080' }
  } else if (has('build.gradle') || has('build.gradle.kts')) {
    runtime = 'Java'; framework = 'Spring Boot (Gradle)'; applicationType = 'service'; confidence = 'medium'; resources.ramLimitMb = '1024'; build = { buildCommand: 'gradle bootJar', appPort: '8080' }
  } else if (has('index.html')) {
    runtime = 'Statique'; framework = 'HTML / CSS / JavaScript'; applicationType = 'static'; build = { outputDir: './' }; confidence = 'high'
  } else if (has('go.mod')) {
    runtime = 'Go'; framework = 'Non pris en charge'; confidence = 'low'
  } else if (has('dockerfile')) {
    runtime = 'Docker'; framework = 'Non pris en charge'; confidence = 'low'
  } else { confidence = 'low' }
  if (has('dockerfile')) build.dockerfile = 'Dockerfile'
  if (has('package.json') && scripts.build) {
    build.buildCommand = 'npm run build'
    if (framework === 'React' && !Boolean(has('vite.config.js') || has('vite.config.ts') || deps.vite)) build.outputDir = 'build'
    if (framework === 'Vue' && !Boolean(has('vite.config.js') || has('vite.config.ts') || deps.vite)) build.outputDir = 'dist'
    if (framework === 'Next.js') build.outputDir = '.next'
  }
  const isVite = Boolean(has('package.json') && (has('vite.config.js') || has('vite.config.ts') || deps.vite))
  const warnings = confidence === 'low' ? ['Structure non reconnue : seuls les profils approuvés sont acceptés.'] : []
  return { framework, runtime, applicationType, hasNpmLockfile: has('package-lock.json'), hasDockerfile: has('dockerfile'),
    hasRequirements: has('requirements.txt'), hasComposerLock: has('composer.lock'), isVite, confidence, build, resources,
    productionDependencyCount: Object.keys(packageJson?.dependencies || {}).length,
    available: confidence !== 'low', explanation: 'Détection indicative. Le runner vérifie les fichiers, dépendances et points d’entrée.', warnings, applications: [] }
}

/** Profile names are allowlisted all the way to BuildKit. */
export function frontendProfile(analysis) {
  if (!analysis) return ''
  if (SERVER_PROFILES[analysis.framework]) return SERVER_PROFILES[analysis.framework]
  if (analysis.framework === 'HTML / CSS / JavaScript') return 'html'
  if (analysis.framework === 'Next.js') return 'next_server'
  if (analysis.isVite && analysis.applicationType === 'static' && ['React', 'Vue', 'Svelte'].includes(analysis.framework)) return 'vite'
  if (['React', 'Vue'].includes(analysis.framework) && analysis.build?.buildCommand) return 'npm_static'
  return ''
}
export function availableFrontendProfiles(analysis) {
  const standard = frontendProfile(analysis)
  if (!standard) return []
  if (['python_fastapi', 'python_flask', 'python_wsgi'].includes(standard)) return [standard, ...['python_fastapi', 'python_flask', 'python_wsgi'].filter(p => p !== standard)]
  return standard === 'next_server' ? ['next_server', 'next_export'] : [standard]
}
export function supportedFrontend(analysis, choice) {
  const supported = availableFrontendProfiles(analysis)
  const profile = choice || supported[0]
  if (!supported.includes(profile) || analysis?.confidence === 'low') return false
  if (profile === 'html' || profile === 'php_plain') return true
  if (profile.startsWith('python_')) return Boolean(analysis?.hasRequirements)
  if (profile.startsWith('php_')) return Boolean(analysis?.hasComposerLock)
  if (profile.startsWith('java_')) return true
  if (profile === 'node_express') return Boolean(analysis?.hasNpmLockfile)
  return Boolean(analysis?.hasNpmLockfile && analysis?.build?.buildCommand)
}
export function recommendFrontendCommands(analysis, files = [], preferred = '') {
  if (!analysis) return null
  const baseProfile = frontendProfile(analysis)
  const profile = preferred || baseProfile
  const valid = availableFrontendProfiles(analysis).includes(profile)
  if (BACKEND_PROFILES.includes(profile)) {
    const starts = {
      python_fastapi: 'uvicorn main:app --host 0.0.0.0 --port 8080',
      python_flask: 'gunicorn --bind 0.0.0.0:8080 app:app',
      python_django: 'gunicorn projet.wsgi:application', python_wsgi: 'gunicorn app:app',
      node_express: 'npm run start', node_nest: 'npm run start:prod',
      java_spring_maven: 'java -jar app.jar', java_spring_gradle: 'java -jar app.jar',
      php_plain: 'Nginx + PHP-FPM', php_laravel: 'Nginx + PHP-FPM', php_symfony: 'Nginx + PHP-FPM',
    }
    const install = profile.startsWith('python_') ? 'pip install -r requirements.txt' : profile.startsWith('node_') ? 'npm ci' :
      profile.startsWith('php_') ? (profile === 'php_plain' ? 'PHP-FPM' : 'composer install') : 'Build Maven / Gradle'
    return { profile, framework: analysis.framework, install, build: profile === 'node_nest' ? 'npm run build' :
      profile.startsWith('java_') ? analysis.build?.buildCommand || 'Packaging Java' : 'Recette préconfigurée',
      output: 'Image applicative', start: starts[profile], supported: valid && supportedFrontend(analysis, profile),
      description: `Application ${analysis.framework} : processus privé sur le port 8080, derrière HTTPS AUTOKOM. Prérequis vérifiés pendant le build.` }
  }
  const output = profile === 'html' ? './' : profile === 'npm_static' ? (analysis.framework === 'Vue' ? 'dist' : 'build') :
    profile === 'next_export' ? 'out' : profile === 'next_server' ? '.next' : 'dist'
  const start = profile === 'next_server' ? 'npm run start' : 'Nginx statique'
  const description = profile === 'next_server' ? 'Next.js avec rendu serveur (SSR), routage et API Node.' :
    profile === 'next_export' ? 'Next.js export statique : nécessite output: "export" dans next.config.' :
    profile === 'html' ? 'Site HTML/CSS/JS sans compilation.' : `${analysis.framework} · compilation statique approuvée.`
  return { profile, framework: analysis.framework, install: profile === 'html' ? 'Non nécessaire' : 'npm ci',
    build: profile === 'html' ? 'Sans compilation' : 'npm run build', output, start,
    supported: valid && supportedFrontend(analysis, profile),
    description: valid ? description : 'Recette non compatible avec cette source ; sélectionner un profil adapté.' }
}
