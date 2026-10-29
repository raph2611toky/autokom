# AUTOKOM v30 — vérification du compte, OTP et codes de secours

## Contrat UX

1. `sudo autokom auth smtp` demande EMAIL_HOST, EMAIL_PORT, EMAIL_LOGIN,
   EMAIL_MDP (masqué), EMAIL_FROM, EMAIL_ADMIN et sa confirmation **une seule fois**.
   La connexion SMTP est testée avec TLS vérifié (587 STARTTLS ou 465 TLS direct).
2. `sudo autokom auth setup` récupère automatiquement EMAIL_ADMIN déjà enregistré.
   Il **n'en redemande pas la saisie**. Le système envoie immédiatement un OTP
   unique à cette adresse puis exige le code reçu **avant** de proposer la
   nouvelle passphrase. Une adresse syntaxiquement correcte n'est jamais
   marquée comme vérifiée tant que le code n'a pas été validé.
3. Après double saisie de la passphrase (14–512 caractères), AUTOKOM écrit
   `auth.json` en mode root:0600 avec le hash salé PBKDF2-HMAC-SHA256 (600 000 itérations),
   puis génère **10 codes de secours** aléatoires à 128 bits chacun.
4. Les codes de secours sont affichés **une fois seulement**, à conserver
   **hors du serveur**. AUTOKOM ne stocke que des empreintes salées de ces
   codes (SHA-256 sur 128 bits de données aléatoires). Chaque code est
   consommé atomiquement et ne peut plus être réutilisé.
5. Le fichier OTP ne contient pas le code en clair : hash PBKDF2 salé, expiration
   10 minutes, 5 essais maximum et impossibilité de contourner cette limite
   en demandant un nouvel OTP pendant sa validité. Un nouveau lancement de
   `auth setup` peut reprendre le code encore valide.

## Commandes

```bash
sudo autokom auth status
sudo autokom auth smtp
sudo autokom auth setup
sudo autokom auth verify
sudo autokom auth reset --method email
sudo autokom auth reset --method code
sudo autokom auth recovery-codes   # révoque et remplace TOUS les codes, nécessite la passphrase
sudo autokom auth migrate          # chiffre les anciens secrets v28/v29, nécessite la passphrase si activée
sudo autokom auth jenkins-token    # mémorise le jeton sous forme chiffrée
```

`sudo autokom auth reset` sans `--method` ouvre le menu habituel (e-mail ou code).
La récupération par code fonctionne même si le serveur SMTP ne fonctionne plus.

## Secret réutilisable ≠ mot de passe vérifiable

- **Passphrase, OTP, codes de secours** : hachage unidirectionnel ; jamais stockés
  en clair dans les JSON de la CLI.
- **Mot de passe SMTP et jeton API Jenkins** : ils doivent être réutilisables
  pour s'authentifier auprès d'autres services et **ne peuvent donc pas être
  simplement hachés**. AUTOKOM les chiffre avec `cryptography.fernet`
  (chiffrement authentifié) et stocke la clé dans
  `/etc/autokom/security/vault.key` (root:0600) ; `smtp.json` et
  `jenkins-api.auth` restent root:0600. `/etc/autokom/security` est root:0700.
- Le coffre local **ne résiste pas à la compromission de root**, qui peut
  déchiffrer les secrets. Il ne remplace pas un gestionnaire de secrets dédié
  (AWS Secrets Manager, Vault, TPM/HSM) ni les permissions Jenkins/Grafana.
- Sur Ubuntu, vérifier la présence de `python3-cryptography`, sinon installer
  le paquet par la méthode de gestion de dépendances validée pour le serveur.

## Passage depuis AUTOKOM v29

Le format SMTP précédent contient `EMAIL_MDP` en clair dans un fichier
root:0600. `auth setup` le convertit en `EMAIL_MDP_ENCRYPTED` avant d'envoyer
l'OTP. Pour migrer explicitement les anciennes informations SMTP et Jenkins,
lancer `sudo autokom auth migrate`. `auth status` est en lecture seule et
signale les formats historiques à migrer.

**Attention :** des copies de sauvegarde/instantanés antérieurs peuvent contenir
les anciens secrets en clair. Les protéger, appliquer leur politique de
rétention et envisager une rotation du mot de passe SMTP et du jeton Jenkins.
La migration ne peut pas effacer ces copies historiques.

Pour restaurer SMTP/Jenkins chiffrés, **restaurer aussi `vault.key` depuis une
sauvegarde séparée et protégée** ; sa perte rend les secrets chiffrés
indéchiffrables. Ne jamais afficher cette clé dans les logs, les tickets ou Git.
Les codes de secours ne doivent pas être sauvegardés en clair sur l'EC2.

## Intégration et limites

Les contrôles `authorize()` déjà branchés aux mutations AUTOKOM ne sont pas
modifiés. La passphrase AUTOKOM est une autorisation **locale**, en complément
et non en remplacement de `sudo`, de l'API Jenkins et des permissions des
services. Le courrier électronique est un canal de récupération de sécurité
moindre qu'un facteur matériel ; la politique d'accès sensible à l'infra doit
également reposer sur des contrôles d'accès système appropriés.

Les tests v30 sont exclusivement locaux : ils simulent SMTP, évaluent
chiffrement/déchiffrement, récupération hors ligne, rotation de code, blocage
OTP, compatibilité v29 et commandes d'aide. Ils ne prouvent pas la
livraison réelle d'un e-mail sur EC2 : valider `auth setup` avec un message
réellement reçu avant de déclarer l'authentification opérationnelle.
