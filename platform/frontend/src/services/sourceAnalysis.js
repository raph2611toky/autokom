/** Analyse légère côté navigateur, exclusivement à partir des métadonnées du dépôt GitHub. */
export function detectSource(files, packageJson = null) {
  const names = new Set(files.filter(f => f.type === 'file').map(f => f.name.toLowerCase()))
  const scripts = packageJson?.scripts || {}
  const deps = { ...packageJson?.dependencies, ...packageJson?.devDependencies }
  const has = name => names.has(name)
  let framework = 'Non déterminé', runtime = '', applicationType = 'application', build = {}, resources = { cpuLimit: '0.5', ramLimitMb: '512', storageGb: '2' }, confidence = 'medium'
  if (has('package.json')) {
    runtime = 'Node.js'
    framework = deps.next ? 'Next.js' : deps.nuxt ? 'Nuxt' : deps['@nestjs/core'] ? 'NestJS' : deps['@angular/core'] ? 'Angular' : deps.vue ? 'Vue' : deps.react ? 'React' : deps.svelte ? 'Svelte' : deps.express ? 'Express / Node.js' : 'Node.js'
    // Vite peut être configuré directement dans package.json, sans vite.config.*.
    // Ne jamais classer Next/Nuxt/Nest/Express comme site statique par simple présence d'un script build.
    const viteCandidate = Boolean(has('vite.config.js') || has('vite.config.ts') || deps.vite)
    const serverApp = Boolean(deps.next || deps.nuxt || deps['@nestjs/core'] || deps.express)
    applicationType = (viteCandidate && !serverApp) || (scripts.build && !scripts.start && !serverApp) ? 'static' : 'service'
    build = { installCommand: has('pnpm-lock.yaml') ? 'pnpm install --frozen-lockfile' : has('yarn.lock') ? 'yarn install --frozen-lockfile' : 'npm ci', buildCommand: scripts.build ? 'npm run build' : '', testCommand: scripts.test ? 'npm test' : '', outputDir: deps.next || deps.nuxt ? '' : has('vite.config.js') || has('vite.config.ts') ? 'dist' : '', startCommand: scripts.start ? 'npm start' : '', appPort: deps.next ? '3000' : '' }
    confidence = deps.react || deps.vue || deps.next || deps.nuxt || deps.svelte ? 'high' : 'medium'
  } else if (has('manage.py') || has('requirements.txt') || has('pyproject.toml')) {
    runtime = 'Python'; framework = has('manage.py') ? 'Django' : (has('app.py') || has('wsgi.py') ? 'Python / Flask possible' : 'Python'); applicationType = 'service'
    build = { installCommand: has('requirements.txt') ? 'pip install -r requirements.txt' : '', buildCommand: '', testCommand: has('manage.py') ? 'python manage.py test' : '', outputDir: '', startCommand: '', appPort: '' }
    confidence = has('manage.py') ? 'high' : 'medium'
  } else if (has('artisan') || has('composer.json')) { runtime = 'PHP'; framework = has('artisan') ? 'Laravel' : 'PHP / Composer'; applicationType = 'service' }
  else if (has('pom.xml') || has('build.gradle') || has('build.gradle.kts')) {
    runtime = 'Java'; framework = 'Java / JVM'; applicationType = 'service'; build = { buildCommand: has('pom.xml') ? 'mvn package' : './gradlew build' }; resources.ramLimitMb = '1024'
  } else if (has('go.mod')) { runtime = 'Go'; framework = 'Go'; applicationType = 'service'; build = { buildCommand: 'go build ./...' } }
  else if (has('index.html')) { runtime = 'Statique'; framework = 'HTML / CSS / JavaScript'; applicationType = 'static'; build = { outputDir: './' }; confidence = 'high' }
  else if (has('dockerfile')) { runtime = 'Docker'; framework = 'Docker'; applicationType = 'container'; build = { dockerfile: 'Dockerfile' } }
  else { confidence = 'low' }
  if (has('dockerfile')) build.dockerfile = 'Dockerfile'
  return { framework, runtime, applicationType, hasNpmLockfile: has('package-lock.json'), hasDockerfile: has('dockerfile'),
    isVite: Boolean(has('package.json') && (has('vite.config.js') || has('vite.config.ts') || deps.vite)), confidence, build, resources, available: confidence !== 'low', explanation: 'Analyse indicative des fichiers GitHub. Les commandes exactes seront validées lors du déploiement.', warnings: confidence === 'low' ? ['Structure non reconnue : vérifiez les paramètres avancés.'] : [], applications: [] }
}
