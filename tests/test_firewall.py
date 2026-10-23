#!/usr/bin/env python3
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'/'lib'))
import firewall


class FakeExec:
    def __init__(self, active=True, published=None):
        self.active=active
        self.published=published or []
        self.added=set()
        self.calls=[]
    def __call__(self, argv, check=True):
        self.calls.append(tuple(argv))
        if argv[:4] == ['ip','-4','route','get']:
            value='1.1.1.1 via 172.31.16.1 dev enp39s0 src 172.31.21.188 uid 0\n'
        elif argv[:4] == ['ip','-o','-4','addr']:
            value='2: enp39s0 inet 172.31.21.188/20\n3: docker0 inet 172.17.0.1/16\n4: br-8a3e134220a2 inet 172.18.0.1/16\n'
        elif argv[:3]==['docker','ps','-q']:
            value='container1\n' if self.published else ''
        elif argv[:2]==['docker','inspect']:
            value=json.dumps(self.published)
        elif argv[:2]==['ufw','status']:
            value='Status: active\n' if self.active else 'Status: inactive\n'
        elif argv[:3]==['ufw','show','added']:
            value='\n'.join('ufw '+' '.join(x) for x in sorted(self.added))
        elif argv[:3]==['ufw','insert','1']:
            self.added.add(tuple(argv[3:]))
            value='Rule inserted'
        elif argv[:3]==['ufw','--force','enable']:
            self.active=True
            value='Firewall is active'
        else:
            value=''
        return subprocess.CompletedProcess(argv, 0, value, '')


class FirewallTests(unittest.TestCase):
    def fake_tools(self):
        return patch.object(firewall.shutil, 'which', side_effect=lambda cmd: '/usr/sbin/'+cmd)
    def test_readonly_check(self):
        fake=FakeExec(active=False)
        with self.fake_tools():
            result=firewall.secure_firewall('mysql', apply=False, executor=fake, say=lambda x:None)
        self.assertFalse(result['active'])
        self.assertFalse(any(c[0]=='ufw' and c[1] not in ('status','show') for c in fake.calls))
    def test_inactive_refuses_without_ssh_guard(self):
        fake=FakeExec(active=False)
        with self.fake_tools(), self.assertRaisesRegex(RuntimeError,'UFW inactif'):
            firewall.secure_firewall('mysql', apply=True, executor=fake, say=lambda x:None)
        self.assertFalse(any(c[:3]==('ufw','--force','enable') for c in fake.calls))
    def test_enforcement_on_wan_and_docker_bridges(self):
        fake=FakeExec(active=True)
        with self.fake_tools():
            firewall.secure_firewall('mysql', apply=True, executor=fake, say=lambda x:None)
        commands=fake.calls
        self.assertIn(tuple(firewall.rule_command('deny','enp39s0',3306)), commands)
        self.assertIn(tuple(firewall.rule_command('deny','enp39s0',33060)), commands)
        self.assertIn(tuple(firewall.rule_command('deny','enp39s0',8080)), commands)
        self.assertIn(tuple(firewall.rule_command('allow','docker0',3306)), commands)
        self.assertIn(tuple(firewall.rule_command('allow','br-8a3e134220a2',3306)), commands)
        # Second run must not duplicate rules.
        first=len(commands)
        with self.fake_tools():
            firewall.secure_firewall('mysql', apply=True, executor=fake, say=lambda x:None)
        self.assertFalse(any(c[:3]==('ufw','insert','1') for c in fake.calls[first:]))
    def test_public_docker_publish_refuses(self):
        published=[{'Name':'/myapi','HostConfig':{'NetworkMode':'bridge'},'NetworkSettings':{'Ports':{'8000/tcp':[{'HostIp':'0.0.0.0','HostPort':'8000'}]}}}]
        fake=FakeExec(active=True, published=published)
        with self.fake_tools(), self.assertRaisesRegex(RuntimeError,'Port Docker publié'):
            firewall.secure_firewall('mysql', apply=True, executor=fake, say=lambda x:None)
        self.assertFalse(any(c[:3]==('ufw','insert','1') for c in fake.calls))
    def test_activate_with_explicit_ssh_rule(self):
        fake=FakeExec(active=False)
        with self.fake_tools():
            firewall.secure_firewall('mysql', apply=True, activate=True,ssh_cidr='198.51.100.10/32',
                                     confirmation=True,executor=fake,say=lambda x:None)
        enable=next(i for i,x in enumerate(fake.calls) if x[:3]==('ufw','--force','enable'))
        ssh=next(i for i,x in enumerate(fake.calls) if x[:3]==('ufw','allow','from'))
        self.assertLess(ssh,enable)
    def test_reject_public_ssh_range(self):
        fake=FakeExec(active=False)
        with self.fake_tools(), self.assertRaisesRegex(RuntimeError,'Refus'):
            firewall.secure_firewall('mysql',apply=True,activate=True,ssh_cidr='0.0.0.0/0',confirmation=True,executor=fake,say=lambda x:None)
    def test_argparse_duplicate_definitions(self):
        p=Path(__file__).resolve().parents[1]/'scripts'/'lib'/'services.py'
        res=subprocess.run([sys.executable,str(p),'--help'],text=True,capture_output=True)
        self.assertEqual(res.returncode,0,res.stderr)
        self.assertEqual(res.stdout.count('--confirm-restart '),1)
    def test_docker_localhost_publication_ok(self):
        published=[{'Name':'/myapi','HostConfig':{'NetworkMode':'bridge'},'NetworkSettings':{'Ports':{'8000/tcp':[{'HostIp':'127.0.0.1','HostPort':'8465'}]}}}]
        fake=FakeExec(active=True, published=published)
        with self.fake_tools():
            self.assertEqual(firewall.docker_published(fake),[])


if __name__ == '__main__':
    unittest.main(verbosity=2)
