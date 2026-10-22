# AUTOKOM professional Ansible stdout callback (ansible-core 2.x).
# Installation: ANSIBLE_STDOUT_CALLBACK=autokom
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

from ansible.plugins.callback import CallbackBase

DOCUMENTATION = r'''
    name: autokom
    type: stdout
    short_description: Concise, actionable AUTOKOM installer output
    description:
      - Displays one status line per completed task.
      - Keeps detailed Ansible task results in a private installer log.
    requirements:
      - Set as stdout_callback using ANSIBLE_STDOUT_CALLBACK or ansible.cfg.
'''


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "stdout"
    CALLBACK_NAME = "autokom"
    CALLBACK_NEEDS_WHITELIST = False

    def __init__(self):
        super().__init__()
        self.index = 0
        self.current = ""
        self.check_mode = os.environ.get("AUTOKOM_CHECK_MODE", "0") == "1"
        self.verbose = os.environ.get("AUTOKOM_VERBOSE", "0") == "1"
        self.color = (os.isatty(1) and not os.environ.get("NO_COLOR")
                      and os.environ.get("TERM", "") != "dumb")
        self.counts = {"ok": 0, "changed": 0, "skipped": 0, "warn": 0, "failed": 0, "unreachable": 0}
        self.log_file = os.environ.get("AUTOKOM_LOG_FILE", "")

    def _paint(self, text, code):
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def _log(self, tag, payload):
        if not self.log_file:
            return
        try:
            with open(self.log_file, "a", encoding="utf-8") as file:
                file.write(f"{datetime.now(timezone.utc).isoformat()} [{tag}] {payload}\n")
        except OSError:
            pass

    def _line(self, symbol, label, status, color):
        title = re.sub(r"\s+", " ", self.current).strip()
        if len(title) > 70:
            title = title[:67] + "..."
        result = self._paint(status, color)
        self._display.display(f"  {self._paint(symbol, color)}  [{self.index:02d}] {title}  {result}")
        self._log(status, title)

    def v2_playbook_on_play_start(self, play):
        self._display.display("\n  " + self._paint("Fondation · Ansible", "1;36"))
        self._log("PLAY", play.get_name())

    def v2_playbook_on_task_start(self, task, is_conditional):
        self.index += 1
        self.current = task.get_name().strip()

    def v2_runner_on_ok(self, result):
        data = result._result
        changed = bool(data.get("changed", False))
        self.counts["changed" if changed else "ok"] += 1
        status = "PRÉVU" if changed and self.check_mode else "MODIFIÉ" if changed else "PRÉSENT"
        self._line("+" if changed else "✓", "", status, "33" if changed else "32")
        if self.verbose and changed:
            self._display.display("       " + str(data.get("msg", ""))[:180])
        self._safe_details(result, status)

    def v2_runner_on_skipped(self, result):
        self.counts["skipped"] += 1
        self._log("SKIPPED", self.current)
        if self.verbose:
            self._line("-", "", "IGNORÉ", "90")

    def v2_runner_on_failed(self, result, ignore_errors=False):
        if ignore_errors:
            self.counts["warn"] += 1
            self._line("!", "", "AVERTISSEMENT", "33")
        else:
            self.counts["failed"] += 1
            self._line("✗", "", "ÉCHEC", "31")
        self._safe_details(result, "FAIL")
        data = result._result
        if not data.get("_ansible_no_log"):
            message = str(data.get("msg") or data.get("stderr") or "Consulter le journal")
            self._display.display("       " + message[:260])
        if ignore_errors:
            self._display.display("       (erreur ignorée par la tâche Ansible)")

    def v2_runner_on_unreachable(self, result):
        self.counts["unreachable"] += 1
        self._line("✗", "", "INJOIGNABLE", "31")
        self._safe_details(result, "UNREACHABLE")

    def _safe_details(self, result, status):
        data = result._result
        if data.get("_ansible_no_log"):
            self._log(status, "Détails masqués : tâche no_log")
            return
        # Log details only in the private log; respect Ansible no_log.
        # Skip invocation to reduce the risk of recording command arguments.
        cleaned = {key: value for key, value in data.items()
                   if key != "invocation" and not key.startswith("_ansible_")}
        try:
            self._log("DETAIL", json.dumps(cleaned, ensure_ascii=False, default=str)[:100000])
        except Exception:
            self._log("DETAIL", "(non sérialisable)")

    def v2_playbook_on_stats(self, stats):
        summary = " · ".join(f"{key} {value}" for key, value in self.counts.items())
        self._display.display("\n  " + self._paint("Résultat : ", "1") + summary)
        self._log("RECAP", summary)
        if self.log_file:
            self._display.display("  Journal : " + self.log_file)
