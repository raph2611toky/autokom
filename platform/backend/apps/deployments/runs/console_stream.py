"""Bounded parsing for Jenkins progressiveText, without Django or credentials.

Only AUTOKOM-prefixed, complete lines are exposed by the deployment-log API.
These markers are informational: they are NOT security attestations about builds.
"""
from __future__ import annotations

import re

# Jenkins Timestamps Plugin normally prefixes lines with HH:MM:SS.
_TIMESTAMP = r'(?:\d{2}:\d{2}:\d{2}(?:\.\d+)?\s+|\[[0-9TZ: .+\-]{12,45}\]\s+)?'
_MARKER = re.compile(
    r'^' + _TIMESTAMP +
    r'(AUTOKOM_LOG:[^\r\n]{1,700}|AUTOKOM_STAGE:'
    r'(?:source|sast|sca|build|release|health):'
    r'(?:RUNNING|SUCCESS|FAILED|SKIPPED))$'
)


def parse_marker(line: str) -> str | None:
    """Recognize one complete log marker; ignore embedded customer output."""
    if not isinstance(line, str):
        return None
    match = _MARKER.fullmatch(line.rstrip('\r').strip())
    return match.group(1) if match else None


def consume_chunk(previous: str, chunk: str, *, final: bool = False):
    """Return (markers, pending). Never discard lines merely due to line count.

    Jenkins provides byte offsets independently; caller stores them with pending
    and log entries in one database transaction.
    """
    previous = previous if isinstance(previous, str) else ''
    chunk = chunk if isinstance(chunk, str) else ''
    combined = previous[-2000:] + chunk
    if len(combined) > 66000:
        raise ValueError('Segment Jenkins trop long pour être traité sans perte.')
    lines = combined.split('\n')
    markers = []
    for line in lines[:-1]:
        marker = parse_marker(line)
        if marker:
            markers.append(marker)
    pending = lines[-1][-2000:]
    if final:
        marker = parse_marker(pending)
        if marker:
            markers.append(marker)
        pending = ''
    return markers, pending


# Diagnostic client: ne jamais publier la console Jenkins brute. Les lignes
# affichées sont une liste positive de messages d'outillage courants, jamais
# un dump arbitraire des processus ou des variables d'environnement.
_ANSI = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*\x07)')
_TECH_SAFE = (
    re.compile(r'^\[Pipeline\] (?:Start of Pipeline|End of Pipeline|node|stage|\{|\}|sh|echo|checkout|script|timestamps|timeout|withCredentials|[a-zA-Z]+)$'),
    re.compile(r'^Running on [A-Za-z0-9_.-]+(?: in .{0,180})?$'),
    re.compile(r'^\s*> git (?:version|rev-parse|fetch|checkout|config|init|remote|cat-file|show|log|status)(?: .{0,250})?$'),
    re.compile(r'^(?:Cloning|Fetching|Checking out|Using|Selected Git installation|Commit message|Recommended git tool)(?: .{0,250})?$', re.I),
    re.compile(r'^(?:npm (?:warn|error|ERR!)|vite v[0-9]|transforming|rendering chunks|computing gzip size|built in|\s*\d+ modules transformed)(?:.{0,220})$', re.I),
    re.compile(r'^(?:ERROR|Error|fatal|FAILURE|WARNING|WARN|Caused by): .{1,240}$'),
    re.compile(r'^#\d+(?:\s+\[[^\]\r\n]{1,90}\])? .{1,240}$'),
    re.compile(r'^\+ python3 /opt/autokom/source/scripts/runtime/autokom_agent_local\.py (?:metadata|sast|sca|build)$'),
)
_SENSITIVE = re.compile(
    r'(?i)(?:\b(?:password|passwd|secret|token|authorization|bearer|cookie|credentials?|private[_ -]?key|access[_ -]?key|session[_ -]?key)\b'
    r'|github_pat_|gh[pousr]_[a-z0-9_]+|-----BEGIN|\b[A-Z_]*(?:KEY|TOKEN|PASSWORD|SECRET)\s*=)'
)
_PRIVATE_URI = re.compile(r'https?://[^\s/@:]+:[^\s/@]+@', re.I)
_OPAQUE_SECRET = re.compile(r'(?<![a-zA-Z0-9])(?:[A-Za-z0-9_\-/+]{40,}={0,2}|[a-fA-F0-9]{32,})(?![A-Za-z0-9])')


def safe_technical_line(line):
    """Allowlisted diagnostic line for authenticated project owners only.

    Arbitrary application stdout is deliberately *not* passed through. That
    output could contain real environment variables, secrets and user data.
    """
    if not isinstance(line, str):
        return None
    value = _ANSI.sub('', line).replace('\r', '').strip()
    value = re.sub(r'^(?:\d{2}:\d{2}:\d{2}(?:\.\d+)?\s+)', '', value)
    if not value or len(value)>360 or any(ord(c)<32 for c in value):
        return None
    # A known Jenkins configuration problem is returned without its identifier.
    if value.startswith('ERROR: Could not find credentials entry'):
        return 'Référence d’identifiants du job introuvable : intervention administrateur nécessaire.'
    if _PRIVATE_URI.search(value) or _SENSITIVE.search(value) or _OPAQUE_SECRET.search(value):
        return None
    if not any(pattern.fullmatch(value) for pattern in _TECH_SAFE):
        return None
    return value[:350]


def consume_diagnostic_chunk(previous, chunk, *, final=False):
    """Return complete permitted records, preserving byte-split console lines.

    Stage markers are preserved for state transitions; non-marker records get
    an AUTOKOM_TECH marker and remain untrusted informational evidence.
    """
    previous = previous if isinstance(previous, str) else ''
    chunk = chunk if isinstance(chunk, str) else ''
    combined = previous[-2000:] + chunk
    if len(combined) > 66000:
        raise ValueError('Segment Jenkins trop long pour être traité sans perte.')
    parts = combined.split('\n')
    records = []
    for line in parts[:-1]:
        marker = parse_marker(line)
        if marker:
            records.append(marker)
        else:
            safe = safe_technical_line(line)
            if safe:
                records.append('AUTOKOM_TECH:' + safe)
    pending = parts[-1][-2000:]
    if final:
        marker = parse_marker(pending)
        if marker:
            records.append(marker)
        else:
            safe = safe_technical_line(pending)
            if safe:
                records.append('AUTOKOM_TECH:' + safe)
        pending = ''
    return records, pending
