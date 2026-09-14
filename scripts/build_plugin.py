#!/usr/bin/env python3
"""Build a distributable zip of the D2S Browser QGIS plugin.

Stages only the files QGIS needs, refuses to ship anything on a forbidden
list, runs the security scanners plugins.qgis.org runs with Bandit's full
rule set, and writes a byte-reproducible archive.
"""

import argparse
import configparser
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

PLUGIN_NAME = "d2s_browser"

INCLUDE_FILES = [
    "__init__.py",
    "d2s_browser.py",
    "d2s_browser_dialog.py",
    "d2s_browser_workers.py",
    "d2s_browser_dialog_base.ui",
    "resources.py",
    "metadata.txt",
    "icon.png",
    "LICENSE",
]

# Vendored dependencies. Only d2spy; requests comes from QGIS.
INCLUDE_TREES = ["libs"]

EXCLUDE_DIRS = {
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".git",
    ".ruff_cache",
}

EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".so", ".pyd", ".dll", ".zip"}

# Anything matching these must never reach the archive. Guards against a
# future edit quietly reintroducing a scanner finding.
FORBIDDEN = [
    r"\.buildinfo$",
    r"direct_url\.json$",
    r"(^|/)INSTALLER$",
    r"(^|/)REQUESTED$",
    r"(^|/)bin/",
    r"(^|/)libs/(requests|urllib3|certifi|idna|charset_normalizer)(/|-)",
    r"(^|/)help/",
    r"(^|/)test/",
    r"(^|/)scripts/",
    r"(^|/)i18n/",
    r"(^|/)assets/",
    r"plugin_upload\.py$",
    r"d2s_qgis_client\.py$",
    r"pb_tool\.cfg$",
    r"Makefile$",
    r"\.qrc$",
    r"(^|/)\.git",
]

# Fixed timestamp so repeated builds are byte-identical.
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def read_version(plugin_dir: Path) -> str:
    parser = configparser.ConfigParser(strict=False)
    parser.read(plugin_dir / "metadata.txt", encoding="utf-8")
    return parser["general"]["version"].strip()


def should_skip(path: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in path.parts):
        return True
    return path.suffix in EXCLUDE_SUFFIXES


def collect(plugin_dir: Path) -> list[Path]:
    """Return plugin-relative paths to ship, sorted."""
    selected: list[Path] = []

    for name in INCLUDE_FILES:
        source = plugin_dir / name
        if not source.is_file():
            sys.exit(f"error: required file is missing: {name}")
        selected.append(Path(name))

    for tree in INCLUDE_TREES:
        root = plugin_dir / tree
        if not root.is_dir():
            sys.exit(f"error: required directory is missing: {tree}")
        for source in root.rglob("*"):
            if source.is_file():
                relative = source.relative_to(plugin_dir)
                if not should_skip(relative):
                    selected.append(relative)

    return sorted(set(selected))


# plugins.qgis.org treats these as developer config: the scan passes but the
# version is held for manual review, and its results email is mis-templated
# as BLOCKED. Any other dotfile is flagged outright. Ship none.
SCAN_CONFIG_FILES = {".bandit", ".secrets.baseline", ".flake8"}


def check_forbidden(paths: list[Path]) -> None:
    patterns = [re.compile(p) for p in FORBIDDEN]
    violations = [
        f"{path} (matched {p.pattern})"
        for path in paths
        for p in patterns
        if p.search(str(path))
    ]
    for path in paths:
        if path.name in SCAN_CONFIG_FILES:
            violations.append(f"{path} (scanner config, forces manual review)")
        elif path.name.startswith("."):
            violations.append(f"{path} (dotfile, flagged by plugins.qgis.org)")
    if violations:
        sys.exit(
            "error: forbidden paths would be shipped:\n  "
            + "\n  ".join(violations)
        )


def stage(plugin_dir: Path, paths: list[Path], staging: Path) -> Path:
    root = staging / PLUGIN_NAME
    if staging.exists():
        shutil.rmtree(staging)
    for relative in paths:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(plugin_dir / relative, target)
        target.chmod(0o644)
    return root


