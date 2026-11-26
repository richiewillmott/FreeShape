"""Refresh Qt Linguist source and runtime catalogs, or check extraction in CI."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
LOCALE = ROOT / "freeshape" / "locale"


def entries(path):
    result = {}
    for context in ET.parse(path).getroot().findall("context"):
        for message in context.findall("message"):
            translation = message.find("translation")
            key = (context.findtext("name"), message.findtext("source"), message.findtext("comment"))
            result[key] = (translation.get("type", "finished"), translation.text or "")
    return result


def refresh(ts):
    files = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "freeshape").rglob("*.py"))
    subprocess.run(["pyside6-lupdate", *files, "-locations", "none",
                    "-disable-heuristic", "sametext", "-disable-heuristic", "similartext",
                    "-ts", str(ts)], cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report missing/stale messages; fail if extraction is out of date")
    args = parser.parse_args()
    ok = True
    for ts in sorted(LOCALE.glob("*.ts")):
        if args.check:
            with tempfile.TemporaryDirectory() as work:
                copy = Path(work) / ts.name
                shutil.copyfile(ts, copy)
                refresh(copy)
                before, after = entries(ts), entries(copy)
                if before != after:
                    ok = False
                    print(f"{ts.name}: source catalog is out of date; run tools/update_translations.py")
                for key, (status, _) in after.items():
                    if status in ("unfinished", "vanished", "obsolete"):
                        print(f"{ts.name}: {status}: {key[1]}")
                if not ts.with_suffix(".qm").is_file():
                    ok = False
                    print(f"{ts.name}: compiled catalog missing")
        else:
            refresh(ts)
            subprocess.run(["pyside6-lrelease", "-nounfinished", str(ts), "-qm", str(ts.with_suffix(".qm"))], check=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
