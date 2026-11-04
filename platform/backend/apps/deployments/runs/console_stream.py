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
