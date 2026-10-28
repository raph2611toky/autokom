#!/usr/bin/env python3
"""Local authorization gate for AUTOKOM. Not a substitute for Unix root or backend RBAC.

Passwords are salted PBKDF2-HMAC-SHA256 (600k iterations); OTPs expire and are
single-use. SMTP needs TLS and a separately protected delivery credential.
"""
from __future__ import annotations
import argparse
import contextlib
import fcntl
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import smtplib
import ssl
import tempfile
import sys
from cli_colors import pretty_print as print
import time
from email.message import EmailMessage

BASE = Path('/etc/autokom/security')
STATE = BASE / 'auth.json'
SMTP = BASE / 'smtp.json'
OTP = BASE / 'recovery.json'
ATTEMPTS = BASE / 'attempts.json'
JENKINS = BASE / 'jenkins-api.auth'
ROUNDS = 600_000
EMAIL = re.compile(r'^[A-Za-z0-9.!#$%&\'*+/?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\.[A-Za-z]{2,63}$')

class SecurityError(RuntimeError):
    pass

def require_root():
    if os.geteuid()!=0:raise SecurityError('Authentification sensible : utiliser sudo (accès local privilégié requis)')

def _dir():
    if BASE.is_symlink():raise SecurityError('Répertoire de sécurité symbolique refusé')
    BASE.mkdir(parents=True, exist_ok=True, mode=0o700)
    if BASE.stat().st_uid!=0 and os.geteuid()==0:raise SecurityError('Répertoire de sécurité non détenu par root')
    BASE.chmod(0o700)

def _read(path,default=None):
    try:
        if path.is_symlink():raise SecurityError('Chemin de sécurité symbolique refusé')
        if os.geteuid()==0 and (path.stat().st_uid!=0 or path.stat().st_mode & 0o077):
            raise SecurityError('Permissions de sécurité incorrectes : '+str(path))
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:return default
    except (UnicodeError,json.JSONDecodeError) as exc:raise SecurityError('Fichier de sécurité invalide : '+str(path)) from exc

def _write(path,obj):
    require_root();_dir()
    if path.is_symlink():raise SecurityError('Écriture vers symlink refusée')
    fd,tmp=tempfile.mkstemp(dir=BASE, prefix='.autokom-', text=True)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(obj,f,ensure_ascii=False,sort_keys=True)
            f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def _tty_secret(label):
    try:
        with open('/dev/tty','r+') as tty:return getpass.getpass(label+': ',stream=tty)
    except (OSError,EOFError) as exc:raise SecurityError('Terminal privé requis (pas de secret dans les arguments)') from exc

def _tty_text(label):
    try:
        with open('/dev/tty','r+') as tty:
            tty.write('  '+label+': ');tty.flush()
            return tty.readline().strip()
    except OSError as exc:raise SecurityError('Terminal interactif requis') from exc

def _password_new():
    first=_tty_secret('Passphrase AUTOKOM (14 caractères minimum)')
    second=_tty_secret('Confirmer la passphrase AUTOKOM')
    if not secrets.compare_digest(first,second):raise SecurityError('Les passphrases ne correspondent pas')
    if len(first)<14 or len(first)>512:raise SecurityError('Longueur de passphrase attendue : 14 à 512 caractères')
    return first

def _hash(pwd):
    salt=secrets.token_bytes(16)
    digest=hashlib.pbkdf2_hmac('sha256',pwd.encode(),salt,ROUNDS)
    return {'algorithm':'pbkdf2-sha256','rounds':ROUNDS,'salt':salt.hex(),'hash':digest.hex()}

def _verify(pwd,obj):
    if obj.get('algorithm')!='pbkdf2-sha256' or not (600_000 <= obj.get('rounds',0) <= 2_000_000):
        raise SecurityError('Format de hash AUTOKOM invalide')
    try:
        actual=hashlib.pbkdf2_hmac('sha256',pwd.encode(),bytes.fromhex(obj['salt']),obj['rounds'])
        return hmac.compare_digest(actual,bytes.fromhex(obj['hash']))
    except (KeyError,ValueError,TypeError) as exc:raise SecurityError('Hash AUTOKOM invalide') from exc

@contextlib.contextmanager
def _lock():
    require_root();_dir()
    file=BASE/'lock'
    if file.is_symlink():raise SecurityError('Verrou sécurité invalide')
    fd=os.open(file,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)

def configured():return _read(STATE) is not None

