/** Parse a small .env file into named non-multiline fields; never log values. */
export const sensitiveEnvName = key => /(?:PASSWORD|SECRET|TOKEN|PRIVATE_KEY|API_KEY|DATABASE_URL|REDIS_URL)/i.test(key)
export const publicEnvName = key => /^(?:VITE_|REACT_APP_|VUE_APP_|NEXT_PUBLIC_)/.test(key) && !sensitiveEnvName(key)
export function parseStackEnv(text, kind) {
  if (typeof text !== 'string' || text.length > 32768) throw new Error('Fichier .env trop volumineux (32 Ko maximum).')
  const map = new Map()
  for (const raw of text.replace(/\r\n/g, '\n').split('\n')) {
    const line = raw.trim()
    if (!line || line.startsWith('#')) continue
    const clean = line.startsWith('export ') ? line.slice(7).trim() : line
    const index = clean.indexOf('=')
    if (index < 1) throw new Error('Ligne .env non reconnue : format CLE=valeur obligatoire.')
    const key = clean.slice(0, index).trim()
    let value = clean.slice(index + 1).trim()
    if (!/^[A-Za-z_][A-Za-z0-9_]{0,159}$/.test(key) || key.startsWith('AUTOKOM_') || key.startsWith('DOCKER_')) throw new Error('Une clé .env est interdite ou mal formée.')
    if (value.startsWith('"') && value.endsWith('"') && value.length >= 2 || value.startsWith("'") && value.endsWith("'") && value.length >= 2) value = value.slice(1, -1)
    if (value.includes('\0') || value.includes('\r')) throw new Error('Valeur de variable invalide.')
    if (kind === 'frontend' && !publicEnvName(key)) throw new Error(`La variable ${key} ne peut pas être exposée au navigateur.`)
    map.set(key, { key, value, secret:kind === 'backend' && sensitiveEnvName(key) })
    if (map.size > 50) throw new Error('Trop de variables (maximum 50).')
  }
  return [...map.values()]
}
export function stackEnvPayload(rows, kind) {
  const variables = {}, secrets = {}
  for (const item of rows || []) {
    if (!item?.key || typeof item.value !== 'string') continue
    if (kind === 'frontend' && !publicEnvName(item.key)) throw new Error('Variable frontend non publique.')
    const target = kind === 'backend' && item.secret ? secrets : variables
    if (kind === 'backend' && !item.secret && sensitiveEnvName(item.key)) throw new Error('Une variable backend sensible doit être chiffrée.')
    target[item.key] = item.value
  }
  return {variables,secrets}
}

/** Initial values for converting an existing single-service frontend into a two-service project.
 * The GitHub repository is immutable. Never copy private values into browser state. */
export function existingFrontendStackDefaults(detail) {
  const cfg = detail?.configurations?.find(item => !item.is_draft) || detail?.configurations?.[0] || {}
  return {
    branch: cfg.branch || detail?.project?.default_branch || 'main',
    root_directory: cfg.root_directory || '',
    profile: cfg.settings?.frontend_profile || '',
    output_directory: cfg.output_directory || 'build',
    healthcheck: cfg.healthcheck_path || '/',
    variables: (detail?.variables || []).filter(row => !row.secret_id && publicEnvName(row.key))
      .map(row => ({ key:row.key, value:row.value || '' })),
  }
}
