# run_all.py
"""
run_all.py

Orchestrator: starts both Streamlit apps simultaneously.

    python run_all.py

  → Main pipeline   : http://localhost:8501  (app.py)
  → Full-Stack Studio: http://localhost:8502  (build_frontend.py)

Press Ctrl-C to stop both servers.
"""

import subprocess
import sys
import signal
import time
from pathlib import Path

# ── Colour helpers ───────────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def bold(s):   return f"{BOLD}{s}{RESET}"
def green(s):  return f"{GREEN}{s}{RESET}"
def yellow(s): return f"{YELLOW}{s}{RESET}"
def red(s):    return f"{RED}{s}{RESET}"


# ── Required files ───────────────────────────────────────────────────
APPS = [
    {
        "label":  "Main Pipeline",
        "script": "app.py",
        "port":   8501,
        "url":    "http://localhost:8501",
    },
    {
        "label":  "Full-Stack Studio",
        "script": "build_frontend.py",
        "port":   8502,
        "url":    "http://localhost:8502",
    },
]


def _check_scripts():
    """Abort early if either script is missing."""
    missing = [a["script"] for a in APPS if not Path(a["script"]).exists()]
    if missing:
        print(red(f"Missing script(s): {', '.join(missing)}"))
        print("Run from the project root that contains both app.py "
              "and build_frontend.py.")
        sys.exit(1)


def _launch(app: dict) -> subprocess.Popen:
    """Start a single Streamlit process and return the Popen handle."""
    cmd = [
        sys.executable, "-m", "streamlit", "run",
        app["script"],
        "--server.port", str(app["port"]),
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    return proc


def _shutdown(procs: list[subprocess.Popen]):
    """Terminate all child processes gracefully."""
    print(f"\n{yellow('Shutting down…')}")
    for proc in procs:
        if proc.poll() is None:
            proc.terminate()
    # Give them 3 s to exit, then force-kill
    deadline = time.time() + 3
    for proc in procs:
        remaining = max(0, deadline - time.time())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            proc.kill()
    print(green("All servers stopped."))


def main():
    _check_scripts()

    print(bold("\n⚡ ZEMO.ai — launching all services\n"))

    procs: list[subprocess.Popen] = []
    for app in APPS:
        proc = _launch(app)
        procs.append(proc)
        print(f"  {green('▶')}  {app['label']:22s}  {app['url']}  "
              f"(pid {proc.pid})")

    print(f"\n  {yellow('Ctrl-C to stop all servers')}\n")

    # ── Graceful Ctrl-C handling ─────────────────────────────────────
    def _sigint_handler(sig, frame):
        _shutdown(procs)
        sys.exit(0)

    signal.signal(signal.SIGINT, _sigint_handler)
    signal.signal(signal.SIGTERM, _sigint_handler)

    # ── Stream stdout from both processes ────────────────────────────
    # Simple round-robin poll — good enough for a dev orchestrator.
    try:
        while True:
            for i, (app, proc) in enumerate(zip(APPS, procs)):
                if proc.poll() is not None:
                    print(red(
                        f"\n  ✗  {app['label']} exited unexpectedly "
                        f"(code {proc.returncode}). "
                        "Check the script for errors."
                    ))
                    _shutdown([p for j, p in enumerate(procs) if j != i])
                    sys.exit(1)
                # Non-blocking line read
                if proc.stdout:
                    line = proc.stdout.readline()
                    if line.strip():
                        tag = app["label"][:12].ljust(12)
                        print(f"  [{tag}] {line.rstrip()}")
            time.sleep(0.05)
    except KeyboardInterrupt:
        _shutdown(procs)
        sys.exit(0)


if __name__ == "__main__":
    main()
