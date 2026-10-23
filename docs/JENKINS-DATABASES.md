# AUTOKOM — Phase 2: Jenkins and Databases

## Objective

One menu: arrow navigation, Enter or direct keys 1–9. Can be launched from a local checkout or an installer obtained through `curl | bash`. Interactive prompts explicitly use `/dev/tty`. In CI, use `--non-interactive` with complete choices.

## Commands

```bash
sudo bash scripts/install/05-services.sh --check
sudo bash scripts/install/05-services.sh --apply
```

Optional root installer integration:

```bash
sudo bash install.sh --check --services
sudo bash install.sh --apply --services
```

Non-interactive example:

```bash
sudo bash scripts/install/05-services.sh --check \
  --non-interactive --db postgresql --bind private --allow 10.0.0.0/24 \
  --docker-access no --domain jenkins.example.com \
  --tls certbot --email admin@example.com
```

## What is configured

- Installs Jenkins LTS with Java 21 if not installed. **Does not overwrite existing Jenkins accounts or its initialAdminPassword**.
- If a domain is selected, installs a first-run Groovy initializer that sets the Jenkins canonical URL only if it is unset/default; preserves any custom URL already configured.
- Explicit opt-in for `jenkins` Docker group (root-equivalent privilege). Process restart via Jenkins systemd override to listen on loopback.
- Creates Nginx reverse proxy; ACME issued via Certbot when requested and DNS is resolvable; imported custom certificate/key checked; existing unmanaged vhosts are **never overwritten**.
- PostgreSQL: `autokom_apps` / `autokom_app`; `pg_hba.conf` rules restricted to allowed CIDR, SCRAM, and last-match reject for the managed application user.
- MySQL: `autokom_apps` / `autokom_app`; bind plus matching host grants, socket-root authentication required. It does **not** change the MySQL administrator password.
- Oracle: official Oracle Database Free lite container, only on machines with at least **12 GiB RAM**; local-only port 1521, persistent volume. Resource guard blocks on the current 7.6 GiB AWS instance. Availability and image license/terms must be checked before using in production.

## Security and scope

- MySQL / PostgreSQL listen-address is a **LOCAL server bind setting**; client CIDRs control DB account access. An AWS Security Group, GCP firewall, or host firewall must ALSO restrict network access. AUTOKOM does not silently alter firewall rules, public Cloud permissions, or existing services other than the selected Jenkins restart.
- Database credentials are generated into `/etc/autokom/secrets` (`0700` directory, `0600` files) and should be imported into an external secret vault when deploying production services. Never put them in GitHub.
- Do not use `--bind all` without explicit `--unsafe-all` and a conscious firewall configuration.
- Existing MySQL/PostgreSQL users are **not** reset. Provisioning and account-management require functional root-socket or postgres peer access.
- A Jenkins installation which is already set up keeps its current security realm, jobs, admin users and plugins. New installations use the official initial setup wizard. Initial password retrieval: `sudo cat /var/lib/jenkins/secrets/initialAdminPassword`. Advanced Jenkins Configuration-as-Code and automatic plugin baselines belong to the next component; they are not silently simulated.
- `--check` uses Ansible dry-run and performs no TLS, database or Jenkins changes. It also checks local paths and required environment before applying.
- Existing unmanaged Nginx vhosts are preserved. For a domain already used by Jenkins, AUTOKOM does not override the configuration.
- A restart of Jenkins or a DB listener can interrupt current connections. Run `--apply` during a maintenance window when protecting an existing production service.
- Existing DBs and Nginx vhosts must be backed up before using this beta patch in production.

## Re-run behavior

- Reuses existing accounts and password files.
- Jenkins overrides are modified only if needed.
- Reuses existing Nginx vhosts without overwriting Certbot-modified TLS configuration.
- The installer does not delete packages, reset Jenkins admin users, destroy databases, or prune Docker volumes.

## Verification

```bash
sudo systemctl status jenkins --no-pager
sudo ss -lntp | grep -E ':(8080|5432|3306|1521)'
sudo nginx -t
sudo systemctl status postgresql mysql --no-pager
sudo id jenkins
python3 tests/test_phase2.py
```

## Known limits

Oracle Free container initial boot may require several minutes and registry use terms; this phase does not programmatically provision Oracle accounts/schema. The stack is not yet validated on the user's exact Ubuntu 26.04.1 host. Jenkins plugin installation and JCasC-based initial admin provisioning are not yet completed. DNS alone cannot guarantee ACME HTTP-01 inbound port 80 reachability; the Certbot command will validate it and report an error otherwise.

## IMPORTANT — existing production environments

If the server already hosts business applications (as in this instance), preserve an EC2 snapshot and database backups. AUTOKOM never opens AWS Security Groups. An incoming domain must resolve to the public interface for ACME. Plan a Jenkins maintenance window because binding it to loopback restarts it; changing PostgreSQL/MySQL `bind-address` may also require restarts.
