import { useEffect, useRef, useState } from 'react'
import { Check, Copy } from 'lucide-react'
import '../styles/copy-feedback.css'

/** A green check means the clipboard write promise has resolved successfully. */
export default function CopyButton({ text, className = 'button-outline', label = 'Copier' }) {
  const [state, setState] = useState('idle')
  const resetTimer = useRef(null)
  useEffect(() => () => window.clearTimeout(resetTimer.current), [])
  async function copy() {
    window.clearTimeout(resetTimer.current)
    try {
      if (!text || !navigator.clipboard?.writeText) throw new Error('clipboard_unavailable')
      await navigator.clipboard.writeText(text)
      setState('success')
    } catch {
      setState('error')
    }
    resetTimer.current = window.setTimeout(() => setState('idle'), 3000)
  }
  return <button className={`${className} ak-copy-action ak-copy-${state}`} type="button" onClick={copy}
    aria-label={state === 'success' ? 'Copié dans le presse-papiers' : state === 'error' ? 'Copie impossible' : label}
    title={state === 'error' ? 'Presse-papiers indisponible : vérifiez les autorisations du navigateur' : ''}>
    {state === 'success' ? <Check size={15} aria-hidden="true" /> : <Copy size={15} aria-hidden="true" />}
    <span aria-live="polite">{state === 'success' ? 'Copié !' : state === 'error' ? 'Échec de copie' : label}</span>
  </button>
}
