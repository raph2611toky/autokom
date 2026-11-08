import { useRef, useState } from 'react'
import { FileUp, Plus, Trash2, Info } from 'lucide-react'
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
    <div className="ak-env-head"><div className="ak-env-heading"><h3>Variables du frontend (.env)</h3><details className="ak-env-help"><summary title="Informations sur les variables publiques" aria-label="En savoir plus sur les variables frontend"><Info size={17}/></summary><p>Ajoutez les variables une à une ou importez un fichier .env. Elles sont insérées dans le JavaScript public pendant la compilation : n'ajoutez jamais de mots de passe, jetons ou autres secrets. Préfixes : VITE_, REACT_APP_, VUE_APP_ ou NEXT_PUBLIC_ selon le framework.</p></details></div><div className="ak-env-actions"><input ref={fileRef} type="file" accept=".env,.txt,text/plain" aria-label="Importer .env" hidden onChange={importFile}/><button type="button" className="button-outline" onClick={() => fileRef.current?.click()}><FileUp size={15}/> Importer .env</button><button type="button" className="button-outline" onClick={() => change([...valid, {key:'', value:''}])} disabled={valid.length >= 40}><Plus size={15}/> Ajouter</button></div></div>
    {valid.map((row, index) => <div className="ak-env-row" key={index}>
      <label className="field"><span>Nom</span><input aria-label={`Nom variable ${index+1}`} placeholder="VITE_API_URL" value={row.key} autoComplete="off" spellCheck={false} onChange={e => change(valid.map((item,i)=>i===index?{...item,key:e.target.value}:item))}/></label>
      <label className="field"><span>Valeur publique</span><input aria-label={`Valeur variable ${index+1}`} placeholder="https://api.example.com" value={row.value} autoComplete="off" spellCheck={false} onChange={e => change(valid.map((item,i)=>i===index?{...item,value:e.target.value}:item))}/></label>
      <button type="button" className="ak-env-remove" aria-label={`Supprimer variable ${index+1}`} onClick={() => change(valid.filter((_,i)=>i!==index))}><Trash2 size={16}/></button>
    </div>)}
    {!valid.length && <p className="ak-env-empty">Aucune variable ajoutée.</p>}
    {feedback && <p className="feedback success" role="status">{feedback}</p>}{error && <p className="feedback error" role="alert">{error}</p>}

  </section>
}
