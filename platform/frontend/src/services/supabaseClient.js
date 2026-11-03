import { createClient } from '@supabase/supabase-js'

// Seules l'URL et la clé publique Supabase sont autorisées dans le frontend.
const url = (import.meta.env.VITE_SUPABASE_URL || '').trim()
const publicKey = (import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY || '').trim()
export const supabaseReady = /^https:\/\/[^/]+\.supabase\.co$/.test(url) && publicKey.length > 10
export const supabase = supabaseReady
  ? createClient(url, publicKey, { auth: { flowType: 'pkce', autoRefreshToken: true, detectSessionInUrl: true, persistSession: true } })
  : null

export function assertAuth() {
  if (!supabase) throw new Error('La connexion doit être activée dans la configuration AUTOKOM.')
  return supabase
}

export async function githubSession() {
  if (!supabase) return { connected: false, token: '', user: null }
  const { data, error } = await supabase.auth.getSession()
  if (error) throw error
  const session = data.session
  // Ne jamais écrire le jeton GitHub dans les brouillons, logs ou exports.
  return { connected: Boolean(session?.provider_token), token: session?.provider_token || '', user: session?.user || null }
}

export async function connectGitHub() {
  const client = assertAuth()
  const { data: { user }, error: identityError } = await client.auth.getUser()
  if (identityError && user) throw identityError
  const options = { scopes: 'repo read:user user:email', redirectTo: `${window.location.origin}/deploy` }
  // Si le compte AUTOKOM est déjà connecté par e-mail, lier l'identité GitHub.
  const alreadyLinked = user?.identities?.some(identity => identity.provider === 'github')
  const result = user && !alreadyLinked ? await client.auth.linkIdentity({ provider: 'github', options })
                      : await client.auth.signInWithOAuth({ provider: 'github', options })
  if (result.error) throw result.error
  return result.data
}

export async function signInEmail(email, password) {
  const client = assertAuth()
  const { data, error } = await client.auth.signInWithPassword({ email, password })
  if (error) throw error
  return data
}

export async function signUpEmail({ name, email, password }) {
  const client = assertAuth()
  const { data, error } = await client.auth.signUp({ email, password, options: { data: { full_name: name }, emailRedirectTo: `${window.location.origin}/login` } })
  if (error) throw error
  return data
}

export async function signOut() {
  if (!supabase) return
  const { error } = await supabase.auth.signOut()
  if (error) throw error
}
