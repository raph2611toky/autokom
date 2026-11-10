#!/usr/bin/env python3
"""Deterministic, read-only candidate placement for customer applications.

IMPORTANT: placement preview is NOT deployment. MIG nodes still require a
reconciling runtime, artifact distribution, host routing and a durable control
plane. Never treat this output as a completed HA release.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autokom_ha_preflight as pf


class PlacementError(ValueError):
    pass


def _score(environment_id: str, name: str) -> bytes:
    return hashlib.sha256(f"autokom-ha:v1:{environment_id}:{name}".encode("ascii")).digest()


def select_workers(environment_id: str, nodes: list[dict], *, high_availability: bool) -> list[dict]:
    """Select one healthy candidate or two in distinct zones; never mutate GCP.

    Stable rendezvous hashing limits reshuffles after scale-out and avoids
    relying on automatically changing MIG instance names in stored configs.
    """
    try:
        environment_id = str(uuid.UUID(environment_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise PlacementError("Identifiant d'environnement UUID invalide.") from exc
    pool: dict[str, dict] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        name, zone = node.get("name"), node.get("zone")
        if (node.get("status") != "RUNNING" or not isinstance(name, str)
                or not name.startswith("autokom-ha-web-") or not isinstance(zone, str)
                or not zone.startswith("us-central1-") or not name.isascii()
                or not zone.isascii()):
            continue
        pool[name] = {"name": name, "zone": zone, "status": "RUNNING"}
    ordered = sorted(pool.values(), key=lambda n: _score(environment_id, n["name"]), reverse=True)
    if not ordered:
        raise PlacementError("Aucun worker GCP RUNNING eligible.")
    first = ordered[0]
    if not high_availability:
        return [first]
    second = next((node for node in ordered[1:] if node["zone"] != first["zone"]), None)
    if second is None:
        raise PlacementError("Haute disponibilite impossible : deux zones distinctes requises.")
    return [first, second]


def main() -> int:
    parser = argparse.ArgumentParser(description="Selection lecture seule de workers AUTOKOM GCP (sans publication)")
    parser.add_argument("--environment", required=True, help="UUID de l'environnement AUTOKOM")
    parser.add_argument("--mode", choices=("standard", "ha"), required=True)
    parser.add_argument("--project", default=pf.DEFAULT_PROJECT)
    parser.add_argument("--region", default=pf.DEFAULT_REGION)
    parser.add_argument("--mig", default=pf.DEFAULT_MIG)
    parser.add_argument("--format", choices=("json", "human"), default="human")
    args = parser.parse_args()
    try:
        report = pf.inventory(args.project, args.region, args.mig, pf.DEFAULT_LB_BACKEND,
                              pf.DEFAULT_LB_MAP, pf.DEFAULT_FORWARD_RULE, False)
        selected = select_workers(args.environment, report["instances"], high_availability=args.mode == "ha")
    except (PlacementError, RuntimeError, ValueError, OSError) as exc:
        print(f"AUTOKOM HA placement: {exc}", file=sys.stderr)
        return 2
    # A healthy validation endpoint never means an application runtime exists.
    deployable = bool(report.get("can_host_customer_applications")) and not report.get("issues")
    result = {"environment_id": args.environment, "requested_mode": args.mode,
              "candidate_workers": selected, "desired_replicas": len(selected),
              "deployable": deployable, "performed_mutations": False,
              "issues": report.get("issues", []) if not deployable else [],
              "note": "Candidats uniquement. Aucun container, image, DNS, NEG ou LB n'a ete modifie."}
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("Mode :", args.mode, "|", len(selected), "worker(s) cible(s)")
        for item in selected:
            print(f"  {item['name']} [{item['zone']}]")
        print("Deploiement client :", "PRET" if deployable else "BLOQUE (runtime MIG non operationnel)")
        for issue in result['issues']:
            print("BLOQUANT:", issue)
        print("Aucune mutation effectuee.")
    return 0 if deployable else 3


if __name__ == "__main__":
    raise SystemExit(main())
