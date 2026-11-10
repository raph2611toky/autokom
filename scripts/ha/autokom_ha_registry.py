#!/usr/bin/env python3
"""AUTOKOM HA 2D: explicit, fail-closed publication of an AUTOKOM build to Artifact Registry.

This tool does not route traffic, update Terraform, run customer containers or modify
AUTOKOM's current on-host release. Use it only for deliberate image promotion.
No service account key and no Snap/gcloud dependency: GCE metadata token only.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROJECT = 'plucky-stacker-480421-c4'
REGION = 'us-central1'
REPO = 'autokom-ha-images'
ARTIFACTS = Path('/var/lib/autokom/build-artifacts')
METADATA = 'http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default'
PROJECT_RE = re.compile(r'[a-z][a-z0-9-]{4,61}[a-z0-9]\Z')
REGION_RE = re.compile(r'[a-z]+-[a-z0-9]+[0-9]\Z')
REPO_RE = re.compile(r'[a-z][a-z0-9-]{1,62}\Z')
RUN_RE = re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\Z')
DIGEST_RE = re.compile(r'sha256:[a-f0-9]{64}\Z')
MANIFEST_TYPES = ', '.join((
    'application/vnd.oci.image.index.v1+json',
    'application/vnd.oci.image.manifest.v1+json',
    'application/vnd.docker.distribution.manifest.list.v2+json',
    'application/vnd.docker.distribution.manifest.v2+json',
))


class PromotionError(Exception):
    pass


def identifiers(project: str, region: str, repository: str, run_id: str | None = None) -> None:
    if not PROJECT_RE.fullmatch(project) or not REGION_RE.fullmatch(region) or not REPO_RE.fullmatch(repository):
        raise PromotionError('Identifiants du registre non autorisés.')
    if run_id is not None and not RUN_RE.fullmatch(run_id):
        raise PromotionError('Identifiant de run invalide (UUID canonique requis).')


def image_tag(project: str, region: str, repository: str, run_id: str) -> str:
    identifiers(project, region, repository, run_id)
    rid = run_id.replace('-', '')
    return f'{region}-docker.pkg.dev/{project}/{repository}/client-{rid}:{rid}'


def _read_metadata(path: str, *, json_response: bool = False):
    req = Request(f'{METADATA}/{path}', headers={'Metadata-Flavor': 'Google'})
    try:
        with urlopen(req, timeout=8) as result:
            raw = result.read(8192)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise PromotionError('Identité GCE indisponible (vérifier le compte et les scopes de la VM).') from exc
    try:
        return json.loads(raw) if json_response else raw.decode('utf-8').strip()
    except (ValueError, UnicodeDecodeError) as exc:
        raise PromotionError('Réponse du service de métadonnées GCE invalide.') from exc


def metadata_identity() -> str:
    value = _read_metadata('email')
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_.-]+@[a-z0-9.-]+\.gserviceaccount\.com', value):
        raise PromotionError('Compte de service GCE absent ou invalide.')
    return value


def metadata_token() -> str:
    doc = _read_metadata('token', json_response=True)
    token = doc.get('access_token') if isinstance(doc, dict) else None
    if not isinstance(token, str) or len(token) < 20:
        raise PromotionError('Jeton GCE indisponible.')
    return token


def check_repository(project: str, region: str, repository: str, token: str) -> None:
    identifiers(project, region, repository)
    url = f'https://artifactregistry.googleapis.com/v1/projects/{project}/locations/{region}/repositories/{repository}'
    req = Request(url, headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'})
    try:
        with urlopen(req, timeout=18) as response:
            if response.status != 200:
                raise PromotionError('Dépôt Artifact Registry non disponible.')
            # The server response has no executable content and is never printed.
            doc = json.loads(response.read(131072))
            if doc.get('format') not in ('DOCKER',):
                raise PromotionError('Le dépôt cible n’est pas un dépôt Docker.')
    except HTTPError as exc:
        raise PromotionError(f'Artifact Registry HTTP_{exc.code} : vérifier IAM et les scopes GCE.') from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise PromotionError('Impossible de vérifier Artifact Registry.') from exc


def registry_manifest_digest(image: str, token: str) -> str | None:
    """Perform a read-only registry HEAD; never download image layers or reveal auth."""
    registry, path = image.split('/', 1)
    name, tag = path.rsplit(':', 1)
    req = Request(f'https://{registry}/v2/{name}/manifests/{tag}', method='HEAD',
                  headers={'Authorization': 'Basic ' + base64.b64encode(('oauth2accesstoken:' + token).encode()).decode(),
                           'Accept': MANIFEST_TYPES})
    try:
        with urlopen(req, timeout=25) as response:
            if response.status != 200:
                raise PromotionError('Registre Docker inaccessible.')
            digest = str(response.headers.get('Docker-Content-Digest') or '')
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise PromotionError(f'Verification du manifeste : HTTP_{exc.code}.') from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise PromotionError('Registre Docker temporairement inaccessible.') from exc
    if not DIGEST_RE.fullmatch(digest):
        raise PromotionError('Digest du manifeste manquant ou invalide.')
    return digest


def run_docker(arguments: list[str], *, config: str | None = None, input_data: str | None = None,
               timeout: int = 60, fds: tuple[int, ...] = ()) -> str:
    """Fixed Docker CLI with scrubbed environment; never report captured output."""
    if not Path('/var/run/docker.sock').is_socket():
        raise PromotionError('Socket Docker local indisponible.')
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8', 'HOME': '/root'}
    args = ['/usr/bin/docker', '--host', 'unix:///var/run/docker.sock']
    if config:
        args += ['--config', config]
    args += arguments
    try:
        proc = subprocess.run(args, capture_output=True, text=True, input=input_data,
                              check=False, timeout=timeout, pass_fds=fds, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PromotionError('Commande Docker indisponible ou expirée.') from exc
    if proc.returncode:
        raise PromotionError('Commande Docker échouée (sortie masquée : aucun secret affiché).')
    return proc.stdout[:131072]


def load_trusted_build(run_id: str, artifacts: Path = ARTIFACTS) -> None:
    """Import ONLY a bounded, fixed-path Jenkins build artifact. No caller-supplied path."""
    identifiers(PROJECT, REGION, REPO, run_id)
    candidate = artifacts / (run_id + '.tar')
    if candidate.is_symlink() or not candidate.is_file():
        raise PromotionError('Artefact Jenkins absent ou lien symbolique non autorisé.')
    fd = os.open(candidate, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or not (1024 <= st.st_size <= 8 * 1024**3):
            raise PromotionError('Taille ou type de l’artefact Jenkins invalide.')
        run_docker(['load', '--input', f'/proc/self/fd/{fd}'], timeout=600, fds=(fd,))
    finally:
        os.close(fd)


def publish(run_id: str, project: str, region: str, repository: str, *, from_archive: bool,
            identity: str, token: str) -> str:
    identifiers(project, region, repository, run_id)
    if os.geteuid() != 0:
        raise PromotionError('La publication nécessite root, uniquement pour accéder au Docker local.')
    image = image_tag(project, region, repository, run_id)
    # A run UUID is immutable: never replace an already-published tag.
    if registry_manifest_digest(image, token) is not None:
        raise PromotionError('Ce run existe déjà dans le registre ; remplacement interdit.')
    if from_archive:
        load_trusted_build(run_id)
    source = 'autokom-local:' + run_id
    run_docker(['image', 'inspect', source, '--format', '{{.Id}}'], timeout=15)
    print('Image locale AUTOKOM vérifiée. Publication privée en cours…', flush=True)
    with tempfile.TemporaryDirectory(prefix='autokom-ha-docker-') as directory:
        os.chmod(directory, 0o700)
        host = f'{region}-docker.pkg.dev'
        run_docker(['login', '-u', 'oauth2accesstoken', '--password-stdin', f'https://{host}'],
                   config=directory, input_data=token + '\n', timeout=30)
        try:
            run_docker(['tag', source, image], timeout=25)
            run_docker(['push', image], config=directory, timeout=1800)
        finally:
            try:
                run_docker(['image', 'rm', image], timeout=30)
            except PromotionError:
                pass
    # Verify manifest server-side after the push: never mistake a Docker tag for a publication.
    for attempt in range(5):
        digest = registry_manifest_digest(image, token)
        if digest is not None:
            return f'{image.split(":", 1)[0]}@{digest}'
        time.sleep(min(2 * (attempt + 1), 8))
    raise PromotionError('Envoi terminé, mais aucun manifeste vérifiable : publication non confirmée.')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='AUTOKOM HA 2D : publication Docker privée et explicite.')
    parser.add_argument('--project', default=PROJECT)
    parser.add_argument('--region', default=REGION)
    parser.add_argument('--repository', default=REPO)
    parser.add_argument('--run-id', default=None)
    parser.add_argument('--check', action='store_true', help='lecture seule : identité et dépôt')
    parser.add_argument('--publish', action='store_true', help='publier un run Jenkins de manière explicite')
    parser.add_argument('--from-build-archive', action='store_true', help='importer le TAR Jenkins local si non déjà importé')
    parser.add_argument('--apply', action='store_true', help='confirmation requise pour toute publication')
    args = parser.parse_args(argv)
    try:
        identifiers(args.project, args.region, args.repository, args.run_id)
        if args.check == args.publish:
            raise PromotionError('Choisir exactement --check ou --publish.')
        if args.check and (args.apply or args.from_build_archive):
            raise PromotionError('Le mode vérification est strictement en lecture seule.')
        if args.publish and (not args.apply or not args.run_id):
            raise PromotionError('Publication : --apply et --run-id obligatoires.')
        identity = metadata_identity()
        token = metadata_token()
        check_repository(args.project, args.region, args.repository, token)
        if args.check:
            print(f'Identité GCE : {identity}')
            print(f'Registre accessible : {args.region}-docker.pkg.dev/{args.project}/{args.repository}')
            print('NB : accès en lecture confirmé ; droit de push vérifié uniquement à la publication.')
            return 0
        digest_reference = publish(args.run_id, args.project, args.region, args.repository,
                                   from_archive=args.from_build_archive, identity=identity, token=token)
        print('Image publiée et manifeste vérifié : ' + digest_reference)
        print('Aucun worker, domaine, LB ou déploiement local modifié.')
        return 0
    except PromotionError as exc:
        print('AUTOKOM_HA_REGISTRY_ERROR: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
