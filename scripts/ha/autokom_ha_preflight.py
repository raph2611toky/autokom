#!/usr/bin/env python3
"""AUTOKOM GCP HA: read-only inventory, readiness & Monitoring metrics.

No resource modifications. No embedded key, no SSH, no project data leakage.
This is a prerequisite to moving customer workloads off the orchestrator;
not an application deployment controller.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_PROJECT = "plucky-stacker-480421-c4"
DEFAULT_REGION = "us-central1"
DEFAULT_MIG = "autokom-ha-rmig"
DEFAULT_LB_BACKEND = "autokom-ha-backend"
DEFAULT_LB_MAP = "autokom-ha-map"
DEFAULT_FORWARD_RULE = "autokom-ha-http"


# Le service systemd utilise directement l'identite GCE. Il ne doit jamais
# invoquer le lanceur gcloud Snap, incompatible avec DynamicUser et l'isolation.
_METADATA_URL = "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token"
_COMPUTE_URL = "https://compute.googleapis.com/compute/v1/projects"


def _metadata_mode() -> bool:
    return os.environ.get("AUTOKOM_GCP_AUTH_MODE", "").strip() == "metadata"


def _metadata_token() -> str:
    req = urllib.request.Request(_METADATA_URL, headers={"Metadata-Flavor": "Google"})
    with urllib.request.urlopen(req, timeout=5) as response:
        data = json.load(response)
    token = data.get("access_token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token:
        raise RuntimeError("Metadata GCE : aucun jeton de compte de service disponible.")
    return token


def _compute_api(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    url = _COMPUTE_URL + path
    body = json.dumps(payload or {}).encode("utf-8") if method == "POST" else None
    req = urllib.request.Request(
        url, data=body,
        headers={"Authorization": "Bearer " + _metadata_token(), "Accept": "application/json",
                 **({"Content-Type": "application/json"} if body is not None else {})},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=25) as response:
        result = json.load(response)
    if not isinstance(result, dict):
        raise RuntimeError("Reponse Compute Engine inattendue.")
    return result


def _metadata_compute_command(*args: str) -> Any:
    """Adaptateur limite aux lectures GCE necessaires au preflight AUTOKOM.

    Aucune commande de mutation ni credential statique ne sont acceptes.
    """
    project = next((value.split("=", 1)[1] for value in args if value.startswith("--project=")), None)
    region = next((value.split("=", 1)[1] for value in args if value.startswith("--region=")), None)
    names = tuple(value for value in args if not value.startswith("--"))
    if not project or not re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", project):
        raise ValueError("Projet GCP absent ou invalide.")
    if len(names) != 4 or names[0] != "compute" or not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", names[3]):
        raise ValueError("Commande GCP en mode metadata non autorisee.")
    prefix = f"/{project}"
    if names[1:3] == ("instance-templates", "describe"):
        return _compute_api("GET", f"{prefix}/global/instanceTemplates/{names[3]}")
    if names[1:3] == ("url-maps", "describe"):
        return _compute_api("GET", f"{prefix}/global/urlMaps/{names[3]}")
    if names[1:3] == ("forwarding-rules", "describe"):
        return _compute_api("GET", f"{prefix}/global/forwardingRules/{names[3]}")
    if names[1:3] == ("backend-services", "describe"):
        return _compute_api("GET", f"{prefix}/global/backendServices/{names[3]}")
    if names[1:3] == ("backend-services", "get-health"):
        service = _compute_api("GET", f"{prefix}/global/backendServices/{names[3]}")
        replies: list[dict[str, Any]] = []
        for backend in service.get("backends") or []:
            group = backend.get("group") if isinstance(backend, dict) else None
            if not isinstance(group, str) or not group.startswith("https://www.googleapis.com/compute/v1/projects/"):
                raise RuntimeError("Backend GCP sans groupe d'instances valide.")
            replies.append(_compute_api("POST", f"{prefix}/global/backendServices/{names[3]}/getHealth", {"group": group}))
        return {"backends": replies}
    raise ValueError("Commande GCP en mode metadata non autorisee.")


def command(*args: str) -> Any:
    if _metadata_mode():
        # Le nom de groupe regional est le seul chemin a cinq composantes.
        tokens = tuple(value for value in args if not value.startswith("--"))
        project = next((value.split("=", 1)[1] for value in args if value.startswith("--project=")), None)
        region = next((value.split("=", 1)[1] for value in args if value.startswith("--region=")), None)
        if len(tokens) == 5 and tokens[:3] == ("compute", "instance-groups", "managed"):
            action, name = tokens[3], tokens[4]
            if (action not in ("describe", "list-instances") or not region or
                    not re.fullmatch(r"[a-z]+-[a-z0-9]+[0-9]", region) or
                    not project or not re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", project) or
                    not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", name)):
                raise ValueError("Requete MIG non autorisee.")
            route = f"/{project}/regions/{region}/instanceGroupManagers/{name}"
            if action == "describe":
                return _compute_api("GET", route)
            result: list[Any] = []
            page_token = None
            for _ in range(20):
                doc = _compute_api("POST", route + "/listManagedInstances", {"pageToken": page_token} if page_token else {})
                result.extend(doc.get("managedInstances") or [])
                page_token = doc.get("nextPageToken")
                if not page_token:
                    return result
            raise RuntimeError("Inventaire MIG depasse 20 pages.")
        return _metadata_compute_command(*args)
    proc = subprocess.run(["gcloud", *args, "--format=json"], text=True, capture_output=True,
                          timeout=50, check=False)
    if proc.returncode:
        raise RuntimeError(f"gcloud {' '.join(args[:4])}: {proc.stderr.strip()[:400]}")
    return json.loads(proc.stdout or "null")


def find_healths(payload: Any) -> list[str]:
    """GCE backend get-health uses healthStatus lists; recursive for provider versions."""
    output: list[str] = []
    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("healthStatus"), list):
                for item in node["healthStatus"]:
                    if isinstance(item, dict):
                        output.append(str(item.get("healthState") or "UNKNOWN").upper())
            for key, value in node.items():
                if key != "healthStatus":
                    visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)
    visit(payload)
    return output


def get_template_name(mig_doc: dict[str, Any]) -> str:
    versions = mig_doc.get("versions") or []
    for version in versions:
        if isinstance(version, dict) and version.get("instanceTemplate"):
            return str(version["instanceTemplate"]).rsplit("/", 1)[-1]
    return ""


def bootstrap_mode(template: dict[str, Any]) -> str:
    items = (template.get("properties") or {}).get("metadata", {}).get("items") or []
    for item in items:
        if isinstance(item, dict) and item.get("key") == "startup-script":
            script = str(item.get("value") or "")
            if "autokom-ha-worker-preparation-only" in script:
                return "worker"
            if "infrastructure validation only" in script.lower() or "validation_server.py" in script:
                return "validation"
            return "application_or_custom"
    return "unknown"


def oauth_token() -> str:
    if _metadata_mode():
        return _metadata_token()
    proc = subprocess.run(["gcloud", "auth", "print-access-token"], text=True,
                          capture_output=True, timeout=25, check=False)
    if proc.returncode or not proc.stdout.strip():
        raise RuntimeError("Impossible d'obtenir un jeton ADC via gcloud; ne pas utiliser une cle JSON permanente.")
    return proc.stdout.strip()


def monitoring_counts(project: str, url_map: str, period_minutes: int = 5) -> dict[str, int]:
    """Read delta request_count for global external Application LB; 4xx excluded from availability SLI."""
    token = oauth_token()
    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(minutes=period_minutes)
    def timestamp(value: dt.datetime) -> str:
        return value.isoformat(timespec="seconds").replace("+00:00", "Z")
    query = {
        "filter": ('metric.type="loadbalancing.googleapis.com/https/request_count" '
                   'AND resource.type="https_lb_rule" '
                   f'AND resource.labels.url_map_name="{url_map}"'),
        "interval.startTime": timestamp(start),
        "interval.endTime": timestamp(end),
        "view": "FULL",
        "pageSize": "100",
    }
    result = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0, "other": 0}
    page_count = 0
    while page_count < 10:
        url = f"https://monitoring.googleapis.com/v3/projects/{urllib.parse.quote(project)}/timeSeries?" + urllib.parse.urlencode(query)
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            doc = json.loads(resp.read(5_000_000))
        for series in doc.get("timeSeries", []):
            code = str((series.get("metric") or {}).get("labels", {}).get("response_code_class", ""))
            label = code[:1] + "xx" if len(code) == 3 and code[:1] in "2345" else "other"
            for point in series.get("points", []):
                raw = (point.get("value") or {}).get("int64Value", (point.get("value") or {}).get("doubleValue", 0))
                try:
                    result[label] += int(float(raw))
                except (TypeError, ValueError, OverflowError):
                    pass
        page_count += 1
        cursor = doc.get("nextPageToken")
        if not cursor:
            return result
        query["pageToken"] = cursor
    raise RuntimeError("Monitoring result contains more than 1000 time-series; narrow the filter.")


def inventory(project: str, region: str, mig: str, backend: str, url_map: str,
              forwarding_rule: str, with_metrics: bool) -> dict[str, Any]:
    project_safe = re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", project)
    names_safe = all(re.fullmatch(r"[a-z][a-z0-9-]{0,62}", value) for value in (mig, backend, url_map, forwarding_rule))
    if not project_safe or not names_safe or not re.fullmatch(r"[a-z]+-[a-z0-9]+[0-9]", region):
        raise ValueError("Identifiant GCP invalide.")
    mig_doc = command("compute", "instance-groups", "managed", "describe", mig,
                      f"--region={region}", f"--project={project}")
    nodes = command("compute", "instance-groups", "managed", "list-instances", mig,
                    f"--region={region}", f"--project={project}")
    if not isinstance(nodes, list):
        raise RuntimeError("Réponse MIG inattendue.")
    backend_doc = command("compute", "backend-services", "describe", backend, "--global", f"--project={project}")
    health_doc = command("compute", "backend-services", "get-health", backend, "--global", f"--project={project}")
    map_doc = command("compute", "url-maps", "describe", url_map, "--global", f"--project={project}")
    forward_doc = command("compute", "forwarding-rules", "describe", forwarding_rule, "--global", f"--project={project}")
    template_name = get_template_name(mig_doc)
    template = (command("compute", "instance-templates", "describe", template_name,
                        f"--project={project}") if template_name else {})
    healths = find_healths(health_doc)
    hosts = [host for rule in map_doc.get("hostRules", []) for host in rule.get("hosts", [])]
    nodes_info = [{"name": str(node.get("instance", "")).rsplit("/", 1)[-1],
                   "zone": str(node.get("instance", "")).split("/zones/")[-1].split("/")[0],
                   "status": node.get("instanceStatus", node.get("currentAction", "UNKNOWN"))}
                  for node in nodes]
    validation = bootstrap_mode(template)
    result: dict[str, Any] = {
        "project": project, "region": region, "mig": mig,
        "instance_count": len(nodes_info), "instances": nodes_info,
        "min_instances": None, "max_instances": None,
        "backend": backend, "backend_port_name": backend_doc.get("portName"),
        "health_states": healths, "healthy_backends": sum(x == "HEALTHY" for x in healths),
        "forwarding_ip": forward_doc.get("IPAddress"),
        "load_balancer_hosts": hosts, "load_balancer_default_backend": map_doc.get("defaultService"),
        "template": template_name, "bootstrap_mode": validation,
        "can_host_customer_applications": False,
        "issues": [], "warnings": [],
    }
    issues = result["issues"]
    warnings = result["warnings"]
    if len(nodes_info) < 2:
        issues.append("Le MIG possède moins de deux instances ; aucune HA client ne peut être promise.")
    if any(node["status"] != "RUNNING" for node in nodes_info):
        issues.append("Certaines instances du MIG ne sont pas RUNNING.")
    if len(healths) < 2 or sum(x == "HEALTHY" for x in healths) < 2:
        issues.append("Moins de deux backends HTTP sont HEALTHY.")
    if validation == "validation":
        issues.append("Instance template toujours en mode VALIDATION : pas de runtime client ni de restauration automatique.")
    if validation == "worker":
        issues.append("Workers initialises seulement : publication des images, reconciliation, routage et HTTPS clients non configures.")
    if validation == "unknown":
        issues.append("Bootstrap du template inconnu : vérifier avant d'activer des applications clientes.")
    if not hosts:
        warnings.append("Aucune règle de domaine client sur le Load Balancer ; map par défaut uniquement.")
    warnings.append("Cet inventaire ne garantit pas la livraison des images, le stockage des secrets ni la gestion des sessions.")
    if with_metrics:
        try:
            result["lb_requests_5m"] = monitoring_counts(project, url_map)
            result["monitoring_status"] = "ok"
        except (RuntimeError, urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError) as exc:
            result["monitoring_status"] = "unavailable"
            warnings.append("Cloud Monitoring inaccessible (API ou role monitoring.viewer à vérifier): " + str(exc)[:180])
    return result


def to_prometheus(report: dict[str, Any]) -> str:
    def metric(name: str, value: int) -> str:
        return f"# TYPE {name} gauge\n{name} {value}\n"
    lines = [metric("autokom_ha_mig_instances", report["instance_count"]),
             metric("autokom_ha_lb_healthy_backends", report["healthy_backends"]),
             metric("autokom_ha_readiness_ok", int(not report["issues"] and report["can_host_customer_applications"]))]
    for label, value in sorted(report.get("lb_requests_5m", {}).items()):
        lines.append(f'autokom_ha_lb_requests_5m{{response_class="{label}"}} {value}\n')
    return "".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description="Inventaire en lecture seule du MIG AUTOKOM et du Load Balancer")
    p.add_argument("--project", default=DEFAULT_PROJECT)
    p.add_argument("--region", default=DEFAULT_REGION)
    p.add_argument("--mig", default=DEFAULT_MIG)
    p.add_argument("--backend", default=DEFAULT_LB_BACKEND)
    p.add_argument("--url-map", default=DEFAULT_LB_MAP)
    p.add_argument("--forwarding-rule", default=DEFAULT_FORWARD_RULE)
    p.add_argument("--with-metrics", action="store_true")
    p.add_argument("--format", choices=("human", "json", "prometheus"), default="human")
    args = p.parse_args()
    try:
        report = inventory(args.project, args.region, args.mig, args.backend,
                           args.url_map, args.forwarding_rule, args.with_metrics)
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print("AUTOKOM HA preflight erreur: " + str(exc), file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.format == "prometheus":
        print(to_prometheus(report), end="")
    else:
        print(f"MIG {report['mig']} : {report['instance_count']} instance(s), {report['healthy_backends']} backend(s) HEALTHY")
        for item in report["instances"]:
            print(f"  {item['name']} [{item['zone']}] {item['status']}")
        print("IP publique LB :", report["forwarding_ip"] or "indisponible")
        print("Template :", report["template"], "(mode", report["bootstrap_mode"] + ")")
        print("Règles de domaines LB :", ", ".join(report["load_balancer_hosts"]) or "aucune")
        if "lb_requests_5m" in report:
            print("Trafic LB (5 min) :", report["lb_requests_5m"])
        for issue in report["issues"]:
            print("BLOQUANT:", issue)
        for warning in report["warnings"]:
            print("ATTENTION:", warning)
        print("STATUT CLIENTS : PAS ENCORE PRET - aucune application client deployee par ce script")
    return 0


if __name__ == "__main__":
    sys.exit(main())
