#!/usr/bin/env python3
"""Fallback for Jenkins APT mirror outages; never install unverified .deb files.

The candidate package version, size and SHA256 must come from the APT package
index, which was verified against Jenkins' 2026 signing key during apt update.
This script only downloads; Ansible installs the verified local file with APT.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

MIRRORS = (
    "https://mirrors.xmission.com/jenkins/debian-stable/",
    "https://mirror.ossplanet.net/jenkins/debian-stable/",
)
TARGET_DIR = Path("/var/cache/autokom/jenkins")
MAX_BYTES = 250 * 1024 * 1024


class DownloadError(RuntimeError):
    pass


def command_output(args):
    result = subprocess.run(args, text=True, capture_output=True, timeout=30, check=False)
    if result.returncode != 0:
        raise DownloadError(f"Metadata APT unavailable: {args[1]}")
    return result.stdout


def candidate_metadata(policy_output: str, show_output: str):
    m = re.search(r"^\s*Candidate:\s*([^\s]+)\s*$", policy_output, re.M)
    if not m or m.group(1) in ("(none)", "none"):
        raise DownloadError("No Jenkins LTS candidate in the signed APT index")
    version = m.group(1)
    if not re.fullmatch(r"[0-9][A-Za-z0-9.+~:-]{0,80}", version):
        raise DownloadError("Invalid Jenkins candidate version")
    for paragraph in re.split(r"\n\s*\n", show_output.strip()):
        fields = dict(re.findall(r"^([A-Za-z0-9-]+):[ \t]*(.+)$", paragraph, re.M))
        if fields.get("Package") != "jenkins" or fields.get("Version") != version:
            continue
        digest = fields.get("SHA256", "").strip().lower()
        length_text = fields.get("Size", "")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise DownloadError("Signed APT metadata contains no valid SHA256")
        if not re.fullmatch(r"[0-9]+", length_text):
            raise DownloadError("Signed APT metadata contains no valid package length")
        length = int(length_text)
        if not (1_000_000 <= length <= MAX_BYTES):
            raise DownloadError("APT package length is outside safe limits")
        # Jenkins distributes the versioned Debian package as architecture 'all'.
        if fields.get("Architecture") != "all":
            raise DownloadError("Unexpected Jenkins package architecture")
        return version, digest, length
    raise DownloadError("Jenkins candidate version absent from signed APT package index")


def matches_checksum(path: Path, digest: str, length: int) -> bool:
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size != length:
            return False
        hasher = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                hasher.update(chunk)
        return hasher.hexdigest() == digest
    except OSError:
        return False


def download_verified(version: str, digest: str, length: int, target_dir: Path = TARGET_DIR):
    target_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(target_dir, 0o700)
    filename = f"jenkins_{version}_all.deb"
    dest = target_dir / filename
    if matches_checksum(dest, digest, length):
        return dest
    dest.unlink(missing_ok=True)
    errors = []
    for mirror in MIRRORS:
        for attempt in range(2):
            temp_path = None
            try:
                fd, name = tempfile.mkstemp(prefix=".jenkins-", suffix=".part", dir=target_dir)
                temp_path = Path(name)
                digest_builder = hashlib.sha256()
                size = 0
                request = urllib.request.Request(mirror + filename, headers={"User-Agent": "AUTOKOM-Jenkins-Installer/1"})
                with os.fdopen(fd, "wb") as output, urllib.request.urlopen(request, timeout=45) as response:
                    if not response.geturl().startswith("https://"):
                        raise DownloadError("Mirror attempted an insecure HTTP redirect")
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > length:
                            raise DownloadError("Downloaded file exceeds signed APT length")
                        output.write(chunk)
                        digest_builder.update(chunk)
                if size != length or digest_builder.hexdigest() != digest:
                    raise DownloadError("Downloaded file does not match signed APT SHA256/length")
                os.chmod(temp_path, 0o600)
                temp_path.replace(dest)
                return dest
            except (OSError, ValueError, urllib.error.URLError, DownloadError) as exc:
                # No URLs / HTTP responses are copied into logs: they could be huge.
                errors.append(f"{mirror.split('/')[2]} attempt {attempt + 1}: {type(exc).__name__}")
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)
    raise DownloadError("All trusted Jenkins mirrors failed verification/download (" + "; ".join(errors) + ")")


def main():
    policy = command_output(["apt-cache", "policy", "jenkins"])
    show = command_output(["apt-cache", "show", "jenkins"])
    version, digest, length = candidate_metadata(policy, show)
    dest = download_verified(version, digest, length)
    print(dest)  # stdout deliberately contains only the verified package path.


if __name__ == "__main__":
    try:
        main()
    except (DownloadError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"AUTOKOM_JENKINS_MIRROR_FAILED: {str(exc)}", file=sys.stderr)
        sys.exit(1)
