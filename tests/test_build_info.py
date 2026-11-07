"""Running build identity, stale-process safety and Windows-friendly startup."""
import json
import os
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace

from flask import Flask, jsonify
import pytest

from studio import build_info, security


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "gui.py").write_text("print('old')\n", encoding="utf-8")
    (root / "config.py").write_text("API_KEY = 'secret-test-value'\n", encoding="utf-8")
    (root / "studio").mkdir()
    (root / "studio" / "runner.py").write_text("VERSION = 1\n", encoding="utf-8")
    monkeypatch.setattr(build_info, "_git_identity", lambda root: {
        "commit": "a" * 40, "short_commit": "a" * 8, "branch": "main"})
    return root


def app_for(root):
    app = Flask(__name__)
    app.config["TESTING"] = True
    security.init_app(app)
    build_info.init_app(app, root)
    mutations = []

    @app.get("/api/read")
    def read():
        return jsonify({"ok": True})

    @app.route("/api/change", methods=["POST", "PUT", "PATCH", "DELETE"])
    def change():
        mutations.append("changed")
        return jsonify({"ok": True})

    @app.post("/api/stop")
    def stop():
        mutations.append("stopped")
        return jsonify({"ok": True})

    return app, mutations


def test_build_identity_exposes_current_build_without_private_paths(checkout, monkeypatch):
    # A build poll must never call an inference service or even generic HTTP.
    import requests
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *a, **k: pytest.fail("Network request"))
    app, _ = app_for(checkout)
    response = app.test_client().get("/api/build")
    data = response.get_json()
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    assert data["status"] == "current" and not data["restart_required"]
    assert data["running"]["commit"] == "a" * 40
    assert data["running"]["code_digest"] == data["disk"]["code_digest"]
    assert data["running"]["started_at"]
    assert "diagnostics" not in data
    serialized = json.dumps(data)
    assert str(checkout) not in serialized and "secret-test-value" not in serialized
    assert "python_executable" not in serialized and '"pid"' not in serialized


def test_explicit_diagnostics_identify_checkout_interpreter_and_process(checkout):
    import sys
    app, _ = app_for(checkout)
    data = app.test_client().get("/api/build?diagnostics=1").get_json()
    assert data["diagnostics"]["project_dir"] == str(checkout)
    assert data["diagnostics"]["python_executable"] == sys.executable
    assert data["diagnostics"]["pid"] == os.getpid()
    assert "secret-test-value" not in json.dumps(data)
    lines = "\n".join(build_info.startup_lines(app))
    assert str(checkout) in lines and sys.executable in lines


