"""AUTOKOM stdout callback: compact final statuses + ephemeral live 3-line panel.

The live panel reads genuine dpkg activity when a local apt task is running.
Other Ansible modules normally return output only when a task finishes; this
callback therefore shows an honest elapsed timer instead of invented logs.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import threading
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from ansible.plugins.callback import CallbackBase

DOCUMENTATION = r'''
    name: autokom
    type: stdout
    short_description: AUTOKOM installer with transient live operation panels
    description:
      - Prints one permanent status line per completed task.
      - Shows three disposable green lines for running tasks in interactive terminals.
      - Watches genuine local dpkg events for APT operations.
      - Records concise metadata instead of entire Ansible result objects.
'''


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "stdout"
    CALLBACK_NAME = "autokom"
    CALLBACK_NEEDS_WHITELIST = False

    GREEN = "\033[32m"
    RESET = "\033[0m"
    ERASE = "\033[2K"
    UP3 = "\033[3A"
    SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self):
        super().__init__()
        self.index = 0
        self.current = ""
        self.current_action = ""
        self.check_mode = os.environ.get("AUTOKOM_CHECK_MODE", "0") == "1"
        self.verbose = os.environ.get("AUTOKOM_VERBOSE", "0") == "1"
        self.log_file = os.environ.get("AUTOKOM_LOG_FILE", "")
        self.color = bool(
            sys.stdout.isatty() and os.environ.get("TERM", "") not in ("", "dumb")
            and "NO_COLOR" not in os.environ
        )
        self.live = self.color and os.environ.get("AUTOKOM_LIVE", "1") != "0" and not os.environ.get("CI")
        self.counts = {"ok": 0, "changed": 0, "skipped": 0,
                       "warn": 0, "failed": 0, "unreachable": 0}
        self._guard = threading.RLock()
        self._stop = None
        self._thread = None
        self._panel_drawn = False
        self._started = 0.0
        self._last_dpkg = ""
        self._dpkg_position = None

    @staticmethod
    def _clean(value):
        """Strip terminal control characters from untrusted text."""
        line = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(value))
        return re.sub(r"\s+", " ", line).strip()

    @staticmethod
    def _width(text, max_columns):
        """Clip by terminal cell width, not by UTF-8 byte length."""
        used, output = 0, []
        for char in text:
            cells = 0 if unicodedata.combining(char) else (2 if unicodedata.east_asian_width(char) in "WF" else 1)
            if used + cells > max_columns:
                break
            output.append(char)
            used += cells
        return "".join(output)

    def _paint(self, message, color):
        return f"\033[{color}m{message}{self.RESET}" if self.color else message

    def _log(self, tag, description):
        if not self.log_file:
            return
        try:
            with open(self.log_file, "a", encoding="utf-8") as handle:
                handle.write(f"{datetime.now(timezone.utc).isoformat()} [{tag}] {self._clean(description)[:300]}\n")
        except OSError:
            pass

    def _task_label(self):
        return self._clean(self.current)[:72]

    def _display_status(self, symbol, status, code):
        title = self._task_label()
        with self._guard:
            self._display.display(f"  {self._paint(symbol, code)}  [{self.index:02d}] {title}  {self._paint(status, code)}")
        self._log(status, f"[{self.index:02d}] {title}")

    def _read_dpkg(self):
        """Read only NEW dpkg transitions of an active local APT task."""
        if self.check_mode or "apt" not in self.current_action:
            return
        path = Path("/var/log/dpkg.log")
        try:
            size = path.stat().st_size
            if self._dpkg_position is None:
                self._dpkg_position = size
            if size < self._dpkg_position:
                self._dpkg_position = size
                return
            if size == self._dpkg_position:
                return
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(self._dpkg_position)
                new_text = handle.read(min(size - self._dpkg_position, 16384))
                self._dpkg_position = handle.tell()
            for line in new_text.splitlines():
                parts = line.split()
                if len(parts) < 5:
                    continue
                operation = parts[2]
                if operation == "status" and parts[3] == "installed":
                    self._last_dpkg = f"Paquet installé : {parts[4]}"
                elif operation in ("install", "upgrade", "configure", "remove"):
                    self._last_dpkg = f"dpkg : {operation} {parts[3]}"
        except (OSError, ValueError):
            pass

    def _live_rows(self, tick):
        elapsed = max(0, int(time.monotonic() - self._started))
        mm, ss = divmod(elapsed, 60)
        progress = self._last_dpkg or (
            "Simulation : calcul des changements (aucun paquet installé)"
            if self.check_mode else
            "Traitement en cours ; attente du résultat Ansible"
        )
        return [
            f"  {self.SPINNER[tick % len(self.SPINNER)]}  [{self.index:02d}] {self._task_label()}",
            f"  ↳  {progress}",
            f"  ↳  Temps écoulé : {mm:02d}:{ss:02d} · actualisation en direct",
        ]

    def _draw_panel(self, rows):
        cols = max(20, shutil.get_terminal_size(fallback=(100, 24)).columns - 1)
        with self._guard:
            if self._panel_drawn:
                sys.stdout.write(self.UP3)
            for text in rows:
                safe = self._width(self._clean(text), cols)
                sys.stdout.write(f"\r{self.ERASE}{self.GREEN}{safe}{self.RESET}\n")
            sys.stdout.flush()
            self._panel_drawn = True

    def _erase_panel(self):
        with self._guard:
            if not self._panel_drawn:
                return
            sys.stdout.write(self.UP3)
            for _ in range(3):
                sys.stdout.write(f"\r{self.ERASE}\n")
            sys.stdout.write(self.UP3)
            sys.stdout.flush()
            self._panel_drawn = False

    def _live_worker(self, event):
        # Quick tasks produce only their final status; no UI flicker.
        if event.wait(0.3):
            return
        tick = 0
        try:
            while not event.is_set():
                self._read_dpkg()
                self._draw_panel(self._live_rows(tick))
                tick += 1
                if event.wait(0.28):
                    break
        finally:
            # The main callback is responsible for clearing the panel.
            pass

    def _start_task(self):
        self._stop_task()
        self._started = time.monotonic()
        self._last_dpkg = ""
        self._dpkg_position = None
        if "apt" in self.current_action:
            try:
                self._dpkg_position = Path("/var/log/dpkg.log").stat().st_size
            except OSError:
                pass
        if self.live:
            self._stop = threading.Event()
            self._thread = threading.Thread(target=self._live_worker, args=(self._stop,), daemon=True)
            self._thread.start()

    def _stop_task(self):
        if self._stop is not None:
            self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._thread = None
        self._stop = None
        self._erase_panel()

    def v2_playbook_on_play_start(self, play):
        self._stop_task()
        self._display.display("\n  " + self._paint("Fondation · Ansible", "1;36"))
        self._log("PLAY", play.get_name())

    def v2_playbook_on_task_start(self, task, is_conditional):
        self._stop_task()
        self.index += 1
        self.current = task.get_name().strip()
        self.current_action = self._clean(getattr(task, "action", ""))
        self._start_task()

    def v2_runner_on_ok(self, result):
        self._stop_task()
        changed = bool(result._result.get("changed", False))
        self.counts["changed" if changed else "ok"] += 1
        status = "PRÉVU" if changed and self.check_mode else "MODIFIÉ" if changed else "PRÉSENT"
        self._display_status("+" if changed else "✓", status, "33" if changed else "32")

    def v2_runner_on_skipped(self, result):
        self._stop_task()
        self.counts["skipped"] += 1
        self._log("IGNORÉ", self._task_label())
        if self.verbose:
            self._display_status("-", "IGNORÉ", "90")

    @staticmethod
    def _failure_message(data):
        if data.get("_ansible_no_log"):
            return "Détails masqués par Ansible (no_log)"
        message = str(data.get("msg") or "Voir la tâche échouée")
        message = re.sub(r"(?i)(password|secret|token|api[_-]?key|authorization)\s*[:=]\s*[^\s,;]+", r"\1=[MASQUÉ]", message)
        message = re.sub(r"(?i)Bearer\s+[^\s]+", "Bearer [MASQUÉ]", message)
        return CallbackModule._clean(message)[:180]

    def v2_runner_on_failed(self, result, ignore_errors=False):
        self._stop_task()
        self.counts["warn" if ignore_errors else "failed"] += 1
        self._display_status("!" if ignore_errors else "✗", "AVERTISSEMENT" if ignore_errors else "ÉCHEC", "33" if ignore_errors else "31")
        message = self._failure_message(result._result)
        self._display.display("       " + message)
        self._log("ERROR", f"{self._task_label()} | {message}")

    def v2_runner_on_unreachable(self, result):
        self._stop_task()
        self.counts["unreachable"] += 1
        self._display_status("✗", "INJOIGNABLE", "31")

    def v2_playbook_on_stats(self, stats):
        self._stop_task()
        summary = " · ".join(f"{key} {value}" for key, value in self.counts.items())
        self._display.display("\n  " + self._paint("Résultat : ", "1") + summary)
        self._log("RECAP", summary)
        if self.log_file:
            self._display.display("  Journal : " + self.log_file)
