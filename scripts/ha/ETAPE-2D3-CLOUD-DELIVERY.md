# AUTOKOM HA - Application delivery engine 2D.3 (staging)

**Do not activate customer DNS or claim public HTTPS HA from this stage.** The
current AUTOKOM primary remains the serving HTTPS origin and rollback fallback.

## Deployment behavior

- Default `high_availability_requested=false`: exactly one healthy worker.
- Explicit HA with active entitlement: exactly two different GCP zones.
- The release is built and health-checked locally by Jenkins as before.
- The broker records safe non-secret declarative metadata, and the installed
  autopublisher uploads the immutable Docker image digest automatically.
- A root-only controller, running every 90 seconds, distributes desired-state
  JSON to a private, regional, versioned GCS bucket.
- An agent on each MIG worker reads only its own object, pulls an immutable
  image digest, starts a constrained container in its own **internal** Docker
  network, and advertises the application through local Nginx on port 8080.
- The controller creates app-scoped zonal GCE_VM_IP_PORT NEGs, app-specific
  backend services and health checks, adding the application's HTTP host rule
  only after it sees the required number of healthy replicas.
- Worker replacement: new instance name -> controller reallocates -> worker
  agent restores the image and the controller retargets the zonal NEG.

## Scope and explicit protections

- **No automated public DNS change, HTTPS certificate management or production
  domain cutover in this stage.** Test through the existing LB using an HTTP
  `Host:` header, but keep customer sites on primary HTTPS.
- Projects containing runtime variables, secret references or paired
  frontend/backend stacks are *not* cloud-promoted by this iteration; local
  runtime is unchanged. This avoids leaking credentials or breaking private
  service-to-service URLs.
- Worker containers use isolated Docker networks without external network
  egress, keeping the GCE metadata service inaccessible from untrusted apps.
- The MIG's existing `8080/healthz` is independent from per-application
  `/__autokom_health/<environment-UUID>` health checks.
- A GCP load balancer health check alone is **not** validation of HTTPS or DNS.
  A load balancer may carry many application-specific backend services.
- The MIG autoscaler can add/replace workers; capacity is bounded by 1500 MiB
  and 2000 millicores per worker in this implementation. Applications which
  do not fit remain on local HTTPS and are reported `INSUFFICIENT_CAPACITY`.
- The new bucket and IAM bindings incur cloud costs. Grant the Terraform
  service account storage-admin in the project only for setup, then reduce it.

## One-time prerequisite

The Terraform switch `enable_app_delivery=true` creates the GCS state bucket
with versioning and grants the primary writer + worker readers. The template
replaces existing MIG workers with the application delivery agent.

**Inspect the Terraform plan carefully.** Never accept MIG, LB, autoscaler,
Cloud NAT or other service deletion. The template replacement and controlled
MIG instance rolling update are expected.

After the two new workers are `HEALTHY`, run:

```text
sudo autokom iac ha delivery --check
sudo autokom iac ha delivery --apply
sudo autokom iac ha delivery --status
```

`--apply` installs and enables the controller service+timer, and triggers a
single guarded reconciliation. The service is configured so no customer DNS or
HTTPS is changed.

## Check HTTP routing without touching DNS

```text
curl -i --resolve 'example.auto.ikom.mg:80:35.186.239.102' \
  http://example.auto.ikom.mg/
```

Use the actual managed application hostname, never an example placeholder.
The expected cloud health is 1/1 for STANDARD or 2/2 for explicit HA. This
validates cloud HTTP staging only. HTTPS production migration is a separate
transaction requiring a valid certificate and per-host DNS verification.