def test_same_size_runtime_edit_with_preserved_mtime_requires_restart(checkout):
    app, _ = app_for(checkout)
    client = app.test_client()
    before = client.get("/api/build").get_json()["running"]
    runtime = checkout / "studio" / "runner.py"
    stamp = runtime.stat()
    runtime.write_text("VERSION = 2\n", encoding="utf-8")
    os.utime(runtime, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    data = client.get("/api/build").get_json()
    assert data["running"] == before
    assert data["restart_required"] and data["status"] == "restart_required"
    assert not data["checkout_changed"]  # uncommitted edit at the same HEAD
    assert data["disk"]["code_digest"] != before["code_digest"]


@pytest.mark.parametrize("change", ["add", "remove", "dependency"])
def test_runtime_inventory_and_dependency_changes_require_restart(checkout, change):
    app, _ = app_for(checkout)
    if change == "add":
        (checkout / "studio" / "new_agent.py").write_text("VERSION = 1\n")
    elif change == "remove":
        (checkout / "studio" / "runner.py").unlink()
    else:
        (checkout / "requirements.txt").write_text("Flask==3.1.0\n")
    assert app.test_client().get("/api/build").get_json()["restart_required"]


def test_docs_assets_profiles_and_output_do_not_require_restart(checkout, monkeypatch):
    app, _ = app_for(checkout)
    for relative in ("README.md", "docs/help.md", "tests/test_extra.py", "settings.json",
                     ".env", ".yaas/channels.json", "output/narration.mp3",
                     "studio/static/js/settings.js", "studio/static/app.css",
                     "studio/templates/index.html", "studio/__pycache__/runner.pyc"):
        path = checkout / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("changed")
    monkeypatch.setattr(build_info, "_git_identity", lambda root: {
        "commit": "b" * 40, "short_commit": "b" * 8, "branch": "main"})
    data = app.test_client().get("/api/build").get_json()
    assert not data["restart_required"] and data["checkout_changed"]
    assert data["status"] == "current"


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_stale_runtime_blocks_api_mutation_but_allows_reads_and_stop(checkout, method):
    app, mutations = app_for(checkout)
    (checkout / "gui.py").write_text("print('new')\n")
    client = app.test_client()
    response = getattr(client, method)("/api/change", json={})
    assert response.status_code == 409
    assert response.get_json()["code"] == "restart_required"
    assert "Ctrl+C" in response.get_json()["error"] and "gui.py" in response.get_json()["error"]
    assert mutations == []
    assert client.get("/api/read").status_code == 200
    assert client.get("/api/build").status_code == 200
    assert client.post("/api/stop", json={}).status_code == 200
    assert mutations == ["stopped"]
    # Existing CSRF/Host protections still apply before the build guard.
    assert client.post("/api/change", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/build", headers={"Host": "evil.example"}).status_code == 403


def test_restarted_app_accepts_new_runtime_without_manual_reset(checkout):
    first, _ = app_for(checkout)
    (checkout / "gui.py").write_text("print('new')\n")
    assert first.test_client().post("/api/change", json={}).status_code == 409
    restarted, mutations = app_for(checkout)
    assert not restarted.test_client().get("/api/build").get_json()["restart_required"]
    assert restarted.test_client().post("/api/change", json={}).status_code == 200
    assert mutations == ["changed"]


def test_zip_or_git_unavailable_keeps_content_identity(checkout, monkeypatch):
    monkeypatch.setattr(build_info, "_git_identity", lambda root: {
        "commit": None, "short_commit": None, "branch": None})
    app, _ = app_for(checkout)
    data = app.test_client().get("/api/build").get_json()
    assert data["status"] == "current"
    assert data["running"]["commit"] is None
    assert len(data["running"]["code_digest"]) == 64
    assert "Build:" in "\n".join(build_info.startup_lines(app))


def test_code_fingerprint_is_bounded_and_does_not_follow_symlinks(checkout, monkeypatch, tmp_path):
    monkeypatch.setattr(build_info, "_MAX_FILE_BYTES", 4)
    assert build_info._code_digest(checkout) is None
    monkeypatch.setattr(build_info, "_MAX_FILE_BYTES", 2 * 1024 * 1024)
    external = tmp_path / "private.py"
    external.write_text("SECRET = 'outside-checkout'\n")
    try:
        (checkout / "studio" / "outside.py").symlink_to(external)
    except (OSError, NotImplementedError):
        pytest.skip("Symbolic links are unavailable")
    assert build_info._code_digest(checkout) is None


def test_local_git_identity_and_docs_only_commit(tmp_path):
    root = tmp_path / "real-git"
    root.mkdir()
    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=5).stdout.decode().strip()
    try:
        git("init", "--initial-branch=main")
    except FileNotFoundError:
        pytest.skip("Git is unavailable")
    git("config", "user.name", "Build Test")
    git("config", "user.email", "build-test@example.invalid")
    (root / "gui.py").write_text("print('app')\n")
    git("add", "gui.py")
    git("-c", "commit.gpgsign=false", "commit", "-m", "runtime")
    identity = build_info.BuildIdentity(root)
    assert identity.running["commit"] == git("rev-parse", "HEAD")
    assert identity.running["branch"] == "main"
    assert identity.running["runtime_modified"] is False
    (root / "README.md").write_text("Docs only\n")
    git("add", "README.md")
    git("-c", "commit.gpgsign=false", "commit", "-m", "docs")
    status = identity.status()
    assert status["checkout_changed"] and not status["restart_required"]
    assert status["disk"]["runtime_modified"] is False
    (root / "studio" / "static").mkdir(parents=True)
    (root / "studio" / "static" / "app.css").write_text("body {color:blue}")
    (root / "settings.json").write_text('{"secret":"private"}')
    assert build_info._git_identity(root)["runtime_modified"] is False
    (root / "studio" / "worker.py").write_text("VERSION = 1\n")
    assert build_info._git_identity(root)["runtime_modified"] is True
    (root / "studio" / "worker.py").unlink()
    (root / "gui.py").write_text("print('new')\n")
    assert build_info._git_identity(root)["runtime_modified"] is True
    # A ZIP nested inside another checkout must not claim its parent's commit.
    nested = root / "nested-zip"
    nested.mkdir()
    assert build_info._git_identity(nested)["commit"] is None


def test_build_endpoint_does_not_depend_on_profile_registry(checkout, monkeypatch):
    import studio
    from studio import channels, workspace
    monkeypatch.setattr(build_info, "_ROOT", checkout)
    monkeypatch.setattr(workspace, "restore_workspace", lambda: None)
    monkeypatch.setattr(channels, "get_profile", lambda *a, **k: pytest.fail("Profile lookup"))
    app = studio.create_app()
    response = app.test_client().get("/api/build?profile_id=deleted-profile&diagnostics=1")
    assert response.status_code == 200
    assert response.get_json()["diagnostics"]["project_dir"] == str(checkout)


def test_occupied_port_does_not_open_browser_or_start_scheduler(monkeypatch):
    import gui
    from studio import schedules
    monkeypatch.setenv("STUDIO_SCHEDULER", "1")
    monkeypatch.delenv("STUDIO_NO_BROWSER", raising=False)
    monkeypatch.setattr(gui.threading, "Timer", lambda *a, **k: pytest.fail("Browser timer started"))
    monkeypatch.setattr(schedules, "start_scheduler", lambda: pytest.fail("Scheduler started before bind"))
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        port = occupied.getsockname()[1]
        monkeypatch.setenv("STUDIO_PORT", str(port))
        with pytest.raises(SystemExit) as error:
            gui.main([])
    assert str(port) in str(error.value)
    assert "already in use" in str(error.value)
    assert "no browser was opened" in str(error.value)


def test_gui_shutdown_closes_bound_server_timer_and_scheduler(monkeypatch):
    import gui
    from studio import schedules
    events = []
    server = SimpleNamespace(serve_forever=lambda: events.append("serve"),
                             server_close=lambda: events.append("close_server"))
    scheduler = SimpleNamespace(close=lambda: events.append("close_scheduler"))
    class Timer:
        def __init__(self, delay, callback):
            events.append("timer")
        def start(self):
            events.append("start_timer")
        def cancel(self):
            events.append("cancel_timer")
    monkeypatch.setattr(gui, "_bind_server", lambda port: events.append("bind") or server)
    monkeypatch.setattr(gui.threading, "Timer", Timer)
    monkeypatch.setattr(schedules, "start_scheduler", lambda: events.append("scheduler") or scheduler)
    monkeypatch.setenv("STUDIO_SCHEDULER", "1")
    monkeypatch.delenv("STUDIO_NO_BROWSER", raising=False)
    monkeypatch.setenv("STUDIO_PORT", "8765")
    gui.main([])
    assert events == ["bind", "scheduler", "timer", "start_timer", "serve",
                      "cancel_timer", "close_server", "close_scheduler"]


def test_scheduler_only_does_not_bind_web_port(monkeypatch):
    import gui
    from studio import schedules
    events = []
    scheduler = SimpleNamespace(running=True, close=lambda: events.append("closed"))
    def wait(seconds):
        events.append("wait")
        scheduler.running = False
    scheduler.stop = SimpleNamespace(wait=wait)
    monkeypatch.setattr(gui, "_bind_server", lambda port: pytest.fail("Web port bound"))
    monkeypatch.setattr(schedules, "start_scheduler", lambda: scheduler)
    monkeypatch.setenv("STUDIO_SCHEDULER", "1")
    gui.main(["--scheduler-only"])
    assert events == ["wait", "closed"]
