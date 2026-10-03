"""Download the generated pictures listed in assets/plates/manifest.json.

Stills go to assets/plates/<shot>.png, moving plates to assets/clips/<shot>.mp4.
Files that already exist are kept.

Usage: python3 scripts/fetch_assets.py
"""
import json
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    with open(os.path.join(ROOT, "assets", "plates", "manifest.json")) as fh:
        manifest = json.load(fh)
    base = manifest["base"]
    jobs = []
    for shot, entry in manifest["shots"].items():
        jobs.append((base + entry["plate"], os.path.join(ROOT, "assets", "plates", shot + ".png")))
        if entry.get("clip"):
            jobs.append((base + entry["clip"], os.path.join(ROOT, "assets", "clips", shot + ".mp4")))
    procs = []
    for url, dest in jobs:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if not os.path.exists(dest):
            procs.append(subprocess.Popen(["curl", "-fsS", "--retry", "3", "-o", dest, url]))
    if any(p.wait() for p in procs):
        raise SystemExit("download failed")
    print(f"{len(jobs)} assets ready")


if __name__ == "__main__":
    main()
