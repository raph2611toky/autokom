import { useRef, useState } from 'react'
import { RotateCcw } from 'lucide-react'
import { stackApi } from '../services/stackApi.js'
import { publicationPath } from '../services/stackHistory.js'
import { deploymentsApi } from '../services/deploymentsApi.js'
import { errorText } from '../services/projectWorkflow.js'

export default function RestorePublication({ projectId, environmentId, run, navigate, busy = false }) {
  const [confirm,setConfirm] = useState(false)
  const [pending,setPending] = useState(false)
  const [error,setError] = useState('')
  const key = useRef(null)
  const inFlight = useRef(false)
  if (run?.release_status !== 'SUPERSEDED' || run.status !== 'SUCCEEDED') return null
  async function restore() {
    if (inFlight.current) return
    inFlight.current = true
    setPending(true)
    setError('')
    try {
      key.current ||= globalThis.crypto?.randomUUID?.()
      if (!key.current) throw new Error('Un navigateur HTTPS récent est requis.')
      const response = await (run.kind === 'stack' ? stackApi.restore(projectId, run.id, key.current)
        : deploymentsApi.restore(projectId, environmentId, run.id, key.current))
      if (!response?.deployment?.id) throw new Error('La mise en file n’a pas été confirmée.')
      navigate(publicationPath(projectId,{id:response.deployment.id, kind:run.kind}))
    } catch (err) { setError(errorText(err)) }
    finally { inFlight.current = false; setPending(false) }
  }
  return <div>
    {!confirm ? <button className="button-outline" type="button" disabled={busy} onClick={() => setConfirm(true)}><RotateCcw size={16}/> Restaurer cette version</button>
      : <div role="group" aria-label="Confirmer la restauration"><p>{run.kind === 'stack' ? 'Réactiver les deux images de la publication' : 'Réactiver l’image de la publication'} {String(run.id).slice(0,8)}. Les variables actuelles restent utilisées et les données ne sont pas restaurées.</p>
        <button className="button-primary" type="button" disabled={pending || busy} onClick={restore}>{pending ? 'Demande en cours…' : 'Confirmer le rollback'}</button>
        <button className="button-outline" type="button" disabled={pending} onClick={() => setConfirm(false)}>Annuler</button></div>}
    {error && <p className="feedback error" role="alert">{error}</p>}
  </div>
}
