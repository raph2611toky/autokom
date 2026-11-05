import { authApi } from './authApi.js'

// Secret temporaire du navigateur : ni JWT ni access token, supprimé après le callback.
export const GITHUB_BROWSER_BINDING_KEY = 'autokom.github.browser_binding'
export const GITHUB_FINISH_SETUP_KEY = 'autokom.github.complete-repos-after-signup'

export function validatedGitHubAuthorizationUrl(raw) {
  const url = new URL(raw)
  if (url.protocol !== 'https:' || url.hostname !== 'github.com' || url.pathname !== '/login/oauth/authorize') {
    throw new Error('URL OAuth GitHub inattendue.')
  }
  return url.href
}

/** Navigateur courant uniquement : ne jamais envoyer browser_binding à GitHub. */
export async function startGitHubAccountAuth(intent, {
  start = intent === 'link' ? authApi.githubLinkStart : authApi.githubStart,
  storage = globalThis.sessionStorage,
  redirect = href => globalThis.location.assign(href),
} = {}) {
  if (!['login', 'signup', 'link'].includes(intent)) throw new TypeError('Intention GitHub invalide.')
  if (!storage) throw new Error('Stockage de session nécessaire à la liaison sécurisée GitHub.')
  const result = await start(intent)
  const href = validatedGitHubAuthorizationUrl(result?.authorization_url)
  if (typeof result?.browser_binding !== 'string' || result.browser_binding.length < 40) {
    throw new Error('Session de connexion GitHub incomplète.')
  }
  storage.setItem(GITHUB_BROWSER_BINDING_KEY, result.browser_binding)
  if (intent === 'signup') storage.setItem(GITHUB_FINISH_SETUP_KEY, '1')
  redirect(href)
}

export function consumeGitHubAccountCallback(hash, storage) {
  const params = new URLSearchParams(String(hash || '').replace(/^#/, ''))
  const status = params.get('status') || 'error'
  const ticket = params.get('ticket') || ''
  const browserBinding = storage?.getItem(GITHUB_BROWSER_BINDING_KEY) || ''
  storage?.removeItem(GITHUB_BROWSER_BINDING_KEY)
  if (!['ok', 'created', 'linked', 'denied', 'error', 'account_exists', 'already_linked', 'not_registered'].includes(status)) {
    return { status: 'error', ticket: '', browserBinding: '' }
  }
  if ((status === 'ok' || status === 'created') && (!ticket || ticket.length > 256 || browserBinding.length < 40)) {
    return { status: 'invalid', ticket: '', browserBinding: '' }
  }
  return { status, ticket: status === 'ok' || status === 'created' ? ticket : '', browserBinding }
}
