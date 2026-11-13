import test from 'node:test'
import assert from 'node:assert/strict'
import { publicationLabel, cleanCustomerLog, phaseTitles } from '../src/services/deploymentPresentation.js'
test('statuts clients jaune, vert, rouge', () => {
  assert.deepEqual(publicationLabel('RUNNING'),['En cours','running'])
  assert.deepEqual(publicationLabel('SUCCEEDED'),['Réussi','success'])
  assert.deepEqual(publicationLabel('FAILED'),['Échec','danger'])
})
test('aucun jargon de moteur dans les logs clients', () => {
  assert.equal(cleanCustomerLog('AUTOKOM_LOG: Jenkins dispatch failed'), 'Étape de préparation en cours de vérification.')
  assert.equal(cleanCustomerLog('AUTOKOM_LOG: Vérification du site réussie'), 'Vérification du site réussie')
  assert.equal(cleanCustomerLog('AUTOKOM_STAGE:build:SUCCESS'),'')
})
test('les étapes ont des titres compréhensibles', () => {
  assert.equal(phaseTitles.build,'Préparation de l’application')
  assert.equal(phaseTitles.release,'Publication')
})
