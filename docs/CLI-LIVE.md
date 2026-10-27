> **Utilisation actuelle :** `sudo autokom install` (automatique), `autokom status`, `sudo autokom check`. Les commandes internes ci-dessous restent destinées à l’expertise.

# AUTOKOM — Ephemeral task live panel

Every long-running Ansible task produces a disposable three-line **green** area in interactive terminals (UTF-8, ANSI). A 0.3s delay avoids flickering on very quick tasks. The panel is redrawn about every 280ms, then erased before a permanent task result is printed.

When an APT task is active, the callback reads **new** local `/var/log/dpkg.log` transitions and can display installed/configured package names in real time. For other modules, Ansible does not stream underlying process stdout, so the panel displays current task and an elapsed timer; these messages do **not** claim nonexistent command output. `--check` is always labeled a simulation.

In a CI job, non-TTY output, `TERM=dumb`, `NO_COLOR` environment, or `--no-live` mode, the temporary panel is disabled automatically. The permanent lines stay readable without ANSI animation.

The callback no longer writes raw results to the AUTOKOM journal. In particular it will **not** serialize gathered facts, SSH public keys, environment variables, module return payloads, or `no_log` task output. Logs contain task metadata (title, status) and limited redacted failure explanations.

Usage:

```bash
# Kali: regression tests without Ansible installed
python3 tests/test_cli_callback.py

# AWS: visualize before installation
sudo bash install.sh --check --update

# Real install after review
sudo bash install.sh --apply

# Plain CLI mode (stable output, no transient panel)
sudo bash install.sh --check --no-live
```

**Do not run `--apply` until `--check` is reviewed**, especially on a server that also hosts production applications. No module unrelated to the foundation is modified by the CLI upgrade.
