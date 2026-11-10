/** AUTOKOM controls this DNS zone; only the one-label prefix is editable. */
export const AUTOKOM_DOMAIN_SUFFIX = 'auto.ikom.mg'
const PREFIX = /^(?=.{1,63}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/

export function managedDomainPrefix(hostname, suffix = AUTOKOM_DOMAIN_SUFFIX) {
  const value = String(hostname || '').trim().toLowerCase().replace(/\.$/, '')
  const tail = '.' + suffix
  if (!value.endsWith(tail)) return ''
  const prefix = value.slice(0, -tail.length)
  return PREFIX.test(prefix) ? prefix : ''
}

export function managedDomainHost(prefix, suffix = AUTOKOM_DOMAIN_SUFFIX) {
  const value = String(prefix || '').trim().toLowerCase()
  if (!PREFIX.test(value) || suffix !== AUTOKOM_DOMAIN_SUFFIX)
    throw new Error('Saisissez uniquement un préfixe DNS valide ; auto.ikom.mg est fixe.')
  if (value === 'backend')
    throw new Error('Ce préfixe est réservé au service IKOM.')
  return value + '.' + suffix
}
