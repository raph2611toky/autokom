/** Starting footprint, not observed usage, a reservation, or a plan entitlement. */
const profiles = Object.freeze({
  html: [100, 128, 1, 'Fichiers servis directement'],
  vite: [200, 192, 1, 'Frontend compilé'],
  npm_static: [200, 192, 1, 'Frontend compilé'],
  next_export: [200, 192, 1, 'Export Next.js'],
  next_server: [500, 512, 2, 'Rendu Next.js côté serveur'],
  node_express: [350, 384, 2, 'Service Node.js'],
  node_nest: [500, 512, 2, 'Service NestJS'],
  python_fastapi: [350, 384, 2, 'Service FastAPI'],
  python_flask: [350, 384, 2, 'Service Flask'],
  python_wsgi: [350, 384, 2, 'Service Python WSGI'],
  python_django: [500, 512, 2, 'Service Django'],
  php_plain: [250, 256, 1, 'Service PHP'],
  php_laravel: [500, 512, 2, 'Service Laravel'],
  php_symfony: [500, 512, 2, 'Service Symfony'],
  java_spring_maven: [750, 1024, 3, 'Service Spring Boot'],
  java_spring_gradle: [750, 1024, 3, 'Service Spring Boot'],
})

export function estimateResources(components, { highAvailability = false } = {}) {
  if (!Array.isArray(components) || !components.length) return null
  const replicas = highAvailability ? 2 : 1
  const services = []
  for (const item of components) {
    if (!item?.analysis?.available || !Object.hasOwn(profiles, item.profile)) return null
    const [cpu, ram, disk, basis] = profiles[item.profile]
    const depCount = item.analysis.productionDependencyCount || 0
    const extra = item.profile === 'next_server' || item.profile.startsWith('node_')
      ? depCount >= 30 ? [150, 256, 1] : depCount >= 12 ? [100, 128, 0] : [0, 0, 0]
      : [0, 0, 0]
    services.push({ label: item.label, basis, cpu: cpu + extra[0], ram: ram + extra[1], disk: disk + extra[2] })
  }
  const total = services.reduce((sum, item) => ({
    cpu: sum.cpu + item.cpu * replicas,
    ram: sum.ram + item.ram * replicas,
    disk: sum.disk + item.disk * replicas,
  }), { cpu: 0, ram: 0, disk: 0 })
  return { services, total, replicas }
}
