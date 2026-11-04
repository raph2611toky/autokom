#!/usr/bin/env python3
"""Temporary AUTOKOM animated progress, last three sanitized real command lines.

Complete output is protected in a root-only file. No process runs in background.
"""
from __future__ import annotations
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

LOG_DIR=Path('/var/log/autokom/platform')
FRAMES=('⠋','⠙','⠹','⠸','⠼','⠴','⠦','⠧','⠇','⠏')
SECRET=re.compile(r'(?i)(authorization|password|passwd|secret|token|private.?key|credential|bearer|cookie|api.?key|jwt|^\s*set-cookie|^\s*--header)')
ANSI=re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))')

class LiveDomainProgress:
    def __init__(self):
        self.tty=sys.stdout.isatty()
        self.visible=False
        self.start=time.monotonic()
        self.frame=0
        self.log_path=None
        self.preview_start=0
        self.label='Préparation'
    def __enter__(self):
        LOG_DIR.mkdir(parents=True,exist_ok=True,mode=0o700)
        os.chmod(LOG_DIR,0o700)
        stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
        self.log_path=LOG_DIR/f'domains-migrate-{stamp}-{os.getpid()}-{secrets.token_hex(3)}.log'
        fd=os.open(self.log_path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as f:f.write('AUTOKOM · domain migration · '+stamp+'\n')
        return self
    def __exit__(self,*args):
        self.clear()
        print('  › Journal détaillé : '+str(self.log_path))
    def note(self,message):
        with self.log_path.open('a',encoding='utf-8') as f:f.write('\n'+message+'\n')
    def _lines(self):
        try:
            with self.log_path.open('rb') as f:
                f.seek(0,os.SEEK_END)
                end=f.tell()
                f.seek(max(self.preview_start,end-12000))
                rows=f.read().decode('utf-8','replace').splitlines()
        except OSError:return []
        result=[]
        for line in rows:
            safe=ANSI.sub('',line)
            safe=''.join(ch if ch.isprintable() else ' ' for ch in safe).strip()
            if not safe:continue
            if SECRET.search(safe):safe='sortie masquée (information sensible)'
            result.append(safe)
        return result[-3:]
    def paint(self):
        if not self.tty:return
        width=max(35,shutil.get_terminal_size((92,24)).columns)
        rows=self._lines() or ['Vérification en cours…']
        rows=['']*(3-len(rows))+rows[-3:]
        status=f'  {FRAMES[self.frame%len(FRAMES)]} {self.label} · {int(time.monotonic()-self.start)}s'[:width-1]
        self.frame+=1
        if self.visible:sys.stdout.write('\r\x1b[3A')
        out=[status]+['\x1b[32m  │ '+line[:max(1,width-8)]+'\x1b[0m' for line in rows]
        sys.stdout.write('\r\x1b[2K'+'\n\r\x1b[2K'.join(out))
        sys.stdout.flush()
        self.visible=True
    def clear(self):
        if not self.tty or not self.visible:return
        sys.stdout.write('\r\x1b[3A')
        for i in range(4):
            sys.stdout.write('\r\x1b[2K')
            if i<3:sys.stdout.write('\n')
        sys.stdout.write('\r\x1b[3A')
        sys.stdout.flush()
        self.visible=False
    def call(self,args,timeout=30):
        """Keep subprocess stdout off TTY but stream exact output into protected log."""
        self.note('['+datetime.now(timezone.utc).isoformat()+'] '+self.label+' · '+Path(args[0]).name)
        before=self.log_path.stat().st_size
        self.preview_start=before
        start=time.monotonic()
        try:
            with self.log_path.open('ab',buffering=0) as out:
                proc=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT)
                while proc.poll() is None:
                    if time.monotonic()-start>timeout:
                        proc.kill();proc.wait()
                        self.note('Timeout: '+self.label)
                        self.clear()
                        return subprocess.CompletedProcess(args,124,'','délai dépassé')
                    self.paint()
                    time.sleep(0.14)
                self.paint()
                result=proc.returncode
        except OSError as exc:
            self.note('Erreur système '+str(type(exc).__name__))
            self.clear()
            return subprocess.CompletedProcess(args,127,'','commande indisponible')
        with self.log_path.open('rb') as history:
            history.seek(0,os.SEEK_END)
            end=history.tell()
            history.seek(max(before,end-262144))
            data=history.read()
        return subprocess.CompletedProcess(args,result,data.decode('utf-8','replace')[-262144:],'')
