"""v30: SMTP admin reuse, OTP-before-password, encrypted mail secret, one-time recovery.
All mail and filesystem actions use isolated fixture and mocks; no live email is sent.
"""
from __future__ import annotations
import contextlib
import io
from pathlib import Path
import tempfile
import time
from unittest import mock

import pytest
import autokom_security as s


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(s, 'BASE',tmp_path)
    for name,file in [('STATE','auth.json'),('SMTP','smtp.json'),('OTP','recovery.json'),
                      ('ATTEMPTS','attempts.json'),('JENKINS','jenkins-api.auth'),
                      ('RECOVERY_CODES','recovery-codes.json'),('VAULT_KEY','vault.key')]:
        monkeypatch.setattr(s, name, tmp_path/file)
    return tmp_path


def conf(admin='admin@example.org', pwd='not-visible-secret'):
    return {'EMAIL_HOST':'smtp.example.org','EMAIL_PORT':587,'EMAIL_LOGIN':'admin',
            'EMAIL_MDP':pwd,'EMAIL_FROM':'no-reply@example.org','EMAIL_ADMIN':admin}


def test_setup_uses_existing_admin_then_otp_before_password(monkeypatch, isolated):
    s._write(s.SMTP,conf())
    calls=[]
    def send(a):
        calls.append('send')
        assert a == 'admin@example.org'
        assert not s.STATE.exists()
    def verify(a):
        calls.append('verify')
        assert not s.STATE.exists()
    def password():
        calls.append('password')
        return 'long password with entropy!'
    monkeypatch.setattr(s,'_mail_code',send)
    monkeypatch.setattr(s,'_check_code',verify)
    monkeypatch.setattr(s,'_password_new',password)
    monkeypatch.setattr(s,'_tty_text',lambda _: pytest.fail('email asked twice'))
    output=io.StringIO()
    with contextlib.redirect_stdout(output): s.setup()
    assert calls==['send','verify','password']
    assert s._read(s.STATE)['email_verified']
    assert len(s._read(s.RECOVERY_CODES)['hashes']) == 10
    assert 'long password' not in s.STATE.read_text()
    assert 'admin@example.org' not in s.SMTP.read_text() or 'not-visible-secret' not in s.SMTP.read_text()
    assert 'CODES DE SECOURS' in output.getvalue()


def test_invalid_otp_prevents_password_prompt_and_account_creation(monkeypatch, isolated):
    s._write(s.SMTP,conf())
    monkeypatch.setattr(s,'_mail_code',lambda _:None)
    monkeypatch.setattr(s,'_check_code',lambda _: (_ for _ in ()).throw(s.SecurityError('OTP invalide')))
    with mock.patch.object(s,'_password_new') as pwd:
        with pytest.raises(s.SecurityError,match='OTP invalide'):s.setup()
        pwd.assert_not_called()
    assert not s.STATE.exists()
    assert not s.RECOVERY_CODES.exists()


def test_smtp_secret_encrypted_legacy_migrated_and_not_plaintext(isolated):
    s._write(s.SMTP,conf())
    updated = s._smtp_migrate_legacy()
    assert 'EMAIL_MDP_ENCRYPTED' in updated
    assert 'EMAIL_MDP' not in updated
    assert 'not-visible-secret' not in s.SMTP.read_text()
    assert 'not-visible-secret' not in s.VAULT_KEY.read_text()
    assert s._smtp_values(updated)['password']=='not-visible-secret'
    assert s.VAULT_KEY.stat().st_mode & 0o777 == 0o600
    assert s.SMTP.stat().st_mode & 0o777 == 0o600
    assert s._smtp_migrate_legacy() == updated


def test_decryption_requires_same_key(isolated):
    s._write(s.SMTP,{'EMAIL_MDP_ENCRYPTED':s._protect_smtp('hidden')})
    s.VAULT_KEY.unlink()
    with pytest.raises(s.SecurityError,match='Clé du coffre absente'):
        s._smtp_values(s._read(s.SMTP))
    assert not s.VAULT_KEY.exists()


def test_codes_are_unique_hashed_and_one_time(isolated):
    codes=s._create_recovery_codes()
    assert len(set(codes))==10
    raw=s.RECOVERY_CODES.read_text()
    assert all(c not in raw for c in codes)
    left=s._consume_recovery_code(codes[0])
    assert left == 9
    with pytest.raises(s.SecurityError,match='déjà utilisé'):
        s._consume_recovery_code(codes[0])
    assert s._consume_recovery_code(codes[1][:8]+'-'+codes[1][8:])==8


