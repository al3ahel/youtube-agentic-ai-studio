"""
YAAS — youtube-agentic-ai-studio · Desktop GUI
Run:  python gui.py
Open: http://localhost:7070

One page controls everything: dashboard, generate, review, library, upload.
The app itself lives in the studio/ package; this file only starts it.

  python gui.py --import-settings old_config.py
      carry over settings an older version saved into config.py
"""
import os
import sys
import threading

# The studio prints "⚠", "→" and titles in any language. A console or log file
# using a legacy Windows code page cannot encode all of them, and an encoding
# error there would abort startup or a running job. Substitute instead.
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError, OSError):
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)   # relative paths (output/, token file) resolve the same from anywhere

from studio import create_app  # noqa: E402  (needs sys.path above)

app = create_app()


def _bind_server(port):
    """Bind before scheduling a browser or starting background jobs."""
    import errno
    from werkzeug.serving import make_server
    try:
        return make_server("127.0.0.1", port, app, threaded=True)
    except (OSError, SystemExit) as exc:
        cause = exc if isinstance(exc, OSError) else exc.__context__
        # Windows reports a port held by another process as WSAEACCES (10013,
        # surfaced as PermissionError/EACCES) as often as WSAEADDRINUSE (10048).
        occupied = (getattr(cause, "errno", None) in {errno.EADDRINUSE, 10048}
                    or getattr(cause, "winerror", None) in {10013, 10048})
        reason = f"Port {port} is already in use." if occupied else f"Could not listen on port {port}."
        raise SystemExit(
            f"{reason} YAAS was not started and no browser was opened. "
            "If another YAAS terminal is running, stop it with Ctrl+C and try again, "
            "or choose another STUDIO_PORT."
        ) from None


def main(argv=None):
    import atexit
    import webbrowser
    from studio.build_info import startup_lines

    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 2 and argv[0] == "--import-settings":
        from studio.settings import import_settings
        import_settings(argv[1])
        return

    scheduler_only = "--scheduler-only" in argv
    if scheduler_only:
        if os.getenv("STUDIO_SCHEDULER", "1") == "0":
            raise SystemExit("Scheduler-only mode requires STUDIO_SCHEDULER=1.")
        from studio.schedules import start_scheduler
        scheduler = start_scheduler()
        atexit.register(scheduler.close)
        try:
            if not scheduler.running:
                raise SystemExit(scheduler.error or "Could not start the scheduler.")
            print("YAAS channel scheduler is running. Keep this process open; Ctrl+C stops it.")
            print("\n".join(startup_lines(app)))
            while scheduler.running:
                scheduler.stop.wait(30)
        except KeyboardInterrupt:
            pass
        finally:
            scheduler.close()
        return

    try:
        port = int(os.getenv("STUDIO_PORT", "7070"))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise SystemExit("STUDIO_PORT must be a port number between 1 and 65535.") from None

    server = _bind_server(port)
    scheduler = None
    browser_timer = None
    try:
        if os.getenv("STUDIO_SCHEDULER", "1") != "0":
            from studio.schedules import start_scheduler
            scheduler = start_scheduler()
            atexit.register(scheduler.close)
        print("\n🎬  YAAS — youtube-agentic-ai-studio")
        print("\n".join(startup_lines(app)))
        print(f"   Listening at  http://localhost:{port}\n")
        if os.getenv("STUDIO_NO_BROWSER") != "1":
            browser_timer = threading.Timer(1.4, lambda: webbrowser.open(f"http://localhost:{port}"))
            browser_timer.daemon = True
            browser_timer.start()
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if browser_timer is not None:
            browser_timer.cancel()
        server.server_close()
        if scheduler is not None:
            scheduler.close()


if __name__ == "__main__":
    main()
