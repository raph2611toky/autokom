"""No network: validate read-only metadata REST path of the HA exporter."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ha_gcp_preflight", ROOT / "scripts/ha/autokom_ha_preflight.py")
ha = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ha)


class TestGceMetadataMode(TestCase):
    def test_mig_get_and_list_over_rest(self):
        calls = []
        def api(method, path, payload=None):
            calls.append((method, path, payload))
            if path.endswith("/listManagedInstances"):
                return {"managedInstances": [{"instanceStatus": "RUNNING", "instance": "abc"}]}
            return {"versions": []}
        with patch.dict(os.environ, {"AUTOKOM_GCP_AUTH_MODE": "metadata"}), patch.object(ha, "_compute_api", side_effect=api):
            one = ha.command("compute", "instance-groups", "managed", "describe", "autokom-ha-rmig", "--region=us-central1", "--project=plucky-stacker-480421-c4")
            two = ha.command("compute", "instance-groups", "managed", "list-instances", "autokom-ha-rmig", "--region=us-central1", "--project=plucky-stacker-480421-c4")
        self.assertEqual(one, {"versions": []})
        self.assertEqual(len(two), 1)
        self.assertEqual(calls[0][0], "GET")
        self.assertEqual(calls[1][0], "POST")

    def test_health_uses_only_described_group(self):
        calls = []
        group = "https://www.googleapis.com/compute/v1/projects/plucky-stacker-480421-c4/regions/us-central1/instanceGroups/autokom-ha-rmig"
        def api(method, path, payload=None):
            calls.append((method, path, payload))
            if method == "GET":
                return {"backends": [{"group": group}]}
            return {"healthStatus": [{"healthState": "HEALTHY"}, {"healthState": "HEALTHY"}]}
        with patch.dict(os.environ, {"AUTOKOM_GCP_AUTH_MODE": "metadata"}), patch.object(ha, "_compute_api", side_effect=api):
            doc = ha.command("compute", "backend-services", "get-health", "autokom-ha-backend", "--global", "--project=plucky-stacker-480421-c4")
        self.assertEqual(ha.find_healths(doc), ["HEALTHY", "HEALTHY"])
        self.assertEqual(calls[1][2], {"group": group})

    def test_full_inventory_without_gcloud(self):
        group = "https://www.googleapis.com/compute/v1/projects/plucky-stacker-480421-c4/regions/us-central1/instanceGroups/autokom-ha-rmig"
        def api(method, path, payload=None):
            if path.endswith("/instanceGroupManagers/autokom-ha-rmig"):
                return {"versions": [{"instanceTemplate": ".../autokom-ha-tpl"}]}
            if path.endswith("/listManagedInstances"):
                return {"managedInstances": [
                    {"instance": ".../zones/us-central1-a/instances/a", "instanceStatus": "RUNNING"},
                    {"instance": ".../zones/us-central1-f/instances/b", "instanceStatus": "RUNNING"}]}
            if path.endswith("/backendServices/autokom-ha-backend/getHealth"):
                return {"healthStatus": [{"healthState": "HEALTHY"}, {"healthState": "HEALTHY"}]}
            if path.endswith("/backendServices/autokom-ha-backend"):
                return {"portName": "http", "backends": [{"group": group}]}
            if path.endswith("/urlMaps/autokom-ha-map"):
                return {"defaultService": "...", "hostRules": []}
            if path.endswith("/forwardingRules/autokom-ha-http"):
                return {"IPAddress": "35.186.239.102"}
            if path.endswith("/instanceTemplates/autokom-ha-tpl"):
                return {"properties": {"metadata": {"items": [{"key": "startup-script", "value": "validation_server.py"}]}}}
            raise AssertionError((method, path))
        with (patch.dict(os.environ, {"AUTOKOM_GCP_AUTH_MODE": "metadata"}),
              patch.object(ha, "_compute_api", side_effect=api),
              patch.object(ha, "monitoring_counts", return_value={"2xx": 0}),
              patch.object(ha.subprocess, "run", side_effect=AssertionError("snap CLI called"))):
            report = ha.inventory("plucky-stacker-480421-c4", "us-central1", "autokom-ha-rmig", "autokom-ha-backend", "autokom-ha-map", "autokom-ha-http", True)
        self.assertEqual(report["healthy_backends"], 2)
        self.assertEqual(report["instance_count"], 2)
        self.assertEqual(report["monitoring_status"], "ok")
        self.assertEqual(report["bootstrap_mode"], "validation")

    def test_reject_mutations(self):
        with patch.dict(os.environ, {"AUTOKOM_GCP_AUTH_MODE": "metadata"}), self.assertRaises(ValueError):
            ha.command("compute", "instance-groups", "managed", "delete", "autokom-ha-rmig", "--region=us-central1", "--project=plucky-stacker-480421-c4")

    def test_oauth_does_not_call_gcloud(self):
        with patch.dict(os.environ, {"AUTOKOM_GCP_AUTH_MODE": "metadata"}), patch.object(ha, "_metadata_token", return_value="fake-token") as metadata, patch.object(ha.subprocess, "run", side_effect=AssertionError("gcloud called")):
            self.assertEqual(ha.oauth_token(), "fake-token")
            metadata.assert_called_once_with()


if __name__ == '__main__':
    main()