def test_recovery_with_code_updates_hash_without_smtp(monkeypatch, isolated):
    s._write(s.STATE,{'version':2,'email':'admin@example.org','email_verified':True,'passphrase':s._hash('initial passphrase 123')})
    code=s._create_recovery_codes()[0]
    vals=iter([code])
    monkeypatch.setattr(s,'_tty_secret',lambda _:next(vals))
    monkeypatch.setattr(s,'_password_new',lambda :'new long passphrase after recovery')
    s.recovery('code')
    assert s._verify('new long passphrase after recovery',s._read(s.STATE)['passphrase'])
    assert not s._verify('initial passphrase 123',s._read(s.STATE)['passphrase'])
    with pytest.raises(s.SecurityError,match='déjà utilisé'):
        s._consume_recovery_code(code)


def test_otp_cannot_be_resent_while_valid(monkeypatch, isolated):
    s._write(s.SMTP,conf())
    s._write(s.OTP,{'email':'admin@example.org','expires_at':time.time()+600,'attempts':5,'created_at':time.time()-120})
    with mock.patch.object(s,'_smtp_connection') as connect:
        with pytest.raises(s.SecurityError,match='encore valide'):
            s._mail_code('admin@example.org')
        connect.assert_not_called()


def test_reset_method_parser_help(capsys):
    with pytest.raises(SystemExit) as exc:
        s.main(['reset','-h'])
    assert exc.value.code == 0
    assert '--method' in capsys.readouterr().out


def test_previous_v29_setup_without_smtp_still_fails(isolated):
    with pytest.raises(s.SecurityError,match='SMTP non configuré'):
        s.setup()


def test_recovery_codes_regenerate_requires_auth(monkeypatch,isolated):
    monkeypatch.setattr(s,'authorize',lambda *a,**k: (_ for _ in ()).throw(s.SecurityError('refus')))
    with pytest.raises(s.SecurityError,match='refus'):s.renew_codes()
    assert not s.RECOVERY_CODES.exists()


def test_jenkins_secret_encrypted_and_plugin_manager_can_use_it(monkeypatch, isolated):
    import jenkins_plugins as j
    monkeypatch.setattr(s,'authorize',lambda _:True)
    monkeypatch.setattr(s,'_tty_text',lambda _:'admin')
    monkeypatch.setattr(s,'_tty_secret',lambda _:'real-token-secret')
    s.set_jenkins_credential()
    raw=s.JENKINS.read_text()
    assert 'real-token-secret' not in raw
    assert s._read(s.JENKINS)['format']=='autokom-jenkins-token-v1'
    import base64
    header=j.auth_header(str(s.JENKINS))
    assert base64.b64decode(header.removeprefix('Basic ')).decode()=='admin:real-token-secret'


def test_migration_jenkins_legacy_keeps_api_usable(isolated):
    import jenkins_plugins as j
    s.JENKINS.write_text('admin:older-jenkins-token\n')
    s.JENKINS.chmod(0o600)
    assert s._migrate_jenkins_legacy() is True
    assert s._migrate_jenkins_legacy() is False
    assert 'older-jenkins-token' not in s.JENKINS.read_text()
    import base64
    assert base64.b64decode(j.auth_header(str(s.JENKINS))[6:]).decode()=='admin:older-jenkins-token'


def test_status_does_not_migrate_legacy_or_print_secrets(isolated, capsys):
    s._write(s.SMTP,conf())
    s.main(['status'])
    output=capsys.readouterr().out
    assert 'not-visible-secret' not in output
    assert 'historique à migrer' in output
    assert s._read(s.SMTP)==conf()
    assert not s.VAULT_KEY.exists()


def test_otp_storage_uses_costly_hash_and_single_use(monkeypatch, isolated):
    s._write(s.SMTP,conf())
    monkeypatch.setattr(s.secrets,'randbelow',lambda n:12345)
    with mock.patch.object(s.smtplib,'SMTP'):
        s._mail_code('admin@example.org')
    record=s._read(s.OTP)
    assert record['digest']['algorithm']=='pbkdf2-sha256'
    assert '012345' not in str(record)
    monkeypatch.setattr(s,'_tty_secret',lambda _:'012345')
    s._check_code('admin@example.org')
    assert not s.OTP.exists()


def test_bad_otp_limit_and_no_regeneration(monkeypatch, isolated):
    s._write(s.SMTP,conf())
    monkeypatch.setattr(s.secrets,'randbelow',lambda n:999999)
    with mock.patch.object(s.smtplib,'SMTP'):
        s._mail_code('admin@example.org')
    monkeypatch.setattr(s,'_tty_secret',lambda _:'000000')
    for _ in range(5):
        with pytest.raises(s.SecurityError,match='incorrect'):
            s._check_code('admin@example.org')
    with pytest.raises(s.SecurityError,match='Trop de tentatives'):
        s._check_code('admin@example.org')
    with pytest.raises(s.SecurityError,match='encore valide'):
        s._mail_code('admin@example.org')
