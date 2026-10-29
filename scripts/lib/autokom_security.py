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
import termios
from cli_colors import pretty_print as print
import time
from email.message import EmailMessage

BASE = Path('/etc/autokom/security')
STATE = BASE / 'auth.json'
SMTP = BASE / 'smtp.json'
OTP = BASE / 'recovery.json'
ATTEMPTS = BASE / 'attempts.json'
JENKINS = BASE / 'jenkins-api.auth'
RECOVERY_CODES = BASE / 'recovery-codes.json'
VAULT_KEY = BASE / 'vault.key'
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

def _tty_open():
    """Use the same low-level /dev/tty access as AUTOKOM's working menus.

    TextIOWrapper('r+') asks whether a character device is seekable on some
    systems; it is not. Do not use open('/dev/tty', 'r+') for prompts.
    """
    try:
        fd = os.open('/dev/tty', os.O_RDWR | os.O_NOCTTY)
        if not os.isatty(fd):
            os.close(fd)
            raise SecurityError('Terminal interactif requis (TTY non détecté)')
        return fd
    except OSError as exc:
        raise SecurityError('Terminal interactif requis (accès /dev/tty impossible)') from exc


def _tty_text(label):
    # Reuse the interactive AUTOKOM input primitive, which works after pick().
    try:
        from menu import ask
        return ask(label, max_bytes=1024)
    except (OSError, RuntimeError, UnicodeError, ValueError) as exc:
        raise SecurityError('Saisie au terminal impossible : '+str(exc)) from exc


def _tty_secret(label):
    """Prompt using os.open, suppress TTY echo, restore settings on ALL exits."""
    fd = _tty_open()
    saved = None
    try:
        saved = termios.tcgetattr(fd)
        attrs = termios.tcgetattr(fd)
        attrs[3] &= ~(termios.ECHO | termios.ECHONL)
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        os.write(fd, ('  '+label+': ').encode('utf-8'))
        result = bytearray()
        while True:
            chunk = os.read(fd, 1)
            if not chunk:
                raise SecurityError('Saisie interrompue : terminal fermé')
            if chunk in (b'\r', b'\n'):
                break
            result.extend(chunk)
            if len(result) > 4096:
                raise SecurityError('Secret trop long')
        try:
            return result.decode('utf-8')
        except UnicodeError as exc:
            raise SecurityError('Encodage du secret non valide') from exc
    except OSError as exc:
        raise SecurityError('Saisie de secret impossible sur le terminal') from exc
    finally:
        if saved is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        try:
            os.write(fd, b'\r\n')
        except OSError:
            pass
        os.close(fd)


