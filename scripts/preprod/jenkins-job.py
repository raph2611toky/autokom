"""Provision or verify the sole staging job; credentials never appear in argv."""
import argparse
import getpass
from pathlib import Path
import sys
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from autokom_runtime_admin import (jenkins_request, pipeline_xml, _read_jenkins_job_pipeline,
                                   _canonical_pipeline, isolated_agent_status)
from configure import private, ETC

JOB = 'autokom-preprod-client-deploy'
CONFIG = '/job/' + JOB + '/config.xml'


def sync(env, source, apply=False):
    code, _ = jenkins_request(env, 'GET', CONFIG)
    if code == 200:
        existing = _read_jenkins_job_pipeline(env, JOB)
        if _canonical_pipeline(existing) == _canonical_pipeline(source):
            return 'Pipeline préproduction identique.'
        if apply:
            raise ValueError('Job préexistant différent. Vérifier son contenu avant remplacement.')
        return 'Job différent, aucune modification.'
    if code != 404:
        raise ValueError('Droits Jenkins insuffisants.')
    if not apply:
        return 'Job préproduction absent. Création possible.'
    code, _ = jenkins_request(env, 'POST', '/createItem?' + urlencode({'name': JOB}), pipeline_xml(source))
    if code not in (200, 201, 202, 302) or _canonical_pipeline(
            _read_jenkins_job_pipeline(env, JOB)) != _canonical_pipeline(source):
        raise ValueError('Création du job non vérifiée.')
    return 'Job préproduction créé et vérifié.'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    env = private(ETC / 'runtime.env')
    env['AUTOKOM_JENKINS_USER'] = input('Compte administrateur Jenkins : ').strip()
    env['AUTOKOM_JENKINS_API_TOKEN'] = getpass.getpass('Jeton API Jenkins (masqué) : ')
    source = Path(__file__).with_name('Jenkinsfile').read_text(encoding='utf-8')
    print(sync(env, source, apply=args.apply))
    ready, _ = isolated_agent_status(env)
    print('Agent BuildKit isolé : ' + ('prêt' if ready else 'non disponible'))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        raise SystemExit('Job préproduction : opération non confirmée (' + type(exc).__name__ + ').') from None
