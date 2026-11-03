# AUTOKOM · Correctif déploiement backend et interface terminal

Ce correctif est **ciblé** sur le gestionnaire `autokom app` et le pipeline du backend.
Il ne touche pas aux installations Jenkins, Docker, Nginx, MySQL, Grafana, ni aux secrets `/etc/autokom/`.

## Constat du serveur

- `backend.auto.ik0m.tech` répond `502` car Gunicorn n’est pas encore démarré (`autokom-backend.service inactive`).
- `autokom app deploy` échoue sur `semgrep scan --config auto --metrics off`.
- Les sorties de pip/Ansible/Certbot sont écrites sans filtrage dans le terminal de l’assistant.

## Correction

- SAST : config Semgrep locale `pipelines/autokom-backend/semgrep-rules.yml` versionnée, `--metrics off` ; une erreur de scanner ou une alerte grave bloque toujours le déploiement.
- Opérations setup/deploy : terminal avec animation temporaire et récapitulatif concis ; détails capturés dans `/var/log/autokom/platform/<action>-date-pid.log` avec permissions 0600, dossier 0700.
- `autokom app status` explique le cas « setup terminé, backend pas encore démarré », au lieu de lever `Échec de systemctl (3)`.
- `autokom app logs --source setup|deploy|service --lines 80` retrouve les journaux.
- Pipeline Jenkins aligné sur les mêmes règles Semgrep sans activer les métriques.

## Procédure

Sur Kali, utiliser `appliquer.py` avec `--repo` (simulation) puis `--apply`; vérifier le diff, exécuter les tests et publier sur `main`.
Sur Ubuntu, exécuter `sudo autokom update`, puis `sudo autokom app deploy --check` et `sudo autokom app deploy`.
Aucun nouveau `app setup` n’est nécessaire si le serveur est déjà configuré.

Si une étape échoue, consulter `sudo autokom app logs --source deploy --lines 100`, puis
`sudo journalctl -u autokom-backend -n 80 --no-pager`. Ne pas contourner Semgrep ou Trivy pour forcer la mise en ligne.

## Limites

Le résultat du health check dépend du démarrage réel de Django/Gunicorn et de MySQL ; les tests locaux ne simulent pas ces services. Les règles Semgrep incluses couvrent quelques anti-patterns critiques Python, pas un catalogue complet ; Trivy et Django complètent les contrôles, mais une politique plus riche devra être construite progressivement.