def _fernet(*, create=True):
    """Root-only envelope encryption for reusable SMTP credential.

    This is at-rest protection, not protection from compromised root.
    Keep vault.key in secured backup separately from smtp.json.
    """
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise SecurityError('Chiffrement SMTP requis : installer python3-cryptography') from exc
    require_root()
    _dir()
    if VAULT_KEY.is_symlink():
        raise SecurityError('Clé du coffre symbolique refusée')
    try:
        fd = os.open(VAULT_KEY, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        if not create:
            raise SecurityError('Clé du coffre absente : restaurer vault.key depuis la sauvegarde')
        fd = os.open(VAULT_KEY, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            key = Fernet.generate_key()
            os.write(fd, key)
            os.fsync(fd)
        finally:
            os.close(fd)
        fd = os.open(VAULT_KEY, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if info.st_uid != 0 or info.st_mode & 0o077:
            raise SecurityError('Permissions de la clé du coffre non conformes')
        key = os.read(fd, 256).strip()
    finally:
        os.close(fd)
    try:
        return Fernet(key)
    except (ValueError, TypeError) as exc:
        raise SecurityError('Clé du coffre invalide') from exc


def _protect_smtp(password):
    return _fernet().encrypt(password.encode('utf-8')).decode('ascii') if password else ''


def _smtp_password(conf):
    if 'EMAIL_MDP_ENCRYPTED' in conf:
        try:
            from cryptography.fernet import InvalidToken
            return _fernet(create=False).decrypt(conf['EMAIL_MDP_ENCRYPTED'].encode('ascii')).decode('utf-8')
        except (InvalidToken, UnicodeError, ValueError, TypeError) as exc:
            raise SecurityError('Secret SMTP chiffré invalide : clé manquante ou incorrecte') from exc
    return conf.get('EMAIL_MDP', conf.get('password', ''))


def _smtp_migrate_legacy():
    """Upgrade v28/v29 root-only SMTP secret to encrypted storage on first use.

    Caller must have root. Existing backups may still carry plaintext.
    """
    require_root()
    with _lock():
        conf = _read(SMTP)
        if not conf or 'EMAIL_MDP_ENCRYPTED' in conf:
            return conf
        legacy = conf.get('EMAIL_MDP', conf.get('password', ''))
        if legacy:
            encrypted = _protect_smtp(legacy)
            conf.pop('EMAIL_MDP', None)
            conf.pop('password', None)
            conf['EMAIL_MDP_ENCRYPTED'] = encrypted
            _write(SMTP, conf)
        return conf


def _smtp_values(conf):
    """Canonical EMAIL_* fields and backward compatibility with v28 secrets."""
    if not conf:
        raise SecurityError('SMTP non configuré : sudo autokom auth smtp')
    mapping = {
        'host': conf.get('EMAIL_HOST', conf.get('host')),
        'port': conf.get('EMAIL_PORT', conf.get('port', 587)),
        'login': conf.get('EMAIL_LOGIN', conf.get('username', '')),
        'password': _smtp_password(conf),
        'sender': conf.get('EMAIL_FROM', conf.get('sender')),
        'admin': conf.get('EMAIL_ADMIN', conf.get('admin', '')),
    }
    return mapping


def _smtp_connection(conf):
    """Enforce verified TLS: implicit TLS 465 or mandatory STARTTLS otherwise."""
    host = conf['host']
    port = int(conf['port'])
    ctx = ssl.create_default_context()
    if port == 465:
        return smtplib.SMTP_SSL(host, port, timeout=12, context=ctx)
    return smtplib.SMTP(host, port, timeout=12)


def _smtp_secure(server, conf):
    """Establish mandatory TLS before sending credentials or email."""
    server.ehlo()
    if int(conf['port']) != 465:
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
    if conf.get('login'):
        server.login(conf['login'], conf['password'])

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
    conf = _smtp_values(_smtp_migrate_legacy())
    code = f'{secrets.randbelow(1_000_000):06d}'
    with _lock():
        prev = _read(OTP, {})
        if prev.get('email') == address and time.time() < prev.get('expires_at', 0):
            raise SecurityError('OTP encore valide : utiliser le code reçu ou attendre son expiration (10 min)')
        _write(OTP, {'digest':_hash(code), 'email':address,
                     'created_at':time.time(), 'expires_at':time.time()+600,'attempts':0})
    msg = EmailMessage()
    msg['From'] = conf['sender']
    msg['To'] = address
    msg['Subject'] = 'AUTOKOM · Code de vérification'
    msg.set_content('Code AUTOKOM : '+code+'\nValable 10 minutes et une seule fois.\n')
    try:
        with _smtp_connection(conf) as server:
            _smtp_secure(server, conf)
            server.send_message(msg)
    except (OSError, smtplib.SMTPException, ssl.SSLError) as exc:
        try:
            OTP.unlink(missing_ok=True)
        except OSError:
            pass
        raise SecurityError('Envoi OTP SMTP sécurisé impossible : aucun e-mail vérifié') from exc

def _check_code(address):
    with _lock():
        record=_read(OTP)
        if not record or record.get('email')!=address or time.time()>record.get('expires_at',0):
            raise SecurityError('Code absent ou expiré')
        if record.get('attempts',0)>=5:raise SecurityError('Trop de tentatives OTP')
        code=_tty_secret('Code reçu par e-mail')
        if not (re.fullmatch(r'\d{6}', code) and
                (_verify(code, record['digest']) if 'digest' in record else
                 hmac.compare_digest(hashlib.sha256(bytes.fromhex(record['salt'])+code.encode()).hexdigest(), record['hash']))):
            record['attempts']+=1;_write(OTP,record)
            raise SecurityError('Code incorrect')
        OTP.unlink()

def _create_recovery_codes(count=10):
    """Generate high-entropy, one-use recovery secrets. No plaintext at rest."""
    require_root()
    codes = [secrets.token_hex(16).upper() for _ in range(count)]
    salt = secrets.token_bytes(16)
    hashes = [hashlib.sha256(salt + bytes.fromhex(code)).hexdigest() for code in codes]
    _write(RECOVERY_CODES, {'version':1,'salt':salt.hex(), 'hashes':hashes, 'created_at':time.time()})
    return codes


def _show_codes(codes):
    print('  ◈ CODES DE SECOURS AUTOKOM (affichés une seule fois)')
    print('  ! Enregistrer hors du serveur dans un coffre-fort ou support hors ligne.')
    print('  ! Toute personne possédant un code peut réinitialiser la passphrase AUTOKOM.')
    for i, code in enumerate(codes, 1):
        print(f'  {i:02d}. {code[:8]}-{code[8:16]}-{code[16:24]}-{code[24:]}')
    print('  › AUTOKOM stocke uniquement les empreintes ; aucune commande ne réaffiche ces codes.')


def _consume_recovery_code(code):
    require_root()
    normalized = code.replace('-', '').strip().upper()
    if not re.fullmatch(r'[A-F0-9]{32}', normalized):
        raise SecurityError('Code de secours invalide')
    with _lock():
        record = _read(RECOVERY_CODES)
        if not record:
            raise SecurityError('Aucun code de secours enregistré')
        value = hashlib.sha256(bytes.fromhex(record['salt']) + bytes.fromhex(normalized)).hexdigest()
        idx = next((i for i,h in enumerate(record['hashes']) if hmac.compare_digest(h, value)), None)
        if idx is None:
            raise SecurityError('Code de secours invalide ou déjà utilisé')
        record['hashes'].pop(idx)
        _write(RECOVERY_CODES, record)
        return len(record['hashes'])


def renew_codes():
    authorize('renouveler tous les codes de secours')
    codes = _create_recovery_codes()
    _show_codes(codes)


def setup():
    require_root()
    with _lock():
        if configured():
            raise SecurityError('Authentification déjà configurée : utiliser change ou reset')
    # EMAIL_ADMIN was entered and confirmed during smtp setup; never ask twice.
    smtp = _smtp_values(_smtp_migrate_legacy())
    email = smtp.get('admin', '').strip()
    if not EMAIL.fullmatch(email):
        raise SecurityError('EMAIL_ADMIN manquant/invalide : sudo autokom auth smtp')
    print('  › Vérification OTP envoyée à EMAIL_ADMIN configuré : ' + email[:1]+'***@'+email.split('@')[-1])
    pending = _read(OTP, {})
    if pending.get('email') == email and pending.get('expires_at', 0) > time.time():
        print('  › OTP encore valide : reprendre le code déjà envoyé, aucun nouvel envoi')
    else:
        _mail_code(email)
        print('  › OTP envoyé via SMTP sécurisé ; compte NON activé avant validation')
    _check_code(email)
    print('  ✓ Adresse de récupération vérifiée par OTP')
    pwd = _password_new()
    with _lock():
        if configured():
            raise SecurityError('Configuration concurrente : refus')
        _write(STATE, {'version':2,'email':email,'email_verified':True,'passphrase':_hash(pwd)})
        _write(ATTEMPTS, {'failures':0,'blocked_until':0})
    codes = _create_recovery_codes()
    print('  ✓ AUTOKOM auth activée · passphrase hachée · e-mail vérifié')
    _show_codes(codes)


def smtp_setup():
    require_root()
    if configured():
        authorize('modifier le serveur SMTP')
    print('  ◈ AUTOKOM / SMTP ET E-MAIL DE RÉCUPÉRATION')
    print('  › TLS obligatoire : STARTTLS (587) ou TLS direct (465).')
    host = _tty_text('EMAIL_HOST · Serveur SMTP (ex: smtp.example.org)')
    port = _tty_text('EMAIL_PORT · Port SMTP [587]') or '587'
    username = _tty_text('EMAIL_LOGIN · Identifiant SMTP (laisser vide si aucun)')
    password = _tty_secret('EMAIL_MDP · Mot de passe SMTP (masqué)') if username else ''
    sender = _tty_text('EMAIL_FROM · Adresse expéditeur des OTP')
    admin = _tty_text('EMAIL_ADMIN · Adresse administrateur/récupération')
    confirm = _tty_text('Confirmer EMAIL_ADMIN')
    if (not re.fullmatch(r'[A-Za-z0-9.-]{1,253}',host) or
        not port.isdecimal() or not 1 <= int(port) <= 65535 or
        not EMAIL.fullmatch(sender) or not EMAIL.fullmatch(admin) or
        admin != confirm or (username and not password)):
        raise SecurityError('SMTP : hôte/port/adresse/confirmation invalide ou mot de passe vide')
    state = _read(STATE)
    if state and admin.casefold() != state.get('email', '').casefold():
        raise SecurityError('EMAIL_ADMIN ne correspond pas à l’e-mail de récupération déjà vérifié : modification refusée')
    conf = {'host': host, 'port': int(port), 'login': username, 'password': password,
            'sender': sender, 'admin': admin}
    from tool_banner import Activity
    try:
        with Activity('Vérification TLS et authentification du serveur SMTP'):
            with _smtp_connection(conf) as server:
                _smtp_secure(server, conf)
    except (OSError, smtplib.SMTPException, ssl.SSLError) as exc:
        raise SecurityError('SMTP : test TLS/authentification échoué ; aucune configuration enregistrée') from exc
    # Exactly the requested keys, stored in a root-only JSON file. EMAIL_MDP
    # is a reusable SMTP credential, so it cannot be hashed like the passphrase.
    _write(SMTP, {
        'EMAIL_HOST': host, 'EMAIL_PORT': int(port), 'EMAIL_LOGIN': username,
        'EMAIL_MDP_ENCRYPTED': _protect_smtp(password), 'EMAIL_FROM': sender, 'EMAIL_ADMIN': admin,
    })
    print('  ✓ SMTP TLS validé · secret chiffré au repos (clé root:0600)')
    print('  ✓ EMAIL_ADMIN confirmé deux fois ; adresse non vérifiée par OTP avant « auth setup »')

def recovery(method='email'):
    require_root()
    state = _read(STATE)
    if not state or not state.get('email_verified'):
        raise SecurityError('Aucun compte AUTOKOM vérifié')
    if method == 'code':
        code = _tty_secret('Code de secours AUTOKOM (usage unique)')
        remaining = _consume_recovery_code(code)
        print(f'  ✓ Code accepté et consommé ; {remaining} code(s) encore utilisable(s)')
    elif method == 'email':
        with _lock():
            record = _read(OTP,{})
            addr = state['email']
            needs_send = not (record and time.time()<record.get('expires_at',0) and record.get('email')==addr)
            if not needs_send and record.get('attempts',0)>=5:
                raise SecurityError('Trop de tentatives OTP : attendre expiration')
        if needs_send:
            _mail_code(addr)
            print('  › Code OTP envoyé à l’adresse vérifiée (adresse non réaffichée)')
        else:
            print('  › Utiliser le code OTP précédemment envoyé (10 min)')
        _check_code(addr)
    else:
        raise SecurityError('Méthode attendue : email ou code')
    pwd = _password_new()
    with _lock():
        state = _read(STATE)
        state['passphrase'] = _hash(pwd)
        _write(STATE,state)
        _write(ATTEMPTS,{'failures':0,'blocked_until':0})
        if OTP.exists(): OTP.unlink()
    print('  ✓ Passphrase renouvelée · vérification de récupération consommée')


def set_jenkins_credential():
    authorize('enregistrer le jeton API Jenkins')
    username=_tty_text('Identifiant administrateur Jenkins')
    token=_tty_secret('Jeton API Jenkins (jamais la passphrase AUTOKOM)')
    if not re.fullmatch(r'[A-Za-z0-9_.@-]{1,150}',username) or not token or '\n' in token or ':' in username:
        raise SecurityError('Identifiant ou jeton Jenkins invalide')
    _write(JENKINS, {'format':'autokom-jenkins-token-v1',
                    'token_encrypted':_fernet().encrypt((username+':'+token).encode()).decode('ascii')})
    print('  ✓ Jeton API Jenkins chiffré au repos dans le coffre local root:0600')

def _migrate_jenkins_legacy():
    """One-time migration for v29 Jenkins API token file; never print its contents."""
    require_root()
    if not JENKINS.exists(): return False
    if JENKINS.is_symlink():raise SecurityError('Secret Jenkins symbolique refusé')
    st=JENKINS.stat()
    if st.st_uid != 0 or st.st_mode & 0o077:
        raise SecurityError('Permissions du jeton Jenkins non conformes')
    raw=JENKINS.read_text(encoding='utf-8').strip()
    if raw.startswith('{'):
        try:
            obj=json.loads(raw)
            if obj.get('format')!='autokom-jenkins-token-v1':raise SecurityError('Format Jenkins secret inattendu')
        except json.JSONDecodeError as exc:
            raise SecurityError('Secret Jenkins JSON invalide') from exc
        return False
    if ':' not in raw or '\n' in raw:
        raise SecurityError('Format de jeton Jenkins historique invalide')
    _write(JENKINS, {'format':'autokom-jenkins-token-v1',
                   'token_encrypted':_fernet().encrypt(raw.encode()).decode('ascii')})
    return True


def main(argv=None):
    p=argparse.ArgumentParser(prog='autokom auth',description='Passphrase locale + e-mail OTP de récupération (SMTP TLS requis)')
    sub=p.add_subparsers(dest='action')
    for action,helptext in [('status','État sans secrets'),('smtp','Configurer le transport SMTP STARTTLS'),('setup','Créer passphrase et vérifier e-mail par OTP'),('verify','Vérifier la passphrase'),('change','Changer la passphrase'),('reset','Récupération par OTP e-mail ou code de secours'),('recovery-codes','Régénérer les codes de secours'),('migrate','Chiffrer les anciens secrets SMTP et Jenkins'),('jenkins-token','Mémoriser une fois le jeton API Jenkins')]:
        sub.add_parser(action,help=helptext)
    sub.choices['reset'].add_argument('--method',choices=['email','code'],help='Canal de récupération ; en menu interactif un choix est proposé')
    args=p.parse_args(argv)
    if args.action is None:
        if not sys.stdin.isatty():p.print_help();return 0
        require_root()
        from menu import pick
        while True:
            if configured():
                choices=[('status','Afficher l’état de sécurité'),('verify','Vérifier la passphrase'),
                         ('change','Changer la passphrase'),('reset','Passphrase oubliée : OTP ou code de secours'),
                         ('recovery-codes','Renouveler les codes de secours'),('migrate','Chiffrer les anciens secrets SMTP'),
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
        conf = _smtp_values(_read(SMTP)) if SMTP.exists() else None
        print('  '+('✓ SMTP prêt (configuration)' if conf else '! SMTP absent'))
        if conf:
            print('  › EMAIL_HOST: '+str(conf['host'])+' · EMAIL_PORT: '+str(conf['port']))
            stored = _read(SMTP, {})
            secret_status = 'chiffré' if 'EMAIL_MDP_ENCRYPTED' in stored else ('historique à migrer' if conf['password'] else 'non requis')
            print('  › EMAIL_LOGIN: '+('configuré' if conf['login'] else 'non requis')+' · EMAIL_MDP: '+secret_status)
            print('  › EMAIL_FROM: '+str(conf['sender'])+' · EMAIL_ADMIN: '+('configuré' if conf['admin'] else 'non défini'))
        codes = _read(RECOVERY_CODES, {})
        print('  › Codes de secours encore disponibles : ' + str(len(codes.get('hashes', []))))
        if JENKINS.exists():
            encrypted=JENKINS.read_text(encoding='utf-8').lstrip().startswith('{')
            print('  '+('✓ Jeton Jenkins chiffré' if encrypted else '! Jeton Jenkins historique : sudo autokom auth migrate'))
        else:
            print('  › Jeton Jenkins non configuré')
        return 0
    if args.action=='smtp':smtp_setup()
    elif args.action=='setup':setup()
    elif args.action=='verify':authorize('vérification manuelle')
    elif args.action=='change':
        authorize('changer la passphrase');state=_read(STATE);state['passphrase']=_hash(_password_new());_write(STATE,state);print('  ✓ Passphrase renouvelée')
    elif args.action=='reset':
        method = args.method
        if method is None:
            from menu import pick
            choice = pick('AUTOKOM · RÉCUPÉRATION', ['OTP envoyé à EMAIL_ADMIN', 'Code de secours hors ligne'],cancel_label='Annuler')
            if not choice:return 0
            method = 'email' if choice == 1 else 'code'
        recovery(method)
    elif args.action=='recovery-codes':renew_codes()
    elif args.action=='migrate':
        if configured():authorize('chiffrer les anciens secrets')
        _smtp_migrate_legacy()
        migrated = _migrate_jenkins_legacy()
        print('  ✓ Configuration SMTP : secret chiffré au repos si présent')
        print('  ✓ Jeton Jenkins : '+('ancien format migré vers le coffre chiffré' if migrated else 'déjà chiffré ou non configuré'))
    elif args.action=='jenkins-token':set_jenkins_credential()
    return 0
