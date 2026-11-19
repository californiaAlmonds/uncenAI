import os
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
if not hasattr(os, "register_at_fork"):
    os.register_at_fork = lambda *a, **kw: None

import sys
import json
import argparse

original_stdout = sys.stdout
sys.stdout = sys.stderr

try:
    from lightning_sdk.cli.utils.auth import require_auth_header
    from lightning_sdk import Studio, Machine, Status
    from lightning_sdk.cli.utils.ssh_connection import configure_ssh_internal
except ImportError:
    sys.stdout = original_stdout
    print(json.dumps({"ok": False, "error": "Lightning SDK not installed properly."}))
    sys.exit(2)

def check_auth():
    try:
        require_auth_header()
    except Exception as e:
        sys.stdout = original_stdout
        print(json.dumps({"ok": False, "error": "Not logged into Lightning.ai. Run: py src/local_controller.py auth --backend lightning"}))
        sys.exit(2)
    except SystemExit:
        sys.stdout = original_stdout
        print(json.dumps({"ok": False, "error": "Not logged into Lightning.ai. Run: py src/local_controller.py auth --backend lightning"}))
        sys.exit(2)

def start(studio_name, machine):
    check_auth()
    Studio.show_progress = False
    s = Studio(name=studio_name, create_ok=True)
    target_m = Machine.from_str(machine)
    if s.status == Status.Running:
        if s.machine != target_m:
            s.switch_machine(target_m)
    else:
        s.start(target_m)
    
    ssh_key = configure_ssh_internal()
    sys.stdout = original_stdout
    print(json.dumps({
        "ok": True,
        "studioId": s._studio.id,
        "sshUserHost": f"s_{s._studio.id}@ssh.lightning.ai",
        "sshKeyPath": str(ssh_key),
        "machine": str(s.machine)
    }))

def stop(studio_name):
    check_auth()
    Studio.show_progress = False
    try:
        s = Studio(name=studio_name, create_ok=False)
    except Exception:
        sys.stdout = original_stdout
        print(json.dumps({"ok": True, "alreadyStopped": True}))
        return
    
    if s.status not in (Status.Stopped, Status.Stopping, Status.NotCreated):
        s.stop()
    
    sys.stdout = original_stdout
    print(json.dumps({"ok": True}))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("cmd", choices=["start", "stop"])
    parser.add_argument("--studio", required=True)
    parser.add_argument("--machine", default="T4")
    args = parser.parse_args()
    
    if args.cmd == "start":
        start(args.studio, args.machine)
    elif args.cmd == "stop":
        stop(args.studio)
