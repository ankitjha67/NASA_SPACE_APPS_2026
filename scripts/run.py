#!/usr/bin/env python3
"""One command setup and start for AgniSetu.

    python scripts/run.py            set up whatever is missing, then start the server
    python scripts/run.py --check    only report what is ready and what is not; change nothing

The first run creates the Python environment, installs the pinned packages, installs and builds the
frontend, downloads the NASA FIRMS data and starts the server. Every later run scans the same things
again, repairs only what is missing or out of date, and starts the server.

This file uses the Python standard library only, so it runs before anything is installed. All real
work is done by the project's own commands: `pip`, `npm ci`, `npm run build` and the `agnisetu`
command line (`check`, `ingest-archive`, `refresh-nrt`, `backfill-api`, `serve`).
"""
# Every command started here is a fixed list built in this file from paths inside the repository.
# ruff: noqa: S603

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
VENV = BACKEND / ".venv"
PYPROJECT = BACKEND / "pyproject.toml"

MIN_PYTHON = (3, 11)
MIN_NODE = (20, 19)
FEED_REFRESH_HOURS = 6.0
# Files whose change means the frontend must be built again.
BUILD_INPUTS = ("src", "public", "index.html", "package.json", "package-lock.json", "vite.config.ts", "tsconfig.json")
# Addresses that mean "every network interface". A browser reaches such a server through the loopback address.
ALL_INTERFACES = ("0.0.0.0", "::")  # noqa: S104
LOOPBACK = "127.0.0.1"


class SetupError(RuntimeError):
    """A step that cannot be repaired automatically. The message says what to do."""


# --------------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------------


def say(text: str) -> None:
    print(text, flush=True)


def step(number: int, total: int, title: str) -> None:
    say(f"\n[{number}/{total}] {title}")


def run(command: list[str], cwd: Path, quiet: bool = False) -> None:
    """Run a command and stop with a clear message when it fails."""
    try:
        completed = subprocess.run(command, cwd=cwd, check=False, capture_output=quiet, text=quiet)
    except FileNotFoundError as exc:
        raise SetupError(f"Could not run '{command[0]}'. Is it installed and on the PATH?") from exc
    if completed.returncode != 0:
        detail = ""
        if quiet:
            tail = ((completed.stdout or "") + (completed.stderr or "")).strip().splitlines()[-15:]
            detail = "\n" + "\n".join(tail)
        raise SetupError(f"Command failed with exit code {completed.returncode}: {' '.join(command)}{detail}")


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def npm_command() -> str:
    found = shutil.which("npm.cmd" if os.name == "nt" else "npm") or shutil.which("npm")
    if not found:
        raise SetupError(
            "npm was not found. Install Node.js 20.19 or newer from https://nodejs.org/ and run this again."
        )
    return found


def parse_version(text: str) -> tuple[int, ...]:
    """'v24.16.0' -> (24, 16, 0). Anything after the numbers is ignored."""
    match = re.search(r"(\d+(?:\.\d+)*)", text)
    return tuple(int(part) for part in match.group(1).split(".")) if match else ()


def read_pins(pyproject_text: str) -> dict[str, str]:
    """Exact versions the project pins, from the dependency lists of pyproject.toml.

    Read with a pattern, not a TOML parser, because the parser only joined the standard library in
    Python 3.11 and this file must be able to say "your Python is too old" on anything older.
    """
    pins: dict[str, str] = {}
    for name, version in re.findall(r'"([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==([0-9][^"\s;]*)"', pyproject_text):
        pins[name.lower().replace("_", "-")] = version
    return pins


def newest_change(paths: list[Path]) -> float:
    latest = 0.0
    for path in paths:
        if path.is_dir():
            for item in path.rglob("*"):
                if item.is_file():
                    latest = max(latest, item.stat().st_mtime)
        elif path.exists():
            latest = max(latest, path.stat().st_mtime)
    return latest


def port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1.0)
        return probe.connect_ex((host, port)) != 0


# --------------------------------------------------------------------------------------------
# Checks. Each returns a list of problems; an empty list means the part is ready.
# --------------------------------------------------------------------------------------------


def python_problems() -> list[str]:
    if sys.version_info[:2] < MIN_PYTHON:
        found = ".".join(str(v) for v in sys.version_info[:3])
        wanted = ".".join(str(v) for v in MIN_PYTHON)
        return [
            f"Python {found} is too old. AgniSetu needs Python {wanted} or newer: https://www.python.org/downloads/"
        ]
    return []


