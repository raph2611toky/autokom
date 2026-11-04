"""Provision the trusted Jenkins pipeline (one-time, no overwrite of existing jobs)."""
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree as ET
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from apps.deployments.runs.jenkins import JenkinsClient


class Command(BaseCommand):
    help = 'Crée le job Jenkins AUTOKOM approuvé, uniquement si absent (--apply).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        path = Path(settings.BASE_DIR) / 'deployment_templates' / 'Jenkinsfile.trusted'
        if not path.is_file():
            raise CommandError('Jenkinsfile approuvé introuvable.')
        script = path.read_text(encoding='utf-8')
        if 'checkout(' not in script or 'AUTOKOM_RUN_ID' not in script:
            raise CommandError('Jenkinsfile approuvé incomplet.')
        client = JenkinsClient()
        try:
            response = client.session.get(client.base + client.job_path + '/api/json',
                 timeout=(4,12), allow_redirects=False)
        except Exception:
            raise CommandError('Jenkins inaccessible.')
        if response.status_code == 200:
            self.stdout.write('Job Jenkins déjà présent — AUTOKOM ne le remplace pas automatiquement.')
            return
        if response.status_code != 404:
            raise CommandError('Impossible de vérifier le job Jenkins (permissions/connexion).')
        if not options['apply']:
            self.stdout.write('Plan : création du job Jenkins ' + client.job + ' (aucun changement).')
            return
        # Inline Pipeline script is controlled by backend source, NOT user repositories.
        root = ET.Element('flow-definition', {'plugin':'workflow-job'})
        ET.SubElement(root, 'description').text = 'AUTOKOM client deployments — TRUSTED SOURCE ONLY'
        ET.SubElement(root, 'keepDependencies').text = 'false'
        properties = ET.SubElement(root, 'properties')
        params = ET.SubElement(properties, 'hudson.model.ParametersDefinitionProperty')
        definitions = ET.SubElement(params, 'parameterDefinitions')
        parameter = ET.SubElement(definitions, 'hudson.model.StringParameterDefinition')
        ET.SubElement(parameter, 'name').text = 'AUTOKOM_RUN_ID'
        ET.SubElement(parameter, 'description').text = 'Identifiant UUID du déploiement AUTOKOM.'
        ET.SubElement(parameter, 'defaultValue').text = ''
        ET.SubElement(parameter, 'trim').text = 'true'
        definition = ET.SubElement(root, 'definition', {
            'class':'org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition', 'plugin':'workflow-cps'})
        ET.SubElement(definition, 'script').text = script
        ET.SubElement(definition, 'sandbox').text = 'true'
        ET.SubElement(root, 'disabled').text = 'false'
        body = ET.tostring(root, encoding='utf-8', xml_declaration=True)
        try:
            res = client.session.post(client.base + '/createItem', params={'name':client.job},
                     data=body, headers={'Content-Type':'application/xml'},
                     timeout=(5,30), allow_redirects=False)
        except Exception:
            raise CommandError('Création Jenkins indisponible.')
        if res.status_code not in (200,201,302):
            raise CommandError('Création Jenkins refusée. Vérifier les permissions Create Job.')
        self.stdout.write(self.style.SUCCESS('Job Jenkins créé ; vérifier credentials et agents isolés avant lancement.'))
