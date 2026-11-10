# AUTOKOM — abonnement actif et présentation HA

Base de lecture : commit a9d56c4b73 (11 novembre 2026).

Ce lot ne modifie aucune ressource GCP, aucun droit de plan, aucune logique de paiement et aucun état Django. Il contient uniquement des fichiers source complets :

- `platform/frontend/src/pages/Pricing.jsx` : lit `/api/billing/account/` pour les utilisateurs authentifiés ; surligne l'offre réellement active par une bordure verte et une coche ; empêche de souscrire à l'offre déjà active ; ne déduit pas l'abonnement de `localStorage`.
- `platform/frontend/src/styles/pricing.css` : ajoute les styles de l'offre active et une présentation compacte de l'option HA existante sur la page de déploiement (interrupteur visuel à la place de la case native, détails révélés par survol ou focus clavier). La valeur envoyée au backend reste celle du champ `highAvailability` existant.

## Limites intentionnelles

- `apps --diagnose` n'indique pas l'abonnement de Django : `HA_DEMANDEE` provient du fichier local `/var/lib/autokom/client-runtime/<environment>/current.json` et peut être différent de la configuration demandée et finalisée.
- `CLOUD_DELIVERY_NON_DEMANDE`, `RAM_HORS_BORNE_DELIVERY` et `CPU_HORS_BORNE_DELIVERY` doivent être résolus dans le processus de déploiement et les ressources, sans falsifier les indicateurs de santé HA.
- `enable_https=false` et `domain_name=null` correspondent actuellement à la préparation HTTP du Load Balancer. Ne pas basculer le DNS ni Terraform avant d'avoir deux répliques applicatives saines et un certificat public opérationnel.

## Vérification d'un état de publication (sans lecture de secrets)

Sur le serveur principal :

```bash
sudo python3 - <<'PY'
from pathlib import Path
import json
p=Path('/var/lib/autokom/client-runtime/94ff7746-5db4-40ee-b3cd-6f2e6ea7e1d0/current.json')
d=json.loads(p.read_text())
for field in ('run_id','cloud_delivery_requested','high_availability_requested','memory_mb','cpu_millicores','variables_present','stack_project_id'):
    print(f'{field} : {d.get(field)!r}')
PY
```

Ne pas copier le fichier complet `current.json` en public. Ne pas modifier directement ce fichier : c'est un état dérivé des publications.
