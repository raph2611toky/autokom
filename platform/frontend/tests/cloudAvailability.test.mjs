import assert from 'node:assert/strict'
import { test } from 'node:test'
import { cloudAvailability } from '../src/services/cloudAvailability.js'

test('deux réplicas sains et le site public en HTTPS ne prouvent pas le HTTPS sur GCP', () => {
  const info = cloudAvailability({ ha: { status:'ACTIVE' }, cloud_delivery: {
    status:'HTTP_LB_READY_NOT_HTTPS', http_lb_ready:true, public_https_ready:false,
    healthy_replicas:2, requested_replicas:2,
  } }, true)
  assert.equal(info.option, 'Option autorisée')
  assert.equal(info.replicas, '2 / 2')
  assert.match(info.delivery, /HTTP GCP prêt/)
  assert.match(info.https, /^Non validé/)
  assert.match(info.note, /distinct/)
})

test('les valeurs par défaut d’un diagnostic indisponible ne sont pas des mesures', () => {
  const info = cloudAvailability({ cloud_delivery: {
    status:'UNAVAILABLE', healthy_replicas:0, requested_replicas:1,
  } })
  assert.equal(info.replicas, 'Non vérifiable')
  assert.equal(info.option, 'Non vérifiable')
  assert.equal(info.note, '')
})
