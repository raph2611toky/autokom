# AUTOKOM

Plateforme DevSecOps autonome, installee de facon reproductible sur serveur Linux.

pour l'installation:
```bash
curl -fsSL https://raw.githubusercontent.com/raph2611toky/autokom/main/install.sh | sudo bash -s -- --apply
```

## Phase 1 — Foundation

- `scripts/bootstrap/00-preflight.sh` : inspection non destructive
- `scripts/bootstrap/01-install-ansible.sh` : bootstrap APT / Ansible
- `scripts/install/02-install-foundation.sh` : lance Ansible (check ou apply)
- `ansible/roles/base_cli` : commandes systeme/reseau, Nginx, Certbot
- `ansible/roles/security_cli` : Nikto, Nmap, Lynis, Trivy, Semgrep
- `scripts/security/03-nginx-catchall.sh` : securite vhost par defaut, opt-in
- `scripts/verify/04-check-foundation.sh` : diagnostics sans scans actifs

Voir `docs/FOUNDATION.md` avant toute installation.

Les scripts `scripts/legacy` sont conserves comme references : ne pas les executer sans revue.
