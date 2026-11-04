"""Scoped runtime logs via the broker; no Docker socket or direct log queries in Django."""
from apps.deployments.runs.autokom_socket import request_control
from apps.deployments.runs.worker import redact


def fetch_runtime_logs(env, limit=100):
    result=request_control('runtime.logs',{'environment_id':str(env.pk),'lines':min(limit,100)},timeout=20)
    return [{'timestamp':'','message':redact(line)} for line in result.get('logs',[])][-limit:]
