import os
import sys
import time
import json
import threading
import subprocess
import urllib.request
import urllib.error
import http.client
import argparse
import re
import secrets
import shutil
from datetime import datetime, date
from urllib.parse import urlparse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STATE_DIR = os.path.abspath(os.environ.get("UNCENAI_STATE_DIR") or os.path.join(BASE_DIR, ".state"))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
MODELS_JSON = os.path.join(CONFIG_DIR, "models.json")
QUOTA_JSON = os.path.join(STATE_DIR, "quota.json")
BACKEND_JSON = os.path.join(STATE_DIR, "backend.json")
SSH_KEY_PATH = os.path.join(STATE_DIR, "id_ed25519")
DAEMON_LOG = os.path.join(STATE_DIR, "daemon.log")

if os.environ.get("UNCENAI_TMP_DIR"):
    t = os.environ["UNCENAI_TMP_DIR"]
    os.environ["TEMP"] = t
    os.environ["TMP"] = t
    os.makedirs(t, exist_ok=True)
elif os.name == "nt" and os.path.isdir("E:\\uv"):
    t = "E:\\tmp"
    os.environ["TEMP"] = t
    os.environ["TMP"] = t
    os.makedirs(t, exist_ok=True)

os.makedirs(STATE_DIR, exist_ok=True)

state_lock = threading.RLock()
lifecycle_lock = threading.Lock()

class State:
    run_id = 0
    active_backend = "colab"
    gpu_state = "off"
    stage_detail = "Offline"
    active_model = None
    target_model = None
    download_percent = None
    gpu_info = None
    last_error = None
    last_activity_ts = time.time()
    last_modal_inference_ts = 0
    ssh_proc = None
    ssh_backend = None
    start_thread = None
    cached_lightning_user = None
    cached_lightning_key = None

def get_config():
    with open(MODELS_JSON, "r", encoding="utf-8") as f:
        return json.load(f)

def load_active_backend():
    try:
        with open(BACKEND_JSON, "r") as f:
            b = json.load(f).get("activeBackend")
            if b in ("colab", "lightning", "modal"): return b
    except: pass
    return get_config().get("proxy", {}).get("defaultBackend", "colab")

def get_backend_config(backend=None):
    if not backend: backend = State.active_backend
    cfg = get_config()
    return cfg.get("backends", {}).get(backend, {})

def get_quota():
    if not os.path.exists(QUOTA_JSON): return {"colab": {"days": {}}, "lightning": {"months": {}}, "modal": {"months": {}}}
    try:
        with open(QUOTA_JSON, "r", encoding="utf-8") as f:
            q = json.load(f)
            if "days" in q:
                old_days = q.pop("days")
                q["colab"] = {"days": old_days}
            for b in ["colab", "lightning", "modal"]:
                if b not in q: q[b] = {"days": {}, "months": {}}
            return q
    except:
        return {"colab": {"days": {}}, "lightning": {"months": {}}, "modal": {"months": {}}}

def save_quota(q):
    with open(QUOTA_JSON, "w", encoding="utf-8") as f:
        json.dump(q, f)

def get_today_used_seconds(backend=None):
    if not backend: backend = State.active_backend
    q = get_quota()
    cfg = get_backend_config(backend)
    period = cfg.get("quotaPeriod", "daily")
    if period == "daily":
        key = date.today().isoformat()
        return q.get(backend, {}).get("days", {}).get(key, 0)
    else:
        key = date.today().strftime("%Y-%m")
        return q.get(backend, {}).get("months", {}).get(key, 0)

def add_quota_seconds(secs, backend):
    q = get_quota()
    cfg = get_backend_config(backend)
    period = cfg.get("quotaPeriod", "daily")
    if period == "daily":
        key = date.today().isoformat()
        q[backend]["days"][key] = q[backend].get("days", {}).get(key, 0) + secs
    else:
        key = date.today().strftime("%Y-%m")
        q[backend]["months"][key] = q[backend].get("months", {}).get(key, 0) + secs
    save_quota(q)

def resolve_cli_exe(exe_name, cfg_override=None):
    if cfg_override and os.path.exists(cfg_override):
        return cfg_override
    w = shutil.which(exe_name)
    if w: return w
    
    home = os.path.expanduser("~")
    local_bin = os.path.join(home, ".local", "bin", exe_name)
    if os.name == "nt": local_bin += ".exe"
    if os.path.exists(local_bin): return local_bin
    
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            appdata_bin = os.path.join(appdata, "Python", "Scripts", f"{exe_name}.exe")
            if os.path.exists(appdata_bin): return appdata_bin
        e_uv = f"E:\\uv\\bin\\{exe_name}.exe"
        if os.path.exists(e_uv): return e_uv

    return exe_name

def resolve_tool_python(tool_pkg_name, cfg_override=None):
    if cfg_override and os.path.exists(cfg_override):
        return cfg_override
    home = os.path.expanduser("~")
    
    if os.name == "nt":
        paths = [
            os.path.join(home, ".local", "share", "uv", "tools", tool_pkg_name, "Scripts", "python.exe"),
            os.path.join(os.environ.get("APPDATA", ""), "uv", "tools", tool_pkg_name, "Scripts", "python.exe"),
            f"E:\\uv\\tools\\{tool_pkg_name}\\Scripts\\python.exe"
        ]
    else:
        paths = [
            os.path.join(home, ".local", "share", "uv", "tools", tool_pkg_name, "bin", "python")
        ]
        
    for p in paths:
        if os.path.exists(p): return p
        
    return sys.executable

