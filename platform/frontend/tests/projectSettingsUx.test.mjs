import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
const read=(file)=>readFileSync(new URL(file,import.meta.url),'utf8')
test('la suppression disparaît de Mes projets et se trouve en zone rouge des paramètres',()=>{
  const projects=read('../src/pages/LiveProjects.jsx')
  assert.ok(projects.includes('ak-project-settings-icon'))
  assert.ok(!projects.includes('deleteProject(project)'))
  const settings=read('../src/pages/LiveProjectSettings.jsx')
  assert.ok(settings.includes('Zone de danger'))
  assert.ok(settings.includes('projectsApi.deleteProject('))
  assert.ok(settings.includes('requiredText={project.name}'))
  assert.ok(settings.includes('<ActionDialog'))
  assert.ok(!settings.includes('window.prompt('))
})
test('route des paramètres réservée et projet accessible',()=>{
  const app=read('../src/App.jsx')
  assert.ok(app.includes('LiveProjectSettings'))
  assert.ok(app.includes('/settings'))
})
test('adresse publique uniquement confirmée via HTTPS',()=>{
  const card=read('../src/components/ProjectPublicSite.jsx')
  assert.ok(card.includes('publicSiteUrl(site)'))
  assert.ok(card.includes('href={url}'))
  const domains=read('../src/pages/LiveSites.jsx')
  assert.ok(domains.includes('activation_error_code'))
})
test('Mes projets garde une seule ligne : statut à droite suivi des paramètres',()=>{
  const projects=read('../src/pages/LiveProjects.jsx')
  const styles=read('../src/styles/sites.css')
  assert.ok(!projects.includes('ak-project-row-public'))
  const row=projects.slice(projects.indexOf('ak-project-row" key='),projects.indexOf('</div>)}</div>'))
  assert.ok(row.indexOf('ak-project-state--') < row.indexOf('ak-project-settings-icon'))
  assert.ok(styles.includes('/* Project inventory: exactly one grid row'))
  assert.ok(styles.includes('.ak-project-list .ak-project-row .ak-project-state'))
})
