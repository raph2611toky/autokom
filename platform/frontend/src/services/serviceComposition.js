import { BACKEND_PROFILES, frontendProfile } from './sourceAnalysis.js'

/** Presentation-only classification. Django and the runner validate the actual recipes. */
export function detectedServiceKind(analysis, selectedProfile = '') {
  const profile = selectedProfile || frontendProfile(analysis)
  if (BACKEND_PROFILES.includes(profile)) return 'backend'
  if (['vite', 'npm_static', 'html', 'next_server', 'next_export'].includes(profile)) return 'frontend'
  return null
}

export function sourceIsSubdirectory(path = '') {
  const clean = String(path).trim().replace(/^\.\//, '').replace(/\/$/, '')
  return Boolean(clean && clean !== '.')
}

/** Only suggest the missing service, and only when a real supported source was detected. */
export function companionSuggestion({ analysis, selectedProfile = '', sourcePath = '', isEditing = false, status = '' } = {}) {
  if (status !== 'ready' || analysis?.confidence === 'low') return null
  if (!isEditing && !sourceIsSubdirectory(sourcePath)) return null
  const kind = detectedServiceKind(analysis, selectedProfile)
  return kind === 'frontend' ? 'backend' : kind === 'backend' ? 'frontend' : null
}
