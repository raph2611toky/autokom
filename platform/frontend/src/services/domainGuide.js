/** Guide offline generated from server-authoritative DNS settings; never hard-code the ingress IP. */
const validIPv4 = value => {
  if (typeof value !== 'string') return false
  const pieces=value.split('.')
  return pieces.length===4 && pieces.every(part => /^\d{1,3}$/.test(part) && Number(part)<=255 && (part==='0' || !part.startsWith('0')))
}
const validHost = value => typeof value==='string' && value.length<=253 &&
  /^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$/.test(value)
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]))
export function dnsGuideHTML(domain, setup) {
  const host=String(domain?.hostname || '').trim().toLowerCase()
  const kind=domain?.domain_type || domain?.type
  const ip=setup?.ingress_ipv4
  if (!validHost(host) || !validIPv4(ip) || !['CUSTOM','SUBDOMAIN'].includes(kind)) {
    throw new Error('Informations DNS officielles indisponibles. Actualiser les domaines avant de télécharger le guide.')
  }
  const custom=kind==='CUSTOM'
  const verify=domain?.verification
  const challenge=verify?.type==='TXT' && verify?.hostname && verify?.value ? verify : null
  const managedSuffix=setup?.managed_suffix || 'domaine AUTOKOM'
  const dnsZone=setup?.dns_zone || 'zone DNS configurée'
  const instructions=custom ? `
    <h2>1. Enregistrement A (obligatoire)</h2>
    <table><thead><tr><th>Type</th><th>Nom complet</th><th>Valeur</th><th>Mode</th></tr></thead>
    <tbody><tr><td>A</td><td>${esc(host)}</td><td><code>${esc(ip)}</code></td><td>DNS uniquement (sans proxy)</td></tr></tbody></table>
    <h2>2. Preuve de propriété TXT</h2>
    ${challenge ? `<table><thead><tr><th>Type</th><th>Nom complet</th><th>Valeur</th></tr></thead><tbody><tr><td>TXT</td><td>${esc(challenge.hostname)}</td><td><code>${esc(challenge.value)}</code></td></tr></tbody></table>` : `<p class="attention">Ajoutez d'abord le domaine dans AUTOKOM : la valeur TXT unique sera affichée dans la fiche du domaine, puis vous pourrez télécharger le guide complet.</p>`}
    <p>Dans votre hébergeur DNS, le champ « Nom » peut être relatif à la zone : utilisez @ pour l'apex, ou le sous-domaine approprié. Évitez de saisir deux fois le suffixe de votre zone.</p>
    <p>Ne créez pas un CNAME au même nom que le A. AUTOKOM vérifie actuellement une adresse A IPv4 publique, et le proxy Cloudflare doit rester désactivé pour ce contrôle.</p>` : `
    <h2>1. Domaine AUTOKOM géré automatiquement</h2>
    <p>Zone Cloudflare : <strong>${esc(dnsZone)}</strong> · Suffixe géré : <strong>${esc(managedSuffix)}</strong>.</p>
    <table><thead><tr><th>Type</th><th>Nom complet</th><th>Cible</th></tr></thead><tbody><tr><td>A</td><td>${esc(host)}</td><td><code>${esc(ip)}</code></td></tr></tbody></table>
    <p>AUTOKOM crée et vérifie cet enregistrement lui-même. Aucune configuration DNS manuelle n'est demandée pour cette zone gérée.</p>`
  return `<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Guide DNS AUTOKOM — ${esc(host)}</title>
<style>body{font:15px/1.65 system-ui,-apple-system,sans-serif;max-width:850px;margin:46px auto;padding:0 25px;color:#182538}h1{font-size:30px;margin-bottom:5px}h2{font-size:19px;margin-top:32px}small,.muted{color:#576377}.tag{display:inline-block;padding:5px 10px;background:#fff0e4;color:#954500}table{width:100%;border-collapse:collapse;margin:14px 0}th,td{border:1px solid #d9dde5;text-align:left;padding:10px;overflow-wrap:anywhere}th{background:#f5f6f8}code{font-size:13px;overflow-wrap:anywhere}.attention{background:#fff7e8;padding:12px;border-left:3px solid #f1a24a}.footer{margin-top:40px;border-top:1px solid #ddd;padding-top:12px;color:#64748b;font-size:12px}@media print{body{margin:0}}</style></head><body>
<div class="tag">AUTOKOM · Guide de configuration DNS</div><h1>${esc(host)}</h1><p class="muted">Document généré à partir des paramètres ingress publics fournis par le serveur AUTOKOM. Ne contient aucun jeton ni clé secrète.</p>
${instructions}
<h2>${custom?'3.':'2.'} Activation et validation HTTPS</h2><ol><li>Ajoutez les enregistrements DNS dans votre fournisseur${custom?' et attendez la propagation.':'.'}</li><li>Depuis « Sites & domaines » dans AUTOKOM, lancez la vérification si nécessaire.</li><li>AUTOKOM vérifie le DNS, installe le reverse proxy et obtient automatiquement le certificat TLS.</li><li>Le bouton « Ouvrir » devient disponible seulement après validation HTTPS.</li></ol>
<p class="attention">La propagation DNS peut prendre du temps. Ne publiez pas de secrets dans les enregistrements A/TXT. Le port HTTPS 443 et la validation ACME doivent être joignables sur le serveur.</p><p class="footer">IP publique ingress au moment de la génération : ${esc(ip)}. Si l'adresse du serveur change, téléchargez un nouveau guide depuis AUTOKOM.</p></body></html>`
}
export function downloadDnsGuide(domain, setup) {
  const html=dnsGuideHTML(domain,setup)
  const file=new Blob([html],{type:'text/html;charset=utf-8'})
  const url=URL.createObjectURL(file)
  const link=document.createElement('a')
  link.href=url
  link.download=`Guide-DNS-AUTOKOM-${domain.hostname}.html`
  document.body.appendChild(link)
  link.click()
  link.remove()
  // Revoke on a later event turn so browsers can finish starting the download.
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