def run_scanners(staging: Path) -> None:
    """Run the plugins.qgis.org scanners. Exits non-zero on any finding.

    Bandit runs its full default rule set, stricter than the site's selected
    subset, and targets the parent of the plugin folder as the site does.
    """
    if shutil.which("uvx") is None:
        sys.exit(
            "error: uvx not found, so the security scan cannot run.\n"
            "Install uv, or pass --no-scan to skip (not recommended for a "
            "release build)."
        )

    print("running bandit (full rule set) ...")
    bandit = subprocess.run(
        ["uvx", "bandit", "-r", str(staging), "-f", "json", "--quiet"],
        capture_output=True,
        text=True,
    )
    try:
        results = json.loads(bandit.stdout)["results"]
    except (ValueError, KeyError):
        sys.exit(
            f"error: bandit produced no report.\n{bandit.stderr}"
        )
    if results:
        for r in results:
            print(
                f"  {r['issue_severity']} {r['test_id']} "
                f"{r['filename']}:{r['line_number']}"
            )
        sys.exit(f"error: bandit reported {len(results)} finding(s)")
    print("  no findings")

    print("running detect-secrets ...")
    secrets = subprocess.run(
        [
            "uvx",
            "detect-secrets",
            "scan",
            "--all-files",
            "--exclude-files",
            r"metadata\.txt",
            "--exclude-files",
            r"\.secrets\.baseline",
            str(staging),
        ],
        capture_output=True,
        text=True,
    )
    try:
        found = json.loads(secrets.stdout)["results"]
    except (ValueError, KeyError):
        sys.exit(f"error: could not parse detect-secrets output:\n{secrets.stderr}")
    if found:
        for path, hits in found.items():
            print(f"  {path}: {[h['type'] for h in hits]}")
        sys.exit(f"error: detect-secrets reported findings in {len(found)} file(s)")
    print("  no secrets found")


def write_zip(staged_root: Path, paths: list[Path], out_file: Path) -> None:
    if out_file.exists():
        out_file.unlink()
    with zipfile.ZipFile(out_file, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for relative in paths:
            arcname = f"{PLUGIN_NAME}/{relative.as_posix()}"
            info = zipfile.ZipInfo(arcname, date_time=ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, (staged_root / relative).read_bytes())


def deploy(out_file: Path, profile_dir: Path | None) -> None:
    if profile_dir is None:
        home = Path.home()
        for version in ("QGIS4", "QGIS3"):
            candidate = (
                home
                / ".local/share/QGIS"
                / version
                / "profiles/default/python/plugins"
            )
            if candidate.is_dir():
                profile_dir = candidate
                break
    if profile_dir is None:
        sys.exit("error: could not find a QGIS plugins directory, use --profile-dir")

    target = profile_dir / PLUGIN_NAME
    if target.exists():
        shutil.rmtree(target)
    with zipfile.ZipFile(out_file) as zf:
        zf.extractall(profile_dir)
    print(f"deployed to {target}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="directory for the zip")
    parser.add_argument("--no-scan", action="store_true", help="skip security scan")
    parser.add_argument(
        "--deploy", action="store_true", help="install the zip into QGIS"
    )
    parser.add_argument("--profile-dir", type=Path, help="QGIS plugins directory")
    args = parser.parse_args()

    plugin_dir = Path(__file__).resolve().parent.parent
    version = read_version(plugin_dir)
    out_dir = args.out or plugin_dir.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{PLUGIN_NAME}_v{version}.zip"

    paths = collect(plugin_dir)
    check_forbidden(paths)

    staging = plugin_dir / "build"
    staged_root = stage(plugin_dir, paths, staging)

    if args.no_scan:
        print("skipping security scan (--no-scan)")
    else:
        run_scanners(staging)

    write_zip(staged_root, paths, out_file)
    shutil.rmtree(staging)

    digest = hashlib.sha256(out_file.read_bytes()).hexdigest()
    top = sorted({p.parts[0] for p in paths})
    print(f"\nbuilt {out_file}")
    print(f"  version : {version}")
    print(f"  files   : {len(paths)}")
    print(f"  size    : {out_file.stat().st_size / 1024:.1f} KiB")
    print(f"  sha256  : {digest}")
    print(f"  contents: {', '.join(top)}")

    if args.deploy:
        deploy(out_file, args.profile_dir)


if __name__ == "__main__":
    main()
