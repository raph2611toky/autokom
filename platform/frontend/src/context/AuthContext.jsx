import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import { API_AVAILABLE } from '../config/runtime.js'
import { authApi } from '../services/authApi.js'

/** État React uniquement : aucun token ni secret n'est stocké dans le contexte. */
const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [session, setSession] = useState({ status: API_AVAILABLE ? 'loading' : 'demo', user: null })

  useEffect(() => {
    if (!API_AVAILABLE) return
    let live = true
    authApi.me().then(result => {
      if (live) setSession({ status: 'authenticated', user: result?.user ?? null })
    }).catch(() => {
      if (live) setSession({ status: 'anonymous', user: null })
    })
    return () => { live = false }
  }, [])

  const actions = useMemo(() => ({
    ...session,
    login: async credentials => {
      if (!API_AVAILABLE) return null
      const result = await authApi.login(credentials)
      setSession({ status: 'authenticated', user: result.user })
      return result
    },
    logout: async () => {
      if (!API_AVAILABLE) return
      await authApi.logout()
      setSession({ status: 'anonymous', user: null })
    },
    refreshUser: async () => {
      if (!API_AVAILABLE) return null
      const result = await authApi.me()
      setSession({ status: 'authenticated', user: result.user })
      return result.user
    },
  }), [session])
  return <AuthContext.Provider value={actions}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const value = useContext(AuthContext)
  if (!value) throw new Error('AuthProvider manquant.')
  return value
}
