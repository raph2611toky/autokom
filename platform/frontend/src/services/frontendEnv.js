/** Public frontend build-time variables. Never store imported contents in localStorage. */
const PUBLIC_PREFIX = /^(?:VITE_|NEXT_PUBLIC_|REACT_APP_|PUBLIC_)[A-Z][A-Z0-9_]*$/
const SENSITIVE = /(?:SECRET|PASSWORD|PRIVATE|TOKEN|CREDENTIAL|API_KEY|ACCESS_KEY|CLIENT_SECRET|SESSION|AUTH_KEY)/i
const MAX_ROWS = 40
const MAX_VALUE = 1024

export function validateFrontendEnvEntry(key, value) {
  const name = String(key || '').trim()
  if (!PUBLIC_PREFIX.test(name) || name.length > 100) throw new Error(`« ${name || 'Clé vide'} » : préfixe frontend public ou nom invalide (VITE_, NEXT_PUBLIC_, REACT_APP_, PUBLIC_).`)
  if (SENSITIVE.test(name)) throw new Error(`« ${name} » : un secret ne doit jamais être embarqué dans un frontend.`)
  if (typeof value !== 'string' || value.length > MAX_VALUE || /[\x00-\x1F\x7F]/.test(value)) throw new Error(`« ${name} » : valeur trop longue ou multiligne.`)
  return { key: name, value }
}

export function normalizeFrontendEnv(rows) {
  if (!Array.isArray(rows) || rows.length > MAX_ROWS) throw new Error(`Limite : ${MAX_ROWS} variables frontend.`)
  const seen = new Set()
  return rows.map(row => {
    const normalized = validateFrontendEnvEntry(row.key, row.value)
    if (seen.has(normalized.key)) throw new Error(`Variable en double : ${normalized.key}.`)
    seen.add(normalized.key)
    return normalized
  })
}

/** Parse .env sans exécuter de code. Refuse les expansions / multiline ambigus. */
export function parseFrontendEnv(content) {
  if (typeof content !== 'string' || content.length > 65536) throw new Error('Fichier .env trop volumineux (64 Ko maximum).')
  const rows = []
  const seen = new Set()
  for (const [index, raw] of content.replace(/^\uFEFF/, '').split(/\r?\n/).entries()) {
    const line = raw.trim()
    if (!line || line.startsWith('#')) continue
    const match = /^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/.exec(line)
    if (!match) throw new Error(`Ligne ${index + 1} : format attendu NOM=valeur.`)
    const key = match[1]
    let value = match[2].trim()
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1)
    } else if (value.startsWith('"') || value.startsWith("'")) {
      throw new Error(`Ligne ${index + 1} : guillemets non fermés.`)
    } else {
      value = value.replace(/\s+#.*$/, '').trim()
    }
    if (/\$\{|\$\(|`/.test(value)) throw new Error(`Ligne ${index + 1} : expansion de variables non autorisée dans cet import.`)
    const item = validateFrontendEnvEntry(key, value)
    if (seen.has(item.key)) throw new Error(`Ligne ${index + 1} : doublon ${item.key}.`)
    seen.add(item.key)
    rows.push(item)
    if (rows.length > MAX_ROWS) throw new Error(`Limite : ${MAX_ROWS} variables frontend.`)
  }
  return rows
}

export function publicEnvForFramework(rows, analysis) {
  const validated = normalizeFrontendEnv(rows)
  if (!validated.length) return []
  if (!analysis?.isVite || analysis?.applicationType !== 'static') {
    throw new Error('Les variables de build frontend sont actuellement applicables aux projets Vite statiques. Pour Next.js SSR, un runtime adapté est requis.')
  }
  for (const row of validated) {
    if (!row.key.startsWith('VITE_')) throw new Error(`${row.key} : le build Vite n’accepte ici que les clés VITE_.`)
  }
  return validated
}
