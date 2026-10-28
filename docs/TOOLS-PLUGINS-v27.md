# AUTOKOM v27 — Gestion des plugins de services

## Objectif

Les menus `autokom tools jenkins plugins` et `autokom tools grafana plugins` utilisent
le sélecteur AUTOKOM (flèches, chiffres, Entrée, 0/q). Les commandes `pluggins`
(faute fréquente) sont acceptées comme alias. L'interface est facultative :
chaque action possède `-h/--help`, des paramètres et un mode de consultation
sans modification. Les anciens `tools jenkins files`, `cat`, `nano`, `env` restent intacts.

## Jenkins (catalogue officiel)

La recherche interroge en HTTPS `https://updates.jenkins.io/update-center.actual.json`,
avec la version du contrôleur si celle-ci est disponible. Aucune suggestion
préremplie n'est présentée comme résultat officiel. Les catégories dépendent
des étiquettes réellement fournies par le catalogue. La liste des plugins
installés peut être lue hors ligne depuis `/var/lib/jenkins/plugins` (sans
prétendre connaître les mises à jour) ; pour savoir si des mises à jour sont
réellement disponibles il faut lire l'API de ce serveur avec son jeton.

```console
autokom tools jenkins plugins -h
autokom tools jenkins plugins
autokom tools jenkins plugins search 'security scan' --select
autokom tools jenkins plugins search kubernetes --category cloud
sudo autokom tools jenkins plugins list --auth-file /root/jenkins-api.auth --updates
sudo autokom tools jenkins plugins install git --auth-file /root/jenkins-api.auth --apply
sudo autokom tools jenkins plugins upgrade --auth-file /root/jenkins-api.auth --apply
sudo autokom tools jenkins plugins remove NOM --auth-file /root/jenkins-api.auth --apply
```

Créer le fichier de jeton **sur le serveur** (ne jamais partager son contenu,
ni le mettre dans une commande shell ou dans Git) :

```console
sudo install -m 600 -o root -g root /dev/null /root/jenkins-api.auth
sudo nano /root/jenkins-api.auth
```

Le contenu est `nom_admin:jeton_API_Jenkins`. Le jeton doit correspondre à
un compte disposant des droits appropriés pour gérer les plugins. La CLI
AUTOKOM réutilise le contrôle d'identité et d'autorisation Jenkins déjà
présent dans AUTOKOM (Jenkins CLI, canal HTTP local, `-auth @fichier`).

La simulation ne modifie aucun plugin. Une application exige sudo + confirmation
clavier. Le gestionnaire Jenkins ne redémarre jamais automatiquement le serveur :
prévoir un arrêt contrôlé des builds. Avant modification, les archives `.jpi/.hpi`
existantes sont copiées dans `/var/backups/autokom/jenkins-plugins/<UTC>`.
Suppression : refuser le plugin lorsqu'un autre plugin dépend de lui ou que les
métadonnées de dépendance ne sont pas vérifiables. Les plugins de base du profil
AUTOKOM DevSecOps sont protégés. Les configurations Jenkins des plugins ne sont
pas détruites. Après modification, valider les versions et le comportement du
contrôleur, puis envisager un redémarrage sécurisé.

## Grafana (catalogue officiel)

Recherche réelle `https://grafana.com/api/plugins` et vérification de la fiche
individuelle avant installation. Le mécanisme existant de la v25 est conservé,
avec la CLI officielle `grafana cli plugins ...`.

```console
autokom tools grafana plugins
autokom tools grafana plugins search 'logs' --type app --select
autokom tools grafana plugins list --filter grafana
autokom tools grafana plugins install ID --check
sudo autokom tools grafana plugins install ID --apply
sudo autokom tools grafana plugins upgrade --apply
sudo autokom tools grafana plugins update ID --apply
sudo autokom tools grafana plugins remove ID --apply
```

La commande `upgrade` utilise `grafana cli plugins update-all`, et `remove`
`grafana cli plugins remove ID`. Une confirmation et une sauvegarde du répertoire
des plugins précèdent toute mutation. Les plugins déclarés dans `plugins.preinstall`
sont protégés contre la suppression. L'application d'une mise à jour ou d'une
suppression ne redémarre pas Grafana automatiquement : cela doit être fait
hors période sensible. L'installation simple conserve le redémarrage contrôlé
existant de la v25. L'ancien `autokom grafana plugins ...` demeure pris en charge.

## Limites explicites

- Résultats du catalogue indisponibles si réseau absent : aucun faux résultat.
- Recherche lexicale avec synonymes, pas d'embeddings sémantiques.
- Jenkins : statut `hasUpdate` déterminé par le contrôleur et son Update Center,
  pas par une comparaison de chaînes de versions Jenkins.
- Une mise à jour de plugin peut nécessiter une version minimale plus récente
  du core ou une migration de configuration : vérifier les dépendances et faire
  un snapshot avant un lot en production. Une sauvegarde d'archives seule n'est
  pas une stratégie complète de restauration.
- Les plugins Grafana embarqués ou gérés par le core/`preinstall` ne doivent pas
  être traités comme extensions indépendantes.
- Ni le catalogue officiel, ni une exécution réussie de l'outil CLI ne prouvent
  que les plugins sont fonctionnels après redémarrage : tests sur EC2 nécessaires.
