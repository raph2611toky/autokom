/** Public host previews; DNS/ownership remain authoritative on the backend. */
const HOST = /^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/
export function stackHostname(input, fallback = '') {
  return String(input || fallback).trim().toLowerCase().replace(/\.$/, '')
}
export function stackHosts(frontValue, backValue, existingFront) {
  const frontend = stackHostname(frontValue, existingFront)
  const backend = stackHostname(backValue, frontend ? `backend.${frontend}` : '')
  return { frontend, backend, valid: HOST.test(frontend) && HOST.test(backend) && frontend !== backend }
}
export function frontendApiVariable(profile, backendHost, selectedKey = '') {
  const prefix = profile?.startsWith('next_') ? 'NEXT_PUBLIC_' : profile === 'npm_static' ? 'REACT_APP_' : 'VITE_'
  return { key:selectedKey || `${prefix}API_URL`, value:`https://${backendHost}/api` }
}
// The backend validates the same contract; generated values are never client-authoritative.
const ENV_NAME = /^[A-Z][A-Z0-9_]{0,99}$/
export function validFrontendApiKey(profile, key) {
  const allowed = profile?.startsWith('next_') ? ['NEXT_PUBLIC_'] :
    profile === 'npm_static' ? ['REACT_APP_', 'VUE_APP_', 'VITE_'] : ['VITE_']
  return ENV_NAME.test(key) && allowed.some(prefix => key.startsWith(prefix) && key.length > prefix.length) &&
    !/(?:SECRET|PASSWORD|TOKEN|PRIVATE|CREDENTIAL|API_KEY|ACCESS_KEY|SESSION|AUTH_KEY)/.test(key) &&
    !key.startsWith('AUTOKOM_')
}
export function validBackendOriginKey(key) {
  return ENV_NAME.test(key) && !key.startsWith(('AUTOKOM_')) && !key.startsWith('DOCKER_') &&
    !['PORT', 'NODE_ENV', 'BACKEND_INTERNAL_URL'].includes(key) &&
    !/(?:PASSWORD|SECRET|TOKEN|PRIVATE_KEY|API_KEY|DATABASE_URL|REDIS_URL)/.test(key)
}
export const MANAGED_BACKEND_ENV = Object.freeze(['PORT', 'FRONTEND_ORIGIN', 'NODE_ENV', 'BACKEND_INTERNAL_URL'])
export const MANAGED_FRONTEND_API = Object.freeze(['VITE_API_URL','REACT_APP_API_URL','VUE_APP_API_URL','NEXT_PUBLIC_API_URL'])
export function withoutManagedFields(rows, kind) {
  const forbidden = kind === 'backend' ? MANAGED_BACKEND_ENV : MANAGED_FRONTEND_API
  return (rows || []).filter(row => !forbidden.includes(row.key))
}
