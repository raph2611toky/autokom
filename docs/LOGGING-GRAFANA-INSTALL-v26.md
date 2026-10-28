# AUTOKOM v26 — Grafana log provisioning at installation time

## Target / contract

- One shared dashboard provider (`/etc/grafana/provisioning/dashboards/autokom.yaml`), same directory and folder as the existing observability module. Do not register another provider under a new name.
- One Loki data source (`uid: autokom-loki`, base URL `http://127.0.0.1:3100`). This URL is resolved **on the Grafana server**, not in the browser.
- Four dashboards: `autokom-logs`, `autokom-logging-health`, `autokom-logging-security`, `autokom-logging-cicd`.
- Identical dashboards, datasource and verification for Docker and native systemd execution of Loki/Alloy.
- No overwrite of an operator-modified provider or AUTOKOM-managed dashboard; the install fails explicitly.

## Full install & upgrade behavior

`sudo autokom install logs` / `sudo autokom install --only logs --backend loki --runtime systemd|docker`:

1. Verify Loki/Alloy and selected runtime. If they are already healthy, reconcile Grafana **only** rather than recreate containers/reinstall packages.
2. Preflight all managed datasource/dashboard files and verify the shared provider. Reject a conflicting provider; create a missing provider without overwriting observability-owned content.
3. Write the datasource and four dashboards idempotently and record checksums.
4. When files have changed or the datasource is demonstrably missing from the active local SQLite database, restart **only** `grafana-server` once, with activity feedback. If unchanged and datasource is confirmed, skip restart. Wait for Grafana's health endpoint after a restart.
5. Preserve all previously configured Alertmanager, Prometheus, Jenkins and business containers.

The Grafana dashboard file provider polls its directory every 30 seconds. Grafana can also be reloaded through the administrator provisioning API using Grafana administrator **basic authentication** (not a service-account bearer token). AUTOKOM currently uses the checked, single Grafana restart path; this does not require storing administrator credentials.

**Do not** equate a query accepted by Loki with Grafana correctly displaying the panel. If the Grafana local SQLite file has no dashboards while the user interface shows dashboards, the status is **inconclusive** (possibly another database or installation). Use a Grafana API service-account token with adequate read/query permissions to check the effective datasource and panels. The CLI never prints token values or log lines.

## Useful checks

```bash
sudo autokom install --only logs --backend loki --runtime systemd --check
sudo autokom install --only logs --backend loki --runtime systemd
sudo autokom logs grafana --check
sudo autokom logs verify
autokom logs audit
```

For Docker use `--runtime docker` instead of `systemd`, retaining `--backend loki` on a small instance. Do not use `both` on a host that fails the RAM preflight.

## Explanation for CI/CD dashboard

A native Jenkins service emits logs with `source=systemd` and `unit=jenkins.service`. A `Jenkins Docker`-only query has no data unless Jenkins is actually running in Docker. The CI/CD dashboard includes Jenkins systemd logs and an unfiltered Docker application log panel, with a separate metric panel where **zero** means no matching errors in five minutes. An empty logs panel still means no matching log lines, not proof of a pipeline failure. The original Prometheus/Grafana service metrics remain unchanged.

## Limitations / testing

Automated tests use controlled mocks and a local SQLite fixture, not the user's EC2. This change does not demonstrate that all dashboards were loaded in the user's Grafana instance. Final validation requires a real Grafana datasource query and a panel inspection, ideally via scoped API token, alongside a positive canary. Installation may briefly interrupt Grafana dashboards and Grafana alert evaluation during its one permitted restart. If the provisioning provider is owned by an operator and conflicts with AUTOKOM's path, the install refuses modification.
