"""Offline safety tests for controlled Jenkins credentials provisioning."""
import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'lib'))
from autokom_jenkins_credentials import (CredentialSetupError, provision, credential_xml,
                                        expected_description, CREDENTIAL_ID)
ENV={'AUTOKOM_RUNNER_SHARED_KEY':'a'*48}


class FakeJenkins:
    def __init__(self, found=False, description=None):
        self.found = found
        self.description = description
        self.posts = 0
        self.xml = None
    def __call__(self, env, method, path, data=None, content_type='application/xml'):
        if method == 'GET':
            if not self.found:
                return 404,b''
            import json
            return 200,json.dumps({'id':CREDENTIAL_ID, 'typeName':'Secret text',
                                   'description':self.description}).encode()
        assert method == 'POST'
        self.posts += 1
        self.xml = data
        root = ET.fromstring(data)
        assert root.findtext('id') == CREDENTIAL_ID
        self.description = root.findtext('description')
        self.found = True
        return 200,b''


class CredentialsTests(unittest.TestCase):
    def test_preview_never_writes(self):
        j=FakeJenkins()
        self.assertEqual(provision(ENV,j,apply=False),'absent')
        self.assertEqual(j.posts,0)
    def test_create_only_once(self):
        j=FakeJenkins()
        self.assertEqual(provision(ENV,j,apply=True),'created')
        self.assertEqual(provision(ENV,j,apply=True),'managed')
        self.assertEqual(j.posts,1)
        self.assertNotIn(b'<secret></secret>',j.xml)
    def test_conflict_fails_closed(self):
        j=FakeJenkins(found=True,description='manually managed')
        with self.assertRaises(CredentialSetupError): provision(ENV,j,apply=True)
        self.assertEqual(j.posts,0)
    def test_refuse_changed_key(self):
        j=FakeJenkins(found=True,description=expected_description(ENV))
        with self.assertRaises(CredentialSetupError): provision({'AUTOKOM_RUNNER_SHARED_KEY':'b'*48},j,apply=True)
    def test_secret_not_stored_in_description(self):
        tree=ET.fromstring(credential_xml(ENV))
        self.assertNotIn(ENV['AUTOKOM_RUNNER_SHARED_KEY'],tree.findtext('description'))
    def test_weak_secret_fails(self):
        with self.assertRaises(CredentialSetupError): credential_xml({'AUTOKOM_RUNNER_SHARED_KEY':'weak'})

if __name__ == '__main__': unittest.main()