def resolve_openssh_bin(name):
    w = shutil.which(name)
    if w: return w
    if os.name == "nt":
        sys32 = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "System32", "OpenSSH", f"{name}.exe")
        if os.path.exists(sys32): return sys32
    return name

def reap_ssh_tunnel(only_backend=None):
    proc = None
    with state_lock:
        if State.ssh_proc and (only_backend is None or State.ssh_backend == only_backend):
            proc = State.ssh_proc
            State.ssh_proc = None
            State.ssh_backend = None
    if proc:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        except Exception:
            pass

def is_cancelled(my_run_id):
    with state_lock:
        return State.run_id != my_run_id

def _cleanup_backend(backend):
    with lifecycle_lock:
        reap_ssh_tunnel(only_backend=backend)
        b_cfg = get_backend_config(backend)
        if backend == "colab":
            session_name = b_cfg.get("sessionName", "uncenai")
            colab_bin = resolve_cli_exe(b_cfg.get("exeName", "colab"))
            subprocess.run([colab_bin, "stop", "-s", session_name], check=False)
        elif backend == "lightning":
            studio_name = b_cfg.get("studioName", "uncenai")
            py_exe = resolve_tool_python("lightning-sdk")
            bridge = os.path.join(BASE_DIR, "src", "lightning_bridge.py")
            subprocess.run([py_exe, bridge, "stop", "--studio", studio_name], check=False)
        elif backend == "modal":
            app_name = b_cfg.get("appName", "uncenai-gpu")
            modal_exe = resolve_cli_exe(b_cfg.get("exeName", "modal"))
            subprocess.run([modal_exe, "app", "stop", app_name], cwd=BASE_DIR, check=False)

def stop_active_backend():
    with state_lock:
        State.run_id += 1
        backend = State.active_backend
        State.gpu_state = "stopping"
        State.stage_detail = "Stopping..."
    
    _cleanup_backend(backend)
    
    with state_lock:
        if State.gpu_state == "stopping":
            State.gpu_state = "off"
            State.stage_detail = "Offline"
            State.active_model = None
            State.download_percent = None
            State.gpu_info = None

def select_backend(backend):
    need_stop = False
    with state_lock:
        if State.active_backend == backend:
            return
        need_stop = State.gpu_state in ("starting", "ready")

    if need_stop:
        stop_active_backend()

    with state_lock:
        State.active_backend = backend
        with open(BACKEND_JSON, "w", encoding="utf-8") as f:
            json.dump({"activeBackend": backend}, f)

class GatewayHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/uncenai/status":
            with state_lock:
                backend = State.active_backend
                b_cfg = get_backend_config(backend)
                used_mins = get_today_used_seconds(backend) / 60.0
                idle_mins = (time.time() - State.last_activity_ts) / 60.0
                
                soft_limit = b_cfg.get("softLimitMinutes", 180)
                hard_limit = b_cfg.get("hardLimitMinutes", 240)
                auto_stop = b_cfg.get("idleAutoStopMinutes", 20)
                period = b_cfg.get("quotaPeriod", "daily")
                
                cfg = get_config()
                backends_map = {}
                for k, v in cfg.get("backends", {}).items():
                    backends_map[k] = {
                        "id": k,
                        "label": v.get("label"),
                        "shortLabel": v.get("shortLabel"),
                        "quotaPeriod": v.get("quotaPeriod"),
                        "usedMinutes": round(get_today_used_seconds(k) / 60.0, 1),
                        "softLimitMinutes": v.get("softLimitMinutes"),
                        "hardLimitMinutes": v.get("hardLimitMinutes")
                    }
                
                resp = {
                    "daemonRunning": True,
                    "activeBackend": backend,
                    "activeBackendLabel": b_cfg.get("label"),
                    "activeBackendShort": b_cfg.get("shortLabel"),
                    "gpuState": State.gpu_state,
                    "stageDetail": State.stage_detail,
                    "activeModel": State.active_model,
                    "targetModel": State.target_model,
                    "downloadPercent": State.download_percent,
                    "gpuInfo": State.gpu_info,
                    "quota": {
                        "quotaPeriod": period,
                        "usedMinutes": round(used_mins, 1),
                        "usedMinutesToday": round(used_mins, 1),
                        "softLimitMinutes": soft_limit,
                        "hardLimitMinutes": hard_limit,
                        "remainingMinutes": max(0, hard_limit - used_mins),
                        "idleMinutes": round(idle_mins, 1),
                        "idleAutoStopMinutes": auto_stop,
                        "softLimitReached": used_mins >= soft_limit
                    },
                    "backends": backends_map,
                    "lastError": State.last_error
                }
            self._send_json(200, resp)
        elif self.path == "/v1/models":
            try:
                cfg = get_config()
                models = [{"id": m["id"], "object": "model", "created": 1730000000, "owned_by": "uncenai"} for m in cfg.get("colabModels", [])]
                self._send_json(200, {"object": "list", "data": models})
            except Exception as e:
                self._send_json(500, {"error": str(e)})
        else:
            self._send_json(404, {"error": "Not found"})

    def do_POST(self):
        if self.headers.get("Origin"):
            self._send_json(403, {"error": "Cross-origin requests forbidden"})
            return
            
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""
        req_data = json.loads(body) if body else {}

        if self.path == "/uncenai/select-backend":
            req_backend = req_data.get("backend")
            if req_backend in ("colab", "lightning", "modal"):
                select_backend(req_backend)
                self._send_json(200, {"status": "ok"})
            else:
                self._send_json(400, {"error": "Invalid backend"})
            return

        if self.path == "/uncenai/start":
            req_backend = req_data.get("backend")
            if req_backend and req_backend in ("colab", "lightning", "modal"):
                select_backend(req_backend)
                
            with state_lock:
                backend = State.active_backend
                State.last_activity_ts = time.time()
                b_cfg = get_backend_config(backend)
                hard_limit = b_cfg.get("hardLimitMinutes", 240)
                ignore_quota = req_data.get("ignoreQuota", False)
                if not ignore_quota and get_today_used_seconds(backend) / 60.0 >= hard_limit:
                    self._send_json(403, {"error": "Hard limit reached."})
                    return
                
                req_model = req_data.get("model")
                
                if State.gpu_state in ("off", "error"):
                    State.run_id += 1
                    my_run_id = State.run_id
                    State.target_model = req_model
                    State.gpu_state = "starting"
                    State.stage_detail = f"Booting {backend} VM..."
                    if backend == "colab":
                        State.start_thread = threading.Thread(target=colab_start_sequence, args=(my_run_id,))
                    elif backend == "lightning":
                        State.start_thread = threading.Thread(target=lightning_start_sequence, args=(my_run_id,))
                    elif backend == "modal":
                        State.start_thread = threading.Thread(target=modal_start_sequence, args=(my_run_id,))
                    State.start_thread.start()
                elif State.gpu_state == "ready" and req_model and req_model != State.active_model:
                    State.run_id += 1
                    my_run_id = State.run_id
                    State.gpu_state = "starting"
                    State.target_model = req_model
                    State.stage_detail = f"Switching to {req_model}..."
                    if backend == "modal":
                        State.start_thread = threading.Thread(target=modal_hotswap, args=(req_model, my_run_id))
                    else:
                        State.start_thread = threading.Thread(target=load_remote_model, args=(req_model, my_run_id))
                    State.start_thread.start()
                
            self._send_json(202, {"status": "starting"})
        elif self.path == "/uncenai/stop":
            stop_active_backend()
            self._send_json(200, {"status": "stopped"})
        elif self.path == "/uncenai/shutdown-daemon":
            reap_ssh_tunnel()
            self._send_json(200, {"status": "shutting down"})
            threading.Thread(target=lambda: os._exit(0)).start()
        elif self.path == "/v1/chat/completions":
            with state_lock:
                State.last_activity_ts = time.time()
                current_state = State.gpu_state
                backend = State.active_backend
            
            if current_state in ("off", "error"):
                self._send_offline_mock(req_data)
                return
            if current_state == "starting":
                start_wait = time.time()
                while time.time() - start_wait < 240:
                    with state_lock:
                        if State.gpu_state != "starting": break
                    time.sleep(1)
                with state_lock:
                    if State.gpu_state != "ready":
                        self._send_offline_mock(req_data)
                        return

            conn = None
            try:
                if backend == "modal":
                    with open(os.path.join(STATE_DIR, "modal_deployment.json"), "r") as f:
                        modal_url = json.load(f)["url"]
                    with open(os.path.join(STATE_DIR, "modal_auth_token.txt"), "r") as f:
                        token = f.read().strip()
                    parsed = urlparse(modal_url)
                    with state_lock:
                        State.last_modal_inference_ts = time.time()
                    conn = http.client.HTTPSConnection(parsed.netloc, timeout=600)
                    headers = {k: v for k, v in self.headers.items() if k.lower() not in ('host', 'connection', 'transfer-encoding')}
                    headers['X-UncenAI-Token'] = token
                    conn.request("POST", self.path, body=body, headers=headers)
                else:
                    conn = http.client.HTTPConnection("127.0.0.1", 11481, timeout=600)
                    headers = {k: v for k, v in self.headers.items() if k.lower() not in ('host', 'connection', 'transfer-encoding')}
                    conn.request("POST", self.path, body=body, headers=headers)
                
                res = conn.getresponse()
                self.send_response(res.status)
                for k, v in res.getheaders():
                    if k.lower() not in ('transfer-encoding', 'connection'):
                        self.send_header(k, v)
                self.send_header("Connection", "close")
                self.end_headers()
                
                req_model = req_data.get("model")
                if req_model:
                    with state_lock:
                        State.active_model = req_model
                
                while True:
                    chunk = res.read1(4096) if hasattr(res, "read1") else res.read(256)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except Exception as e:
                self._send_offline_mock(req_data)
            finally:
                if conn:
                    conn.close()
                with state_lock:
                    State.last_activity_ts = time.time()
                    if backend == "modal":
                        State.last_modal_inference_ts = time.time()

    def _send_json(self, status, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_offline_mock(self, req_data):
        is_stream = req_data.get("stream", False)
        with state_lock:
            b_cfg = get_backend_config(State.active_backend)
            used_mins = round(get_today_used_seconds(State.active_backend) / 60.0, 1)
            soft = b_cfg.get("softLimitMinutes", 180)
            short = b_cfg.get("shortLabel", "GPU")
        msg = f"⚡ **UncenAI {short} is currently OFF** (preserving your free quota — used {used_mins} / {soft} min).\n\nClick **UncenAI GPU** in the bottom VS Code status bar (or run `Start-UncenAI.cmd`) to boot your backend, or pick an **OpenRouter Free** model."
        
        if is_stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunk = {"id": "chatcmpl-mock", "object": "chat.completion.chunk", "created": int(time.time()), "model": "offline", "choices": [{"index": 0, "delta": {"role": "assistant", "content": msg}, "finish_reason": None}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            resp = {"id": "chatcmpl-mock", "object": "chat.completion", "created": int(time.time()), "model": "offline", "choices": [{"index": 0, "message": {"role": "assistant", "content": msg}, "finish_reason": "stop"}]}
            self._send_json(200, resp)

def colab_start_sequence(my_run_id):
    with state_lock:
        State.last_activity_ts = time.time()
    try:
        cfg = get_config()
        b_cfg = get_backend_config("colab")
        session_name = b_cfg.get("sessionName", "uncenai")
        default_model = cfg.get("proxy", {}).get("defaultModel")
        colab_bin = resolve_cli_exe(b_cfg.get("exeName", "colab"))
        ssh_keygen_bin = resolve_openssh_bin("ssh-keygen")
        
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session_name):
            raise Exception("Invalid session name")

        if not os.path.exists(SSH_KEY_PATH):
            subprocess.run([ssh_keygen_bin, "-t", "ed25519", "-N", "", "-f", SSH_KEY_PATH], check=True)
        
        with state_lock:
            State.stage_detail = "Checking Google Colab Auth..."
            
        if is_cancelled(my_run_id): return _cleanup_backend("colab")
        auth_check = subprocess.run([colab_bin, "sessions"], capture_output=True, text=True, timeout=30)
        if auth_check.returncode != 0:
            msg = "Google Colab CLI not logged in. In VS Code click 'UncenAI GPU -> Provider Logins -> Google Colab OAuth' or run: py src/local_controller.py auth --backend colab"
            with state_lock:
                State.last_error = msg
            raise Exception(msg)
            
        if session_name not in auth_check.stdout:
            with state_lock:
                State.stage_detail = "Provisioning T4 runtime..."
            if is_cancelled(my_run_id): return _cleanup_backend("colab")
            subprocess.run([colab_bin, "new", "-s", session_name, "--gpu", "T4"], check=True)
        
        with state_lock:
            State.stage_detail = "Uploading files..."
        setup_script = os.path.join(STATE_DIR, "setup.py")
        with open(setup_script, "w") as f:
            f.write("import os; os.makedirs('/content/uncenai', exist_ok=True)")
        if is_cancelled(my_run_id): return _cleanup_backend("colab")
        subprocess.run([colab_bin, "exec", "-s", session_name, "-f", setup_script], check=True)
        subprocess.run([colab_bin, "upload", "-s", session_name, MODELS_JSON, "/content/uncenai/models.json"], check=True)
        remote_server_path = os.path.join(BASE_DIR, "src", "colab_remote_server.py")
        subprocess.run([colab_bin, "upload", "-s", session_name, remote_server_path, "/content/uncenai/colab_remote_server.py"], check=True)
        
        with state_lock:
            State.stage_detail = "Starting remote server..."
        boot_script = os.path.join(STATE_DIR, "boot_remote.py")
        with open(boot_script, "w") as f:
            f.write('''import urllib.request, subprocess, sys, time
try:
    urllib.request.urlopen("http://127.0.0.1:11481/health", timeout=1)
except:
    subprocess.Popen([sys.executable, "/content/uncenai/colab_remote_server.py", "--config", "/content/uncenai/models.json", "--port", "11481", "--llama-port", "11482"], stdout=open("/content/uncenai/remote.log", "ab"), stderr=subprocess.STDOUT, start_new_session=True)
for _ in range(15):
    try:
        if urllib.request.urlopen("http://127.0.0.1:11481/health", timeout=1).getcode() == 200: break
    except: pass
    time.sleep(1)
''')
        if is_cancelled(my_run_id): return _cleanup_backend("colab")
        subprocess.run([colab_bin, "exec", "-s", session_name, "-f", boot_script], check=True)
        
        with state_lock:
            State.stage_detail = "Starting SSH tunnel..."
        if is_cancelled(my_run_id): return _cleanup_backend("colab")
        spawn_ssh_tunnel("colab")
        
        for _ in range(30):
            if is_cancelled(my_run_id): return _cleanup_backend("colab")
            try:
                if urllib.request.urlopen("http://127.0.0.1:11481/health", timeout=1).getcode() == 200:
                    break
            except: pass
            time.sleep(1)
            
        with state_lock:
            model_id = State.target_model or default_model
        if is_cancelled(my_run_id): return _cleanup_backend("colab")
        load_remote_model(model_id, my_run_id)

    except Exception as e:
        if not is_cancelled(my_run_id):
            with state_lock:
                State.gpu_state = "error"
                State.stage_detail = f"Failed: {e}"
                if not State.last_error:
                    State.last_error = str(e)
    finally:
        if not is_cancelled(my_run_id):
            with state_lock:
                State.last_activity_ts = time.time()

def lightning_start_sequence(my_run_id):
    with state_lock:
        State.last_activity_ts = time.time()
    try:
        cfg = get_config()
        b_cfg = get_backend_config("lightning")
        studio_name = b_cfg.get("studioName", "uncenai")
        default_model = cfg.get("proxy", {}).get("defaultModel")
        ssh_bin = resolve_openssh_bin("ssh")
        scp_bin = resolve_openssh_bin("scp")
        
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", studio_name):
            raise Exception("Invalid studio name")
            
        with state_lock:
            State.stage_detail = "Starting Lightning Studio..."
            
        bridge = os.path.join(BASE_DIR, "src", "lightning_bridge.py")
        py_exe = resolve_tool_python("lightning-sdk")
        
        if is_cancelled(my_run_id): return _cleanup_backend("lightning")
        proc = subprocess.run([py_exe, bridge, "start", "--studio", studio_name, "--machine", "T4"], capture_output=True, text=True)
        if is_cancelled(my_run_id): return _cleanup_backend("lightning")
        if proc.returncode != 0:
            raise Exception("Bridge failed: " + proc.stderr)
            
        try:
            res = json.loads(proc.stdout)
            if not res.get("ok"): raise Exception(res.get("error"))
        except Exception as e:
            raise Exception("Failed to parse bridge output: " + str(e))
            
        ssh_user_host = res.get("sshUserHost")
        ssh_key_path = res.get("sshKeyPath")
        
        if not re.match(r"^s_[A-Za-z0-9_-]+@ssh\.lightning\.ai$", ssh_user_host):
            raise Exception("Invalid sshUserHost")
            
        if not os.path.isfile(ssh_key_path) or os.path.basename(ssh_key_path).startswith("-"):
            raise Exception("Invalid SSH key path")
            
        with state_lock:
            State.cached_lightning_user = ssh_user_host
            State.cached_lightning_key = ssh_key_path
            State.stage_detail = "Uploading files to Lightning..."
            
        devnull = "NUL" if os.name == "nt" else "/dev/null"
        ssh_cmd_base = [ssh_bin, "-i", ssh_key_path, "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={STATE_DIR.replace('\\', '/')}/lightning_known_hosts"]
        scp_cmd_base = [scp_bin, "-i", ssh_key_path, "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={STATE_DIR.replace('\\', '/')}/lightning_known_hosts"]
        
        if is_cancelled(my_run_id): return _cleanup_backend("lightning")
        subprocess.run(ssh_cmd_base + [ssh_user_host, "mkdir -p ~/uncenai"], check=True)
        subprocess.run(scp_cmd_base + [MODELS_JSON, f"{ssh_user_host}:~/uncenai/models.json"], check=True)
        remote_server_path = os.path.join(BASE_DIR, "src", "colab_remote_server.py")
        subprocess.run(scp_cmd_base + [remote_server_path, f"{ssh_user_host}:~/uncenai/colab_remote_server.py"], check=True)
        
        with state_lock:
            State.stage_detail = "Starting remote server..."
            
        boot_cmd = "curl -sf http://127.0.0.1:11481/health >/dev/null || (nohup python3 ~/uncenai/colab_remote_server.py --config ~/uncenai/models.json --port 11481 --llama-port 11482 >~/uncenai/remote.log 2>&1 & sleep 2)"
        if is_cancelled(my_run_id): return _cleanup_backend("lightning")
        subprocess.run(ssh_cmd_base + [ssh_user_host, boot_cmd], check=True)
        
        with state_lock:
            State.stage_detail = "Starting SSH tunnel..."
            
        if is_cancelled(my_run_id): return _cleanup_backend("lightning")
        spawn_ssh_tunnel("lightning", ssh_user_host, ssh_key_path)
        
        for _ in range(30):
            if is_cancelled(my_run_id): return _cleanup_backend("lightning")
            try:
                if urllib.request.urlopen("http://127.0.0.1:11481/health", timeout=1).getcode() == 200:
                    break
            except: pass
            time.sleep(1)
            
        with state_lock:
            model_id = State.target_model or default_model
        if is_cancelled(my_run_id): return _cleanup_backend("lightning")
        load_remote_model(model_id, my_run_id)

    except Exception as e:
        if not is_cancelled(my_run_id):
            with state_lock:
                State.gpu_state = "error"
                State.stage_detail = f"Failed: {e}"
                if not State.last_error:
                    State.last_error = str(e)
    finally:
        if not is_cancelled(my_run_id):
            with state_lock:
                State.last_activity_ts = time.time()

def modal_start_sequence(my_run_id):
    with state_lock:
        State.last_activity_ts = time.time()
        State.last_modal_inference_ts = time.time()
    try:
        cfg = get_config()
        target_model = State.target_model or cfg.get("proxy", {}).get("defaultModel")
        
        token_path = os.path.join(STATE_DIR, "modal_auth_token.txt")
        if not os.path.exists(token_path):
            with open(token_path, "w") as f:
                f.write(secrets.token_urlsafe(32))
        with open(token_path, "r") as f:
            token = f.read().strip()
            
        with state_lock:
            State.stage_detail = "Deploying to Modal..."
            
        env = os.environ.copy()
        env["UNCENAI_AUTH_TOKEN"] = token
        env["UNCENAI_MODAL_GPU"] = "T4"
        
        modal_exe = resolve_cli_exe(get_backend_config("modal").get("exeName", "modal"))
        modal_app_path = os.path.join(BASE_DIR, "src", "modal_app.py")
        
        if is_cancelled(my_run_id): return _cleanup_backend("modal")
        proc = subprocess.run([modal_exe, "deploy", modal_app_path], env=env, cwd=BASE_DIR, capture_output=True, text=True)
        if is_cancelled(my_run_id): return _cleanup_backend("modal")
        
        if proc.returncode != 0:
            msg = "Modal CLI not configured. Click 'UncenAI GPU -> Provider Logins -> Modal Setup' or run: uv tool install modal && py src/local_controller.py auth --backend modal"
            with state_lock:
                State.last_error = msg
            raise Exception(msg)
            
        url_match = re.search(r"https://[a-zA-Z0-9._-]+modal\.run", proc.stdout)
        if not url_match:
            raise Exception("Could not find Modal deployment URL in output")
            
        modal_url = url_match.group(0)
        with open(os.path.join(STATE_DIR, "modal_deployment.json"), "w") as f:
            json.dump({"url": modal_url}, f)
            
        if is_cancelled(my_run_id): return _cleanup_backend("modal")
        with state_lock:
            State.gpu_state = "ready"
            State.active_model = target_model
            State.stage_detail = f"Modal Serverless Ready ({target_model})"
            
        req = urllib.request.Request(f"{modal_url}/v1/load", data=json.dumps({"model": target_model}).encode(), headers={'Content-Type': 'application/json', 'X-UncenAI-Token': token}, method='POST')
        try:
            urllib.request.urlopen(req, timeout=5)
        except:
            pass

    except Exception as e:
        if not is_cancelled(my_run_id):
            with state_lock:
                State.gpu_state = "error"
                State.stage_detail = f"Failed: {e}"
                if not State.last_error:
                    State.last_error = str(e)
    finally:
        if not is_cancelled(my_run_id):
            with state_lock:
                State.last_activity_ts = time.time()

def modal_hotswap(model_id, my_run_id):
    with state_lock:
        State.target_model = model_id
        State.stage_detail = f"Preparing to load {model_id}..."
        State.last_activity_ts = time.time()
        
    try:
        with open(os.path.join(STATE_DIR, "modal_deployment.json"), "r") as f:
            modal_url = json.load(f)["url"]
        with open(os.path.join(STATE_DIR, "modal_auth_token.txt"), "r") as f:
            token = f.read().strip()
            
        req = urllib.request.Request(f"{modal_url}/v1/load", data=json.dumps({"model": model_id}).encode(), headers={'Content-Type': 'application/json', 'X-UncenAI-Token': token}, method='POST')
        urllib.request.urlopen(req, timeout=600)
    except:
        pass
    
    if not is_cancelled(my_run_id):
        with state_lock:
            State.active_model = model_id
            State.gpu_state = "ready"
            State.stage_detail = f"Modal Serverless Ready ({model_id})"
            State.last_activity_ts = time.time()

def load_remote_model(model_id, my_run_id):
    with state_lock:
        State.target_model = model_id
        State.stage_detail = f"Preparing to load {model_id}..."
        State.last_activity_ts = time.time()
    
    def monitor_health():
        while not is_cancelled(my_run_id):
            with state_lock:
                if State.gpu_state != "starting": break
            try:
                resp = json.loads(urllib.request.urlopen("http://127.0.0.1:11481/health", timeout=1).read())
                if is_cancelled(my_run_id): break
                with state_lock:
                    st = resp.get("stage")
                    pct = resp.get("downloadPercent")
                    if st and pct is not None:
                        State.stage_detail = f"{st} {pct}%"
                    elif st:
                        State.stage_detail = st
                        
                    State.download_percent = pct
                    State.active_model = resp.get("activeModel")
                    State.gpu_info = resp.get("gpu")
                    if st == "ready" and State.active_model == model_id:
                        State.gpu_state = "ready"
                        State.stage_detail = f"Ready ({State.active_model})"
                        break
            except: pass
            time.sleep(1.5)
            
    mon_thread = threading.Thread(target=monitor_health)
    mon_thread.start()

    try:
        req = urllib.request.Request("http://127.0.0.1:11481/v1/load", data=json.dumps({"model": model_id}).encode(), headers={'Content-Type': 'application/json'}, method='POST')
        urllib.request.urlopen(req, timeout=600)
    except Exception as e:
        if not is_cancelled(my_run_id):
            with state_lock:
                if State.gpu_state != "ready":
                    State.gpu_state = "error"
                    State.stage_detail = f"Load failed: {e}"
                    State.last_error = str(e)
    finally:
        if not is_cancelled(my_run_id):
            with state_lock:
                State.last_activity_ts = time.time()

def spawn_ssh_tunnel(backend, ssh_user_host=None, ssh_key_path=None):
    with state_lock:
        if State.ssh_proc and State.ssh_proc.poll() is None:
            return
            
        if ssh_key_path and (not os.path.isfile(ssh_key_path) or os.path.basename(ssh_key_path).startswith("-")):
            return
            
        ssh_bin = resolve_openssh_bin("ssh")
        devnull = "NUL" if os.name == "nt" else "/dev/null"

        if backend == "colab":
            cfg = get_backend_config("colab")
            session_name = cfg.get("sessionName", "uncenai")
            colab_bin = resolve_cli_exe(cfg.get("exeName", "colab"))
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session_name): return
            ssh_cmd = [
                ssh_bin, "-N", "-T",
                "-L", "127.0.0.1:11481:127.0.0.1:11481",
                "-i", SSH_KEY_PATH.replace('\\', '/'),
                "-o", "StrictHostKeyChecking=no", "-o", f"UserKnownHostsFile={devnull}",
                "-o", "ServerAliveInterval=20", "-o", "ServerAliveCountMax=3",
                "-o", "ExitOnForwardFailure=yes",
                "-o", f"ProxyCommand=\"{colab_bin.replace('\\', '/')}\" ssh --proxy-mode --force -s {session_name} -i \"{SSH_KEY_PATH.replace('\\', '/')}\"",
                "root@colab-runtime"
            ]
        elif backend == "lightning" and ssh_user_host and ssh_key_path:
            ssh_cmd = [
                ssh_bin, "-N", "-T",
                "-L", "127.0.0.1:11481:127.0.0.1:11481",
                "-i", ssh_key_path.replace('\\', '/'),
                "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=accept-new",
                "-o", f"UserKnownHostsFile={STATE_DIR.replace('\\', '/')}/lightning_known_hosts",
                "-o", "ServerAliveInterval=20", "-o", "ServerAliveCountMax=3",
                "-o", "ExitOnForwardFailure=yes",
                ssh_user_host
            ]
        else:
            return
            
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000 # CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
            
        State.ssh_proc = subprocess.Popen(ssh_cmd, **kwargs)
        State.ssh_backend = backend

def watchdog():
    while True:
        time.sleep(15)
        with state_lock:
            current_state = State.gpu_state
            backend = State.active_backend
            idle_mins = (time.time() - State.last_activity_ts) / 60.0
            modal_active = (backend == "modal" and current_state == "ready" and (time.time() - State.last_modal_inference_ts) <= 180)
            
        if current_state in ("starting", "ready"):
            if backend == "modal":
                if modal_active:
                    add_quota_seconds(15, backend)
            else:
                add_quota_seconds(15, backend)
                
            used_mins = get_today_used_seconds(backend) / 60.0
            b_cfg = get_backend_config(backend)
            auto_stop = b_cfg.get("idleAutoStopMinutes", 20)
            hard_limit = b_cfg.get("hardLimitMinutes", 240)
            
            stop_reason = None
            if backend == "modal":
                if used_mins >= hard_limit: stop_reason = "hard_limit"
            else:
                if idle_mins >= auto_stop or used_mins >= hard_limit: stop_reason = "idle_or_limit"
            
            if stop_reason:
                stop_active_backend()
                
        if current_state == "ready" and backend != "modal":
            try:
                urllib.request.urlopen("http://127.0.0.1:11481/health", timeout=3).read()
            except:
                with state_lock:
                    c_user = State.cached_lightning_user
                    c_key = State.cached_lightning_key
                if backend == "colab":
                    spawn_ssh_tunnel("colab")
                elif backend == "lightning" and c_user and c_key:
                    spawn_ssh_tunnel("lightning", c_user, c_key)

def startup_adopt_session():
    State.active_backend = load_active_backend()
    
    with state_lock:
        backend = State.active_backend
        
    if backend == "colab":
        b_cfg = get_backend_config("colab")
        session_name = b_cfg.get("sessionName", "uncenai")
        colab_bin = resolve_cli_exe(b_cfg.get("exeName", "colab"))
        
        try:
            auth_check = subprocess.run([colab_bin, "sessions"], capture_output=True, text=True, timeout=15)
            if auth_check.returncode == 0 and session_name in auth_check.stdout:
                spawn_ssh_tunnel("colab")
                for _ in range(15):
                    try:
                        resp = json.loads(urllib.request.urlopen("http://127.0.0.1:11481/health", timeout=1).read())
                        if resp.get("ok"):
                            with state_lock:
                                State.gpu_state = "ready"
                                State.active_model = resp.get("activeModel")
                                State.gpu_info = resp.get("gpu")
                                State.stage_detail = f"Ready ({State.active_model})" if State.active_model else "Ready (Idle)"
                                State.last_activity_ts = time.time()
                            break
                    except: pass
                    time.sleep(1)
        except:
            pass

def cmd_daemon():
    os.makedirs(STATE_DIR, exist_ok=True)
    threading.Thread(target=startup_adopt_session, daemon=True).start()
    t = threading.Thread(target=watchdog, daemon=True)
    t.start()
    server = ThreadingHTTPServer(("127.0.0.1", 11480), GatewayHandler)
    server.serve_forever()

def cmd_ensure_daemon():
    os.makedirs(STATE_DIR, exist_ok=True)
    try:
        urllib.request.urlopen("http://127.0.0.1:11480/uncenai/status", timeout=2).read()
    except:
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = 0x00000008 | 0x08000000 # DETACHED_PROCESS | CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
            
        with open(DAEMON_LOG, "a") as f:
            subprocess.Popen([sys.executable, __file__, "daemon"], stdout=f, stderr=f, **kwargs)
        for _ in range(30):
            try:
                urllib.request.urlopen("http://127.0.0.1:11480/uncenai/status", timeout=1).read()
                break
            except:
                time.sleep(0.5)

def cmd_start(backend, model, wait, ignore_quota):
    cmd_ensure_daemon()
    req = {"model": model, "ignoreQuota": ignore_quota}
    if backend: req["backend"] = backend
    req_bytes = json.dumps(req).encode('utf-8')
    req_obj = urllib.request.Request("http://127.0.0.1:11480/uncenai/start", data=req_bytes, headers={'Content-Type': 'application/json'}, method='POST')
    urllib.request.urlopen(req_obj)
    if wait:
        while True:
            try:
                resp = json.loads(urllib.request.urlopen("http://127.0.0.1:11480/uncenai/status").read())
                state = resp["gpuState"]
                print(f"Status: {state} - {resp['stageDetail']}")
                if state in ("ready", "error"):
                    break
            except: pass
            time.sleep(2)

def cmd_stop():
    try:
        req = urllib.request.Request("http://127.0.0.1:11480/uncenai/stop", data=b"{}", headers={'Content-Type': 'application/json'}, method='POST')
        urllib.request.urlopen(req, timeout=5)
        print("Stopped via daemon.")
    except:
        b = load_active_backend()
        _cleanup_backend(b)
        print("Stopped via fallback CLI.")

def cmd_status(json_output):
    try:
        resp = json.loads(urllib.request.urlopen("http://127.0.0.1:11480/uncenai/status", timeout=2).read())
    except:
        resp = {"daemonRunning": False, "gpuState": "off", "stageDetail": "Daemon not running", "quota": {"usedMinutesToday": 0}}
    if json_output:
        print(json.dumps(resp, indent=2))
    else:
        print(f"Daemon: {'Running' if resp.get('daemonRunning') else 'Stopped'}")
        b_lbl = resp.get("activeBackendLabel", resp.get("activeBackend"))
        print(f"Backend: {b_lbl}")
        print(f"State: {resp.get('gpuState')} - {resp.get('stageDetail')}")
        q = resp.get("quota", {})
        print(f"Quota Used: {q.get('usedMinutes', 0)} / {q.get('softLimitMinutes', 0)} min")

def cmd_auth(backend):
    backend = backend or load_active_backend()
    b_cfg = get_backend_config(backend)
    
    if backend == "colab":
        colab_bin = resolve_cli_exe(b_cfg.get("exeName", "colab"))
        subprocess.run([colab_bin, "sessions"])
        subprocess.run([colab_bin, "usage"])
    elif backend == "lightning":
        py_exe = resolve_cli_exe(b_cfg.get("exeName", "lightning"))
        subprocess.run([py_exe, "login"])
    elif backend == "modal":
        modal_exe = resolve_cli_exe(b_cfg.get("exeName", "modal"))
        subprocess.run([modal_exe, "setup"])

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="cmd")
    subparsers.add_parser("daemon")
    subparsers.add_parser("ensure-daemon")
    p_start = subparsers.add_parser("start")
    p_start.add_argument("--backend", choices=["colab", "lightning", "modal"])
    p_start.add_argument("--model")
    p_start.add_argument("--wait", action="store_true")
    p_start.add_argument("--ignore-quota", action="store_true")
    subparsers.add_parser("stop")
    p_status = subparsers.add_parser("status")
    p_status.add_argument("--json", action="store_true")
    p_auth = subparsers.add_parser("auth")
    p_auth.add_argument("--backend", choices=["colab", "lightning", "modal"])

    args = parser.parse_args()
    if args.cmd == "daemon": cmd_daemon()
    elif args.cmd == "ensure-daemon": cmd_ensure_daemon()
    elif args.cmd == "start": cmd_start(args.backend, args.model, args.wait, args.ignore_quota)
    elif args.cmd == "stop": cmd_stop()
    elif args.cmd == "status": cmd_status(getattr(args, "json", False))
    elif args.cmd == "auth": cmd_auth(args.backend)
