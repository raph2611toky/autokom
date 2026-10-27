> **Utilisation actuelle :** `sudo autokom install` (automatique), `autokom status`, `sudo autokom check`. Les commandes internes ci-dessous restent destinées à l’expertise.

# AUTOKOM CLI output conventions

AUTOKOM uses `scripts/lib/ui.sh` in every Bash module and
`ansible/plugins/callback/autokom.py` for Ansible output.

## Running

```bash
sudo bash install.sh --check
sudo bash install.sh --apply
sudo bash install.sh --apply --update
sudo bash install.sh --check --verbose
```

`--check` simulates the foundation playbook; Git/Ansible bootstrap and source
checkout may still be installed. `--update` is explicitly opt-in and requires
an unchanged Git worktree. No automatic update happens when rerunning installer.

Logs are created with mode 0600 under `/var/log/autokom/`, visible to root.
The installation process never writes application secrets intentionally; every
new Ansible task handling credentials must set `no_log: true`.

Ansible CLI callback design:
- ✓ PRÉSENT: already conforms to expected state
- + PRÉVU: would change in `--check`
- + MODIFIÉ: changed in `--apply`
- ! AVERTISSEMENT: failed, but task marked `ignore_errors`
- ✗ ÉCHEC: actual failure
- skipped tasks hidden in normal output (counted in recap)

No synthetic completion percentages are shown for dynamic Ansible playbooks.
`--verbose` shows skipped tasks and additional optional messages.

Use the shared UI functions in all future modules: `ak_init`, `ak_section`,
`ak_kv`, `ak_info`, `ak_ok`, `ak_warn`, `ak_err`, `ak_run`, `ak_finish`.

For staging/production, use tagged Git releases, signature/checksum verification,
and avoid blindly executing changing `main` over `curl | sudo bash`.