def authorize(action='opération sensible', *, require_setup=True):
    """Root + passphrase on every invocation; no auth session or env bypass."""
    require_root()
    with _lock():
        state=_read(STATE)
        if not state:
            if require_setup:raise SecurityError('AUTOKOM auth non configuré : sudo autokom auth setup')
            return False
        failed=_read(ATTEMPTS,{'failures':0,'blocked_until':0})
        now=time.time()
        if now<float(failed.get('blocked_until',0)):
            raise SecurityError('Trop de tentatives : temporairement bloqué')
        attempt=_tty_secret('Autorisation AUTOKOM · '+action)
        if not _verify(attempt,state['passphrase']):
            count=int(failed.get('failures',0))+1
            _write(ATTEMPTS,{'failures':count,'blocked_until':now+900 if count>=5 else 0})
            raise SecurityError('Passphrase incorrecte')
        _write(ATTEMPTS,{'failures':0,'blocked_until':0})
        return True

def _mail_code(address):
    conf=_read(SMTP)
    if not conf:raise SecurityError('SMTP non configuré : sudo autokom auth smtp')
    code=f'{secrets.randbelow(1_000_000):06d}'
    salt=secrets.token_bytes(16)
    _write(OTP,{'salt':salt.hex(),'hash':hashlib.sha256(salt+code.encode()).hexdigest(),
                'email':address,'expires_at':time.time()+600,'attempts':0})
    msg=EmailMessage()
    msg['From']=conf['sender'];msg['To']=address;msg['Subject']='AUTOKOM · Code de vérification'
    msg.set_content('Code AUTOKOM : '+code+'\nValable 10 minutes et une seule fois.\n')
    try:
        with smtplib.SMTP(conf['host'],int(conf['port']),timeout=12) as server:
            server.ehlo();server.starttls(context=ssl.create_default_context());server.ehlo()
            if conf.get('username'):server.login(conf['username'],conf['password'])
            server.send_message(msg)
    except (OSError,smtplib.SMTPException,ssl.SSLError) as exc:
        try:OTP.unlink(missing_ok=True)
        except OSError:pass
        raise SecurityError('Envoi OTP SMTP TLS impossible : aucun e-mail vérifié') from exc

def _check_code(address):
    with _lock():
        record=_read(OTP)
        if not record or record.get('email')!=address or time.time()>record.get('expires_at',0):
            raise SecurityError('Code absent ou expiré')
        if record.get('attempts',0)>=5:raise SecurityError('Trop de tentatives OTP')
        code=_tty_secret('Code reçu par e-mail')
        actual=hashlib.sha256(bytes.fromhex(record['salt'])+code.encode()).hexdigest()
        if not hmac.compare_digest(actual,record['hash']):
            record['attempts']+=1;_write(OTP,record)
            raise SecurityError('Code incorrect')
        OTP.unlink()

def setup():
    require_root()
    with _lock():
        if configured():raise SecurityError('Authentification déjà configurée : utiliser change ou reset')
    email=_tty_text('Adresse e-mail de récupération')
    email2=_tty_text('Confirmer l’adresse e-mail')
    if email!=email2 or not EMAIL.fullmatch(email):raise SecurityError('Adresse e-mail incorrecte ou confirmation différente')
    pwd=_password_new()
    _mail_code(email)
    print('  › Code envoyé via SMTP TLS ; vérification requise avant activation')
    _check_code(email)
    with _lock():
        if configured():raise SecurityError('Configuration concurrente : refus')
        _write(STATE,{'version':1,'email':email,'email_verified':True,'passphrase':_hash(pwd)})
    print('  ✓ AUTOKOM auth activée · passphrase hachée · e-mail vérifié')

def smtp_setup():
    require_root()
    if configured():authorize('modifier le serveur SMTP')
    host=_tty_text('Serveur SMTP (DNS)')
    port=_tty_text('Port SMTP STARTTLS [587]') or '587'
    sender=_tty_text('Adresse expéditeur')
    username=_tty_text('Identifiant SMTP (vide si aucun)')
    if not re.fullmatch(r'[A-Za-z0-9.-]{1,253}',host) or not port.isdecimal() or not 1<=int(port)<=65535 or not EMAIL.fullmatch(sender):
        raise SecurityError('Configuration SMTP invalide')
    password=_tty_secret('Mot de passe SMTP (séparé de la passphrase AUTOKOM)') if username else ''
    # Test STARTTLS and authentication before saving the delivery credentials.
    try:
        with smtplib.SMTP(host,int(port),timeout=12) as server:
            server.ehlo();server.starttls(context=ssl.create_default_context());server.ehlo()
            if username:server.login(username,password)
    except (OSError,smtplib.SMTPException,ssl.SSLError) as exc:raise SecurityError('SMTP STARTTLS : test échoué ; configuration non enregistrée') from exc
    _write(SMTP,{'host':host,'port':int(port),'sender':sender,'username':username,'password':password})
    print('  ✓ SMTP TLS configuré ; identifiants SMTP root:0600 (nécessaires à l’envoi)')

