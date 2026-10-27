> **Utilisation actuelle :** `sudo autokom install` (automatique), `autokom status`, `sudo autokom check`. Les commandes internes ci-dessous restent destinées à l’expertise.

# AUTOKOM Phase 2 — Gardes réseau

**Objectif :** permettre `mysqld` sur `0.0.0.0` pour Docker **sans** exposer le port 3306 sur l'interface WAN. Le port 33060 (MySQL X), Jenkins 8080 et les ports de gestion connus sont eux aussi bloqués vers le WAN via UFW.

## Garanties / limites

- `--check` : aucune modification réseau. L'audit indique les ports Docker publiés potentiellement exposés.
- `--apply` : si UFW est inactif, refuse de modifier les services tant que l'activation protégée n'est pas demandée explicitement. Si UFW est actif, des blocages spécifiques à l'interface WAN sont appliqués **avant** les changements du service ; UFW autorise les bridges Docker existants à accéder au port du serveur SQL sélectionné.
- Les règles sont idempotentes, via `ufw show added`, et ne modifient pas les règles internes de Docker.
- Les publications `docker -p 0.0.0.0:PORT:...` (sauf 80/443) doivent être corrigées séparément : Docker peut contourner les règles UFW sur les ports publiés. L'installation refuse l'application si elle en détecte.
- Aucune permission MySQL pour `user@'%'` n'est ajoutée par ce module. Le bind `0.0.0.0` n'accorde aucun privilège SQL.
- Les Security Groups AWS restent à configurer pour n'autoriser que 22 depuis l'IP d'administration, 80/443 pour les applications, et **aucun port SQL / Jenkins direct depuis Internet**.
- L'activation d'UFW sur un serveur SSH est à haut risque. Utiliser une session persistante et un accès console EC2 de secours. La protection de la session courante est vérifiée si la variable `SSH_CONNECTION` est disponible.
- La configuration historique des services, les restrictions applicatives et les règles de pare-feu extérieures peuvent limiter l'accès des conteneurs ; faire les tests fonctionnels après application.

## Avant tout changement

```bash
sudo bash scripts/security/network-audit.sh
python3 scripts/lib/services.py --help
sudo bash scripts/install/services.sh --check
```

## Si UFW est déjà actif

```bash
sudo bash scripts/install/services.sh --apply
```

## Si UFW est inactif et que SSH est accessible depuis une IP fixe

**Remplacer `203.0.113.10/32` par le CIDR réel de votre adresse publique d'administration.** Ne jamais copier ce réseau d'exemple dans un serveur réel.

```bash
sudo bash scripts/install/services.sh --apply \
  --enable-ufw --ssh-cidr VOTRE_IP_PUBLIQUE/32 --ssh-port 22
```

Une confirmation interactive `ACTIVER-PAREFEU` sera exigée. Pour un environnement non interactif, `--confirm-firewall` doit être passé explicitement.

Si vous choisissez `--bind all`, il définit **uniquement** l'écoute sur `0.0.0.0`; les comptes SQL autorisés restent régis par les hôtes/CIDR séparément. Ne pas autoriser `0.0.0.0/0` dans la liste des clients SQL.

## Contrôles après installation

```bash
sudo ufw status numbered
sudo ufw show added
sudo ss -lntp | grep -E ':(3306|33060|8080|5432)[[:space:]]'
sudo mysql --protocol=socket -uroot -NBe 'SELECT @@GLOBAL.bind_address;'
sudo nginx -t
sudo docker ps --format 'table {{.Names}}\t{{.Ports}}'
```

Tester la connectivité MySQL depuis **un conteneur autorisé** et vérifier depuis **une machine extérieure** que 3306/33060/8080 sont fermés. Ne pas confondre un état UFW valide avec une preuve d'inaccessibilité externe.
