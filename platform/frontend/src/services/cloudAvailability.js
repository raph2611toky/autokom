const deliveryLabels = {
  NOT_DEPLOYED: 'Aucune version livrée sur GCP',
  UNAVAILABLE: 'État GCP indisponible',
  ASSIGNED: 'Réplicas en cours de préparation',
  WAITING_FOR_HEALTH: 'En attente des contrôles de santé',
  WAITING_WORKER_UPGRADE: 'En attente de la mise à jour des workers',
  SCALING_UP: 'Augmentation de la capacité du MIG',
  INSUFFICIENT_CAPACITY: 'Capacité insuffisante',
  CAPACITY_LIMIT: 'Plafond de capacité atteint',
  HTTP_LB_READY_NOT_HTTPS: 'Routage HTTP GCP prêt · bascule HTTPS non validée',
}

export function cloudAvailability(state, siteHttps = false) {
  const delivery = state?.cloud_delivery
  const httpReady = delivery?.http_lb_ready === true
  const httpsReady = delivery?.public_https_ready === true
  const status = typeof delivery?.status === 'string' ? delivery.status : ''
  const counts = [delivery?.healthy_replicas, delivery?.requested_replicas]
  const countsKnown = status && !['NOT_DEPLOYED', 'UNAVAILABLE', 'UNKNOWN'].includes(status) &&
    counts.every(n => Number.isInteger(n) && n >= 0) && counts[1] > 0
  return {
    option: state?.ha?.status === 'ACTIVE' ? 'Option autorisée' :
      state?.ha?.status === 'PENDING' ? 'Option en attente de validation' :
        state?.ha?.status ? 'Option non active' : 'Non vérifiable',
    replicas: countsKnown ? counts.join(' / ') : 'Non vérifiable',
    delivery: httpsReady ? 'Routage public GCP validé' :
      (Object.hasOwn(deliveryLabels, status) ? deliveryLabels[status] :
        status.startsWith('RECONCILE_ERROR_') ? 'Erreur de synchronisation GCP' : 'État GCP non vérifiable'),
    http: httpReady ? 'Vérifié pour cette application' : 'Non validé',
    https: httpsReady ? 'Validé via le load balancer GCP' : 'Non validé via le load balancer GCP',
    note: !httpsReady && siteHttps ? 'Le HTTPS du site public est distinct du routage HTTPS via GCP.' : '',
  }
}