def recovery():
    require_root()
    with _lock():
        state=_read(STATE)
        if not state or not state.get('email_verified'):raise SecurityError('E-mail de récupération non vérifié')
        record=_read(OTP,{})
        addr=state['email']
        needs_send=not (record and time.time()<record.get('expires_at',0) and record.get('email')==addr)
        if not needs_send and record.get('attempts',0)>=5:raise SecurityError('Trop de tentatives OTP : attendre expiration')
    if needs_send:
        _mail_code(addr)
        print('  › Code envoyé à l’adresse configurée (adresse non affichée)')
    else:
        print('  › Utiliser le code envoyé précédemment (valable 10 minutes)')
    _check_code(addr)
    pwd=_password_new()
    with _lock():
        state=_read(STATE)
        state['passphrase']=_hash(pwd)
        _write(STATE,state)
        _write(ATTEMPTS,{'failures':0,'blocked_until':0})
    print('  ✓ Passphrase renouvelée · code OTP consommé')

def set_jenkins_credential():
    authorize('enregistrer le jeton API Jenkins')
    username=_tty_text('Identifiant administrateur Jenkins')
    token=_tty_secret('Jeton API Jenkins (jamais la passphrase AUTOKOM)')
    if not re.fullmatch(r'[A-Za-z0-9_.@-]{1,150}',username) or not token or '\n' in token or ':' in username:
        raise SecurityError('Identifiant ou jeton Jenkins invalide')
    _dir()
    fd,temp=tempfile.mkstemp(dir=BASE,prefix='.jenkins-')
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w') as f:
            f.write(username+':'+token+'\n');f.flush();os.fsync(f.fileno())
        os.replace(temp,JENKINS)
    finally:
        if os.path.exists(temp):os.unlink(temp)
    print('  ✓ Jeton API Jenkins mémorisé dans un coffre local root:0600 (non haché : Jenkins nécessite son secret)')

def main(argv=None):
    p=argparse.ArgumentParser(prog='autokom auth',description='Passphrase locale + e-mail OTP de récupération (SMTP TLS requis)')
    sub=p.add_subparsers(dest='action')
    for action,helptext in [('status','État sans secrets'),('smtp','Configurer le transport SMTP STARTTLS'),('setup','Créer passphrase et vérifier e-mail par OTP'),('verify','Vérifier la passphrase'),('change','Changer la passphrase'),('reset','Récupération par OTP e-mail'),('jenkins-token','Mémoriser une fois le jeton API Jenkins')]:
        sub.add_parser(action,help=helptext)
    args=p.parse_args(argv)
    if args.action is None:
        if not sys.stdin.isatty():p.print_help();return 0
        require_root()
        from menu import pick
        while True:
            if configured():
                choices=[('status','Afficher l’état de sécurité'),('verify','Vérifier la passphrase'),
                         ('change','Changer la passphrase'),('reset','Passphrase oubliée : OTP e-mail'),
                         ('jenkins-token','Enregistrer le jeton Jenkins une fois'),('smtp','Configurer le transport SMTP')]
            else:
                choices=[('smtp','Configurer SMTP TLS (requis pour OTP)'),
                         ('setup','Initialiser passphrase et vérifier l’e-mail'),('status','Afficher l’état actuel')]
            selected=pick('AUTOKOM · AUTHENTIFICATION CENTRALISÉE', [c[1] for c in choices],
                          cancel_label='Quitter sans modifier',hint='↑/↓ · Entrée · chiffres · 0/q pour annuler')
            if not selected:return 0
            try:main([choices[selected-1][0]])
            except SecurityError as exc:print('  ✗ '+str(exc))
        return 0
    if args.action=='status':
        require_root()
        state=_read(STATE,{})
        print('  ◈ AUTOKOM / AUTHENTIFICATION')
        print('  '+('✓ Configurée' if state else '! Non configurée'))
        print('  '+('✓ E-mail confirmé' if state.get('email_verified') else '! E-mail non vérifié'))
        print('  '+('✓ SMTP prêt (configuration)' if SMTP.exists() else '! SMTP absent'))
        print('  '+('✓ Jeton Jenkins protégé' if JENKINS.exists() else '› Jeton Jenkins non configuré'))
        return 0
    if args.action=='smtp':smtp_setup()
    elif args.action=='setup':setup()
    elif args.action=='verify':authorize('vérification manuelle')
    elif args.action=='change':
        authorize('changer la passphrase');state=_read(STATE);state['passphrase']=_hash(_password_new());_write(STATE,state);print('  ✓ Passphrase renouvelée')
    elif args.action=='reset':recovery()
    elif args.action=='jenkins-token':set_jenkins_credential()
    return 0