_PROBE = """
import importlib.metadata as metadata, json, sys
found = {}
for name in json.loads(sys.argv[1]):
    try:
        found[name] = metadata.version(name)
    except metadata.PackageNotFoundError:
        found[name] = None
try:
    import agnisetu
    found["agnisetu"] = agnisetu.__file__
except Exception:
    found["agnisetu"] = None
print(json.dumps(found))
"""


def version_differences(pins: dict[str, str], installed: dict[str, str | None]) -> list[str]:
    problems = []
    for name, wanted in sorted(pins.items()):
        have = installed.get(name)
        if have is None:
            problems.append(f"{name} {wanted} is not installed")
        elif have != wanted:
            problems.append(f"{name} is {have}, the project pins {wanted}")
    return problems


def backend_problems() -> list[str]:
    """Compare what is installed in the environment with the versions pinned in pyproject.toml."""
    python = venv_python()
    if not python.exists():
        return ["the Python environment backend/.venv does not exist"]
    pins = read_pins(PYPROJECT.read_text(encoding="utf-8"))
    result = subprocess.run(
        [str(python), "-c", _PROBE, json.dumps(sorted(pins))], cwd=BACKEND, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        return ["the Python environment backend/.venv is broken and could not be read"]
    installed = json.loads(result.stdout)
    problems = version_differences(pins, installed)
    if not installed.get("agnisetu"):
        problems.append("the agnisetu package itself is not installed in the environment")
    return problems


def node_problems() -> list[str]:
    node = shutil.which("node")
    if not node:
        return ["Node.js was not found. Install Node.js 20.19 or newer from https://nodejs.org/"]
    result = subprocess.run([node, "--version"], capture_output=True, text=True, check=False)
    if parse_version(result.stdout)[:2] < MIN_NODE:
        wanted = ".".join(str(v) for v in MIN_NODE)
        return [
            f"Node.js {result.stdout.strip() or 'of unknown version'} is too old. The frontend needs {wanted} or newer."
        ]
    return []


def lock_differences(lock: dict, installed: dict) -> list[str]:
    """Compare package-lock.json with what npm recorded in node_modules/.package-lock.json.

    Optional packages are built for one operating system each, so the ones npm skipped on this
    machine are not counted as missing.
    """
    wanted = {k: v for k, v in lock.get("packages", {}).items() if k.startswith("node_modules/")}
    have = installed.get("packages", {})
    differences = []
    for name, entry in sorted(wanted.items()):
        found = have.get(name)
        short = name.replace("node_modules/", "")
        if found is None:
            if not entry.get("optional"):
                differences.append(f"{short} {entry.get('version')} is not installed")
        elif found.get("version") != entry.get("version"):
            differences.append(f"{short} is {found.get('version')}, package-lock.json pins {entry.get('version')}")
    return differences


def frontend_package_problems() -> list[str]:
    """node_modules must hold exactly the versions pinned in package-lock.json."""
    recorded = FRONTEND / "node_modules" / ".package-lock.json"
    if not recorded.exists():
        return ["frontend packages are not installed"]
    try:
        lock = json.loads((FRONTEND / "package-lock.json").read_text(encoding="utf-8"))
        installed = json.loads(recorded.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["the list of installed frontend packages could not be read"]
    differences = lock_differences(lock, installed)
    if len(differences) > 5:
        return [*differences[:5], f"and {len(differences) - 5} more"]
    return differences


def frontend_build_problems() -> list[str]:
    built = FRONTEND / "dist" / "index.html"
    if not built.exists():
        return ["the frontend has not been built"]
    if newest_change([FRONTEND / name for name in BUILD_INPUTS]) > built.stat().st_mtime:
        return ["frontend source files changed since the last build"]
    return []


def data_status() -> dict:
    """Ask the application what its store holds. No network access."""
    result = subprocess.run(
        [str(venv_python()), "-m", "agnisetu.cli", "check"], cwd=BACKEND, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        tail = "\n".join((result.stderr or result.stdout).strip().splitlines()[-8:])
        raise SetupError(f"The data check failed:\n{tail}")
    return json.loads(result.stdout)


def feeds_are_stale(status: dict) -> bool:
    hours = status["recent"]["hours_since_feed_refresh"]
    return hours is None or hours > FEED_REFRESH_HOURS


def data_problems(status: dict) -> list[str]:
    problems = []
    archive = status["archive"]
    if archive["missing"]:
        problems.append(f"{len(archive['missing'])} yearly archive files are not downloaded yet")
    if archive["recheck"]:
        problems.append(
            f"{len(archive['recheck'])} archive files were not published at the last look; time to ask again"
        )
    if archive["files_ok"] and status["recent"]["feed_days_missing"]:
        problems.append(f"{status['recent']['feed_days_missing']} of the last days have no data yet")
    elif archive["files_ok"] and feeds_are_stale(status):
        problems.append("the live 7 day feeds have not been read recently")
    if status["gap"]["days_missing"]:
        gap = status["gap"]
        problems.append(f"{gap['days_missing']} days between {gap['first']} and {gap['last']} have no data")
    return problems


# --------------------------------------------------------------------------------------------
# Repairs
# --------------------------------------------------------------------------------------------


def install_backend() -> None:
    python = venv_python()
    if not python.exists():
        say("  creating backend/.venv")
        run([sys.executable, "-m", "venv", str(VENV)], cwd=BACKEND)
    say("  installing the pinned Python packages (a few minutes the first time)")
    pip = [str(python), "-m", "pip", "install", "--quiet", "--disable-pip-version-check"]
    run([*pip, "--upgrade", "pip"], cwd=BACKEND, quiet=True)
    run([*pip, "-e", ".[dev]"], cwd=BACKEND, quiet=True)


def install_frontend_packages() -> None:
    say("  installing the frontend packages from package-lock.json (npm ci)")
    run([npm_command(), "ci", "--no-audit", "--no-fund"], cwd=FRONTEND, quiet=True)


def build_frontend() -> None:
    say("  building the frontend (npm run build)")
    run([npm_command(), "run", "build"], cwd=FRONTEND, quiet=True)


def ensure_env_file() -> bool:
    target, example = ROOT / ".env", ROOT / ".env.example"
    if target.exists() or not example.exists():
        return False
    shutil.copyfile(example, target)
    return True


def cli(*arguments: str) -> bool:
    """Run one agnisetu command, letting its progress show. Network trouble is reported, not fatal."""
    return (
        subprocess.run([str(venv_python()), "-m", "agnisetu.cli", *arguments], cwd=BACKEND, check=False).returncode == 0
    )


def fetch_data(status: dict) -> dict:
    archive = status["archive"]
    if archive["missing"] or archive["recheck"]:
        countries = ", ".join(c.replace("_", " ") for c in status["countries"])
        if archive["files_ok"] == 0:
            size = f"the full record for {countries}; about 1.8 GB and 10 to 30 minutes for the default three countries"
        else:
            size = f"only the files still missing for {countries}"
        say(f"  downloading the NASA FIRMS yearly archive: {size}.")
        say("  This can be interrupted and resumed.")
        if not cli("ingest-archive"):
            say("  ! some archive files could not be downloaded. Run this script again to retry them.")
        status = data_status()
    if status["archive"]["files_ok"] and (status["recent"]["feed_days_missing"] or feeds_are_stale(status)):
        say("  reading the live 7 day feeds")
        if not cli("refresh-nrt"):
            say("  ! the live feeds could not be read. The server tries again every three hours.")
        status = data_status()
    if status["gap"]["days_missing"]:
        gap = status["gap"]
        if status["map_key_configured"]:
            say(f"  filling {gap['days_missing']} days ({gap['first']} to {gap['last']}) through the FIRMS area API")
            if not cli("backfill-api"):
                say("  ! the backfill did not finish. Run this script again to continue it.")
            status = data_status()
        else:
            say(
                f"  note: {gap['days_missing']} days from {gap['first']} to {gap['last']} lie between the yearly\n"
                "  archive and the live feed. They need a free NASA FIRMS key: request one at\n"
                "  https://firms.modaps.eosdis.nasa.gov/api/map_key/ , put it in .env as\n"
                "  AGNISETU_FIRMS_MAP_KEY and run this script again. Everything else works without it."
            )
    return status


# --------------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------------


def report(title: str, problems: list[str]) -> bool:
    if problems:
        say(f"  needs work: {title}")
        for problem in problems:
            say(f"    - {problem}")
        return False
    say(f"  ok: {title}")
    return True


def check_only() -> int:
    """Report the state of every part without changing anything. Exit code 0 means ready to serve."""
    say("AgniSetu readiness check (nothing is changed)")
    ready = report("Python version", python_problems())
    backend_ok = report("Python environment and pinned packages", backend_problems())
    ready &= backend_ok
    ready &= report("Node.js", node_problems())
    ready &= report("frontend packages", frontend_package_problems())
    ready &= report("frontend build", frontend_build_problems())
    if backend_ok:
        status = data_status()
        problems = data_problems(status)
        archive = status["archive"]
        if not archive["files_ok"]:
            ready = False
        title = f"NASA FIRMS data ({archive['files_ok']} archive files, through {status['recent']['last_covered_day']})"
        report(title, problems)
        if problems and archive["files_ok"]:
            say("    (the server can start; the script would fetch these first)")
    else:
        say("  unknown: NASA FIRMS data (needs the Python environment first)")
    say("\nReady to serve." if ready else "\nNot ready. Run: python scripts/run.py")
    return 0 if ready else 1


def set_up_and_serve(args: argparse.Namespace) -> int:
    total = 6
    step(1, total, "Python")
    problems = python_problems()
    if problems:
        raise SetupError(problems[0])
    say(f"  ok: Python {'.'.join(str(v) for v in sys.version_info[:3])}")

    step(2, total, "Python environment and pinned packages")
    problems = backend_problems()
    if problems:
        for problem in problems[:6]:
            say(f"  - {problem}")
        install_backend()
        problems = backend_problems()
        if problems:
            raise SetupError("The Python packages still do not match after installing:\n  " + "\n  ".join(problems))
    say(f"  ok: {len(read_pins(PYPROJECT.read_text(encoding='utf-8')))} pinned packages match pyproject.toml")

    step(3, total, "Frontend packages")
    problems = node_problems()
    if problems:
        raise SetupError(problems[0])
    problems = frontend_package_problems()
    if problems:
        for problem in problems:
            say(f"  - {problem}")
        install_frontend_packages()
        problems = frontend_package_problems()
        if problems:
            raise SetupError("The frontend packages still do not match after npm ci:\n  " + "\n  ".join(problems))
    say("  ok: node_modules matches package-lock.json")

    step(4, total, "Frontend build")
    problems = frontend_build_problems()
    if problems:
        say(f"  - {problems[0]}")
        build_frontend()
    say("  ok: frontend/dist is up to date")

    step(5, total, "Configuration and NASA FIRMS data")
    if ensure_env_file():
        say("  created .env from .env.example (every setting is optional)")
    status = data_status()
    if args.skip_data:
        say("  skipped on request (--skip-data)")
    else:
        if data_problems(status):
            status = fetch_data(status)
        left = data_problems(status)
        archive = status["archive"]
        if not archive["files_ok"]:
            say("  ! no fire data is available yet. The server starts and explains what to do.")
        elif left:
            say(f"  ok with notes: {archive['files_ok']} archive files; " + "; ".join(left))
        else:
            say(f"  ok: {archive['files_ok']} archive files, every day through {status['recent']['last_covered_day']}")

    step(6, total, "Server")
    if args.no_serve:
        say("  not started on request (--no-serve). Everything is ready.")
        return 0
    host = args.host or status["server"]["host"]
    port = args.port or int(status["server"]["port"])
    reach = LOOPBACK if host in ALL_INTERFACES else host
    if not port_is_free(reach, port):
        raise SetupError(
            f"Port {port} is already in use. AgniSetu may already be running at http://{reach}:{port} . "
            "Stop it, or choose another port with --port."
        )
    url = f"http://{reach}:{port}"
    say(f"  starting AgniSetu at {url}  (Ctrl+C to stop)")
    if args.open:
        webbrowser.open(url)
    command = [str(venv_python()), "-m", "agnisetu.cli", "serve", "--host", host, "--port", str(port)]
    try:
        return subprocess.call(command, cwd=BACKEND)
    except KeyboardInterrupt:
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Set up whatever AgniSetu is missing, then start the server.")
    parser.add_argument("--check", action="store_true", help="only report what is ready; change nothing")
    parser.add_argument("--skip-data", action="store_true", help="do not download or refresh NASA FIRMS data")
    parser.add_argument("--no-serve", action="store_true", help="set everything up but do not start the server")
    parser.add_argument("--open", action="store_true", help="open the app in the default browser once it starts")
    parser.add_argument("--host", default=None, help="address to bind (default: AGNISETU_HOST or 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="port to bind (default: AGNISETU_PORT or 8000)")
    args = parser.parse_args(argv)
    try:
        return check_only() if args.check else set_up_and_serve(args)
    except SetupError as exc:
        say(f"\nStopped: {exc}")
        return 1
    except KeyboardInterrupt:
        say("\nInterrupted. Run the script again to continue where it stopped.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
