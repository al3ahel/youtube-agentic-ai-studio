"""Local build identity and a guard against mixing pulled code with an old process.

Only executable Python and dependency manifests are fingerprinted. Settings,
generated media, documentation and browser assets do not require a Python restart.
No provider, inference, update or remote Git requests are made here.
"""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import platform
import re
import subprocess
import sys

from flask import jsonify, request

_ROOT = Path(__file__).resolve().parent.parent
_PACKAGES = ("agents", "apis", "studio", "uploader", "video")
_FILES = ("gui.py", "config.py", "requirements.txt",
          "renderers/hyperframes/package.json", "renderers/hyperframes/package-lock.json")
_MAX_FILES = 1024
_MAX_ENTRIES = 4096
_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_TOTAL_BYTES = 16 * 1024 * 1024
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_RESTART_MESSAGE = (
    "The project code changed after this app started. Stop this YAAS process with "
    "Ctrl+C in its terminal, then restart gui.py from the updated checkout. "
    "You can still inspect this project or stop a running job."
)


def _runtime_files(root):
    def inventory_error(error):
        raise error

    paths = []
    entries = 0
    for name in _FILES:
        path = root / name
        if path.is_symlink():
            raise ValueError("A runtime file is a symbolic link.")
        if path.exists():
            paths.append(path)
    for package in _PACKAGES:
        folder = root / package
        if folder.is_symlink():
            raise ValueError("A runtime package is a symbolic link.")
        if not folder.exists():
            continue
        for directory, dirs, files in os.walk(folder, followlinks=False, onerror=inventory_error):
            entries += len(dirs) + len(files)
            if entries > _MAX_ENTRIES:
                raise ValueError("The runtime file inventory exceeds its limit.")
            dirs[:] = sorted(name for name in dirs if not name.startswith(".")
                             and name not in {"__pycache__", "node_modules", "tests"})
            if any((Path(directory) / name).is_symlink() for name in dirs):
                raise ValueError("A runtime directory is a symbolic link.")
            for name in files:
                if name.endswith(".py"):
                    path = Path(directory) / name
                    if path.is_symlink():
                        raise ValueError("A runtime file is a symbolic link.")
                    paths.append(path)
                    if len(paths) > _MAX_FILES:
                        raise ValueError("The runtime file count exceeds its limit.")
    return sorted(paths, key=lambda path: path.relative_to(root).as_posix())


def _code_digest(root):
    """Hash bounded file contents, so same-size edits and removals are detected."""
    try:
        paths = _runtime_files(root)
        if not paths or not any(path.suffix == ".py" for path in paths):
            return None
        digest = hashlib.sha256()
        total = 0
        for path in paths:
            if not path.is_file() or path.stat().st_size > _MAX_FILE_BYTES:
                return None
            with path.open("rb") as stream:
                data = stream.read(_MAX_FILE_BYTES + 1)
            total += len(data)
            if len(data) > _MAX_FILE_BYTES or total > _MAX_TOTAL_BYTES:
                return None
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
        return digest.hexdigest()
    except (OSError, ValueError):
        return None


def _git_identity(root):
    """Read local refs only. ZIP installs and unavailable Git remain supported."""
    def read(*arguments):
        result = subprocess.run(["git", "--no-optional-locks", "-C", str(root), *arguments],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=1, check=False)
        if result.returncode or len(result.stdout) > 8192:
            return None
        return result.stdout.decode("utf-8", errors="replace").strip()

    unknown = {"commit": None, "short_commit": None, "branch": None, "runtime_modified": None}
    try:
        lines = (read("rev-parse", "--show-toplevel", "HEAD") or "").splitlines()
        if len(lines) != 2 or Path(lines[0]).resolve() != root.resolve():
            return unknown
        commit = lines[1] if re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", lines[1]) else None
        branch = read("symbolic-ref", "--short", "-q", "HEAD")
        branch = branch if branch and len(branch) <= 200 and not any(ord(c) < 32 for c in branch) else None
        paths = [*_FILES, *(f":(glob){name}/**/*.py" for name in _PACKAGES),
                 ":(exclude,glob)**/tests/**", ":(exclude,glob)**/__pycache__/**",
                 ":(exclude,glob)**/node_modules/**", ":(exclude,glob)**/.*/**"]
        changes = read("status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *paths)
        # `status --ignored` reports whole ignored directories even when their
        # contents do not match the pathspec. Enumerate ignored files separately
        # so caches do not masquerade as local code, but ignored source still does.
        ignored = read("ls-files", "--others", "--ignored", "--exclude-standard", "-z", "--", *paths)
        modified = True if changes or ignored else None if changes is None or ignored is None else False
        return {"commit": commit, "short_commit": commit[:8] if commit else None, "branch": branch,
                "runtime_modified": modified}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return unknown


class BuildIdentity:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.running = self._disk()
        self.running["started_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.diagnostics = {"project_dir": str(self.root), "python_executable": sys.executable,
                            "python_version": platform.python_version(), "pid": os.getpid()}

    def _disk(self):
        return {**_git_identity(self.root), "code_digest": _code_digest(self.root)}

    def restart_required(self):
        current = _code_digest(self.root)
        return bool(self.running["code_digest"] and current
                    and self.running["code_digest"] != current)

    def status(self, diagnostics=False):
        disk = self._disk()
        verified = bool(self.running["code_digest"] and disk["code_digest"])
        restart = verified and self.running["code_digest"] != disk["code_digest"]
        status = "restart_required" if restart else "current" if verified else "unavailable"
        message = (_RESTART_MESSAGE if restart else "Running code matches this checkout." if verified
                   else "The runtime files could not be checked. Confirm the checkout is readable and restart gui.py.")
        result = {"ok": True, "running": dict(self.running), "disk": disk,
                  "restart_required": bool(restart), "checkout_changed": bool(
                      self.running["commit"] and disk["commit"] and self.running["commit"] != disk["commit"]),
                  "status": status, "message": message}
        if diagnostics:
            result["diagnostics"] = dict(self.diagnostics)
        return result


def init_app(app, root=None):
    identity = BuildIdentity(root if root is not None else _ROOT)
    app.extensions["yaas_build"] = identity

    def build_route():
        response = jsonify(identity.status(diagnostics=request.args.get("diagnostics") == "1"))
        response.headers["Cache-Control"] = "no-store"
        return response

    app.add_url_rule("/api/build", "build_info", build_route)

    @app.before_request
    def guard_runtime_mutation():
        if (request.method in _WRITE_METHODS and request.path.startswith("/api/")
                and request.endpoint != "pipeline.stop_pipeline" and request.path != "/api/stop"
                and identity.restart_required()):
            response = jsonify({"ok": False, "code": "restart_required", "error": _RESTART_MESSAGE})
            response.headers["Cache-Control"] = "no-store"
            return response, 409
        return None


def startup_lines(app):
    """Explicit terminal diagnostics; paths/PID are not in the normal status UI."""
    identity = app.extensions["yaas_build"]
    running, details = identity.running, identity.diagnostics
    version = running["short_commit"] or f"unversioned {(running['code_digest'] or 'unknown')[:12]}"
    if running.get("runtime_modified"):
        version += " + local code changes"
    branch = f" ({running['branch']})" if running["branch"] else ""
    return [f"   Build: {version}{branch}", f"   Project: {details['project_dir']}",
            f"   Python: {details['python_executable']} ({details['python_version']})",
            f"   Process: {details['pid']}"]
