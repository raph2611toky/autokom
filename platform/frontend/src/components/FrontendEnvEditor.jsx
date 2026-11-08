import { useRef, useState } from 'react'
import { FileUp, Plus, Trash2, AlertTriangle } from 'lucide-react'
import { normalizeFrontendEnv, parseFrontendEnv } from '../services/frontendEnv.js'

export default function FrontendEnvEditor({ rows = [], onChange, analysis }) {
  const fileRef = useRef(null)
  const [error, setError] = useState('')
  const [feedback, setFeedback] = useState('')
  const valid = Array.isArray(rows) ? rows : []
  function change(next) { onChange(next); setError(''); setFeedback('') }
  async function importFile(event) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    if (file.size > 65536) { setError('Fichier trop volumineux (maximum 64 Ko).'); return }
    try {
      const parsed = parseFrontendEnv(await file.text())
      const merged = new Map(valid.map(row => [row.key, row]))
      for (const row of parsed) merged.set(row.key, row)
      change(normalizeFrontendEnv([...merged.values()]))
      setFeedback(`${parsed.length} variable(s) analysée(s), séparées en champs modifiables.`)
    } catch (err) { setError(err.message || 'Fichier .env invalide.') }
  }
  return <section className="ak-env-editor" aria-label="Variables frontend publiques">
    <div className="ak-env-head"><div><h3>Variables du frontend (.env)</h3><p>Ajoutez chaque champ ou importez un fichier .env : ses clés et valeurs seront séparées automatiquement.</p></div><div className="ak-env-actions"><input ref={fileRef} type="file" accept=".env,.txt,text/plain" aria-label="Importer .env" hidden onChange={importFile}/><button type="button" className="button-outline" onClick={() => fileRef.current?.click()}><FileUp size={15}/> Importer .env</button><button type="button" className="button-outline" onClick={() => change([...valid, {key:'', value:''}])} disabled={valid.length >= 40}><Plus size={15}/> Ajouter</button></div></div>
    <p className="ak-env-warning"><AlertTriangle size={16}/> Ces variables sont injectées au moment du build et visibles dans le JavaScript public. <strong>Ne saisissez aucun token, mot de passe ou clé secrète.</strong> Le déploiement automatique actuel prend en charge les variables <code>VITE_*</code> des sites Vite.</p>
    {valid.map((row, index) => <div className="ak-env-row" key={index}>
      <label className="field"><span>Nom</span><input aria-label={`Nom variable ${index+1}`} placeholder="VITE_API_URL" value={row.key} autoComplete="off" spellCheck={false} onChange={e => change(valid.map((item,i)=>i===index?{...item,key:e.target.value}:item))}/></label>
      <label className="field"><span>Valeur publique</span><input aria-label={`Valeur variable ${index+1}`} placeholder="https://api.example.com" value={row.value} autoComplete="off" spellCheck={false} onChange={e => change(valid.map((item,i)=>i===index?{...item,value:e.target.value}:item))}/></label>
      <button type="button" className="ak-env-remove" aria-label={`Supprimer variable ${index+1}`} onClick={() => change(valid.filter((_,i)=>i!==index))}><Trash2 size={16}/></button>
    </div>)}
    {!valid.length && <p className="ak-env-empty">Aucune variable frontend. Le déploiement utilise sa configuration actuelle.</p>}
    {feedback && <p className="feedback success" role="status">{feedback}</p>}{error && <p className="feedback error" role="alert">{error}</p>}
    {valid.length > 0 && !analysis?.isVite && <p className="ak-env-note">Ce framework est détecté, mais l’injection de variables publiques est disponible pour les builds Vite statiques uniquement.</p>}
  </section>
}
