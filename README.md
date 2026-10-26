# AUTOKOM — Phase 3 · paramètres dynamiques v2

Correctif incrémental à appliquer **sur un dépôt AUTOKOM contenant déjà la Phase 3 Observabilité**.

Fichiers nouveaux ou modifiés :

- `scripts/lib/observability.py` — refactor des ports, URLs et paramètres reliés.
- `scripts/lib/observability_config.py` — lecture/validation de la configuration unique.
- `config/observability/defaults.ini` — valeurs par défaut, sans secret.
- `tests/test_observability.py` — compatibilité des tests existants.
- `tests/test_observability_config.py` — tests de régression des paramètres dynamiques.
- `docs/OBSERVABILITY-CONFIG.md` — utilisation et migration.

**Sécurité :** aucun changement réseau avant `--apply` ; hôtes internes validés `127.0.0.1` uniquement ; refus des conflits de ports, chemins non sûrs et déplacements implicites de TSDB. Les identifiants restent interactifs.

Exemples :

```bash
python3 scripts/lib/observability.py --show-config
python3 scripts/lib/observability.py --show-config \
   --set prometheus.port=9091 --set grafana.port=3001

sudo bash scripts/install/08-observability.sh --check --interactive
```

Voir `docs/OBSERVABILITY-CONFIG.md` pour les priorités, la persistance et les avertissements production.
