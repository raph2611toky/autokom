/** Démonstration explicite : aucun débit, aucun numéro de carte accepté. */
import { api } from './api.js'
const uuid = value => {
  if (typeof value !== 'string' || !/^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/i.test(value)) throw new TypeError('Identifiant invalide.')
  return value
}
export const billingApi = Object.freeze({
  availability: () => api.get('/billing/sandbox/availability/'),
  subscription: (projectId, envId) => api.get(`/projects/${uuid(projectId)}/environments/${uuid(envId)}/billing/sandbox/subscription/`),
  checkout: (projectId, envId, plan, period, idempotencyKey) => {
    if (!['essential','pro'].includes(plan) || !['monthly','annual'].includes(period)) throw new TypeError('Formule ou période invalide.')
    return api.post(`/projects/${uuid(projectId)}/environments/${uuid(envId)}/billing/sandbox/checkout/`, {
      plan, period, idempotency_key: uuid(idempotencyKey),
    })
  },
})
