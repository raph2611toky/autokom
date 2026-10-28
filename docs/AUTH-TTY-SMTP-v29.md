# AUTOKOM v29 — Terminal d'authentification et SMTP

## Problème reproduit

La v28 utilisait `open('/dev/tty','r+')` pour lire les formulaires de `autokom auth`.
Sur les TTY Linux, cette ouverture peut lever
`io.UnsupportedOperation: File or stream is not seekable`. L'erreur était ensuite
présentée à tort comme « Terminal interactif requis », alors que le menu de
sélection fonctionnait.

## Résolution sans modification des autres modules

`autokom auth` réutilise la saisie texte `/dev/tty` de `menu.ask` et ouvre les
secrets avec `os.open('/dev/tty', os.O_RDWR | os.O_NOCTTY)`, désactive
l'écho du terminal via `termios` et restaure les attributs dans `finally`.
La saisie est masquée pour la passphrase, le mot de passe SMTP, le jeton Jenkins et les OTP. Les champs non secrets (hôte, login SMTP et adresses) restent visibles pendant la saisie.
La sécurité refusera explicitement une session sans véritable terminal.

## Configuration initiale

```sh
sudo autokom auth smtp
sudo autokom auth setup
sudo autokom auth status
sudo autokom auth verify
```

`sudo autokom auth` permet de choisir les mêmes opérations par le menu. Le
formulaire SMTP demande, dans l'ordre :

| Champ | Rôle |
|---|---|
| `EMAIL_HOST` | Domaine DNS SMTP, ex. smtp.example.org |
| `EMAIL_PORT` | 587 (STARTTLS, défaut) ou 465 (TLS direct) |
| `EMAIL_LOGIN` | Compte SMTP ; optionnel lorsque le fournisseur n'exige pas d'authentification |
| `EMAIL_MDP` | Mot de passe SMTP, saisi de manière masquée |
| `EMAIL_FROM` | Adresse expéditrice du code OTP |
| `EMAIL_ADMIN` | Adresse de récupération, confirmée deux fois |

La vérification du serveur exige TLS et valide, s'il y a lieu, l'identification
avant la sauvegarde. Le fichier `/etc/autokom/security/smtp.json` (root:0600)
utilise ces six noms. Les anciens fichiers v28 (`host`, `port`, `username`,
`password`, `sender`) restent lisibles : la migration du contenu ne se fait
que lors d'une configuration volontaire. Le mot de passe SMTP est un secret
réutilisable **non hachable**, contrairement à la passphrase AUTOKOM, qui
reste **uniquement hachée et salée**.

`auth setup` refuse de demander une passphrase tant que SMTP n'est pas
configuré. Le formulaire redemande l'adresse de récupération deux fois, puis
la compare à `EMAIL_ADMIN` s'il est défini. Il envoie un OTP à cette adresse,
valable 10 minutes, à usage unique et limité à cinq essais. L'activation
réussit uniquement après réception et validation effective du code.

Lorsque l'authentification est déjà activée, une modification SMTP exige
l'ancienne passphrase. Changer silencieusement `EMAIL_ADMIN` est interdit :
le processus ne remplace jamais une adresse de récupération déjà vérifiée.

## Tester sans exposer de secrets

```sh
sudo autokom auth status
sudo autokom auth smtp
sudo autokom auth setup
sudo autokom auth verify
```

Les sorties ne doivent afficher ni `EMAIL_MDP`, ni la passphrase, ni le code
OTP. SMTP opérationnel ne prouve pas à lui seul la réception du mail : le test
OTP de `auth setup` est la preuve finale. Le serveur doit pouvoir joindre le
serveur SMTP choisi depuis son réseau sortant AWS.

## Vérifications d'intégration

La suite teste une vraie session en pseudo-terminal : choix numérique dans
le menu, saisie SMTP, mot de passe dont l'écho est désactivé et saisie suivante.
Elle teste les six champs SMTP, les permissions root, STARTTLS, TLS direct,
l'absence de configuration partielle si la confirmation échoue, la
compatibilité v28 et le refus d'une adresse de récupération incohérente.
Aucune modification de Jenkins, Grafana, Loki, Alloy ou des reverse proxies.
