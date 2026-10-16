#!/usr/bin/env python3
"""
Download an official Alertmanager release for the differential tests and print the
directory that holds its `alertmanager` and `amtool` binaries.

    python scripts/fetch_alertmanager.py 0.34.1
    python scripts/fetch_alertmanager.py 0.34.1 --sha256 <hex>   # pin the tarball's hash

The tarball comes from github.com/prometheus/alertmanager/releases and must match the
release's sha256sums.txt and, with --sha256, the pinned hash too (CI pins its matrix).
Binaries are cached in .cache/alertmanager/<version>/ at the repository root, so a second
run is offline.
"""

import argparse
import hashlib
import io
import os
import platform
import shutil
import sys
import tarfile
import urllib.request

RELEASES = "https://github.com/prometheus/alertmanager/releases/download"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BINARIES = ("alertmanager", "amtool")


def _platform() -> str:
    system = platform.system().lower()
    if system not in ("linux", "darwin", "freebsd", "netbsd", "openbsd"):
        sys.exit(f"no Alertmanager release tarball for {platform.system()}")
    machine = platform.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(machine)
    if arch is None:
        sys.exit(f"no Alertmanager release tarball for the {machine} architecture")
    return f"{system}-{arch}"


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310 (fixed https URL)
        return resp.read()


def fetch(version: str, sha256: str = "", dest: str = "") -> str:
    version = version.lstrip("v")
    dest = dest or os.path.join(ROOT, ".cache", "alertmanager", version)
    if all(os.access(os.path.join(dest, b), os.X_OK) for b in BINARIES):
        try:
            with open(os.path.join(dest, "tarball.sha256")) as f:
                cached = f.read().strip()
        except OSError:
            cached = ""
        if not sha256 or sha256.lower() == cached:
            return dest

    name = f"alertmanager-{version}.{_platform()}"
    tarball = _get(f"{RELEASES}/v{version}/{name}.tar.gz")
    digest = hashlib.sha256(tarball).hexdigest()
    sums = _get(f"{RELEASES}/v{version}/sha256sums.txt").decode()
    listed = {line.split()[1]: line.split()[0] for line in sums.splitlines() if line.strip()}
    if listed.get(f"{name}.tar.gz") != digest:
        sys.exit(f"{name}.tar.gz: sha256 {digest} does not match the release's sha256sums.txt")
    if sha256 and sha256.lower() != digest:
        sys.exit(f"{name}.tar.gz: sha256 {digest} does not match the pinned {sha256}")

    # Extract only the two binaries, by name, so nothing else in the archive is written.
    tmp = dest + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
        for binary in BINARIES:
            member = tar.getmember(f"{name}/{binary}")
            if not member.isfile():
                sys.exit(f"{name}/{binary} in the tarball is not a regular file")
            src = tar.extractfile(member)
            path = os.path.join(tmp, binary)
            with open(path, "wb") as out:
                shutil.copyfileobj(src, out)
            os.chmod(path, 0o755)
    with open(os.path.join(tmp, "tarball.sha256"), "w") as f:
        f.write(digest + "\n")
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(tmp, dest)
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("version", help="Alertmanager version, e.g. 0.34.1")
    parser.add_argument("--sha256", default="", help="expected sha256 of the tarball")
    parser.add_argument("--dest", default="", help="directory for the binaries")
    args = parser.parse_args()
    print(fetch(args.version, args.sha256, args.dest))


if __name__ == "__main__":
    main()
