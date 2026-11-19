import argparse
import hmac
import http.client
import http.server
import json
import os
import pathlib
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
import urllib.error

# Global state
server_state = {
    "stage": "idle", # idle, downloading, loading, ready, error
    "activeModel": None,
    "targetModel": None,
    "downloadPercent": 0.0,
    "llama_process": None,
    "error": None
}
state_lock = threading.Lock()
load_lock = threading.Lock()
cache_dir = "/content/uncenai-cache"
config_cache = {}

def get_gpu_stats():
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3
        )
        if res.returncode == 0:
            parts = res.stdout.strip().split(",")
            if len(parts) >= 3:
                return {
                    "name": parts[0].strip(),
                    "vramUsedMB": int(float(parts[1].strip())),
                    "vramTotalMB": int(float(parts[2].strip()))
                }
    except Exception:
        pass
    return None

def safe_extract_tar(tar, dest_dir):
    if hasattr(tarfile, "data_filter") and sys.version_info >= (3, 12):
        tar.extractall(path=dest_dir, filter="data")
        return
        
    dest_abs = os.path.abspath(dest_dir)
    safe_members = []
    for member in tar.getmembers():
        if not (member.isfile() or member.isdir()):
            continue
        if member.issym() or member.islnk():
            continue
        member_path = os.path.abspath(os.path.join(dest_abs, member.name))
        if not member_path.startswith(dest_abs + os.sep) and member_path != dest_abs:
            continue
        safe_members.append(member)
    tar.extractall(path=dest_dir, members=safe_members)

def ensure_llama_server():
    bin_dir = os.path.join(cache_dir, "bin", "llama")
    os.makedirs(bin_dir, exist_ok=True)
    llama_bin = os.path.join(bin_dir, "llama-server")
    
    if os.path.exists(llama_bin) and os.access(llama_bin, os.X_OK):
        return llama_bin
        
    req = urllib.request.Request("https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=5", headers={"User-Agent": "uncenai/1.0"})
    with urllib.request.urlopen(req) as resp:
        releases = json.loads(resp.read().decode('utf-8'))
        
    cuda_13 = False
    cuda_12 = False
    try:
        if os.path.exists("/usr/local/cuda/version.json"):
            with open("/usr/local/cuda/version.json") as f:
                ver = f.read()
                if "13." in ver: cuda_13 = True
                else: cuda_12 = True
        else:
            nvcc_res = subprocess.run(["nvcc", "--version"], capture_output=True, text=True)
            if "release 13." in nvcc_res.stdout: cuda_13 = True
            elif "release 12." in nvcc_res.stdout: cuda_12 = True
    except Exception:
        pass

    target_suffix = "ubuntu-x64.tar.gz"
    if cuda_13:
        target_suffix = "ubuntu-cuda-13.5-x64.tar.gz"
    else:
        target_suffix = "ubuntu-cuda-12.8-x64.tar.gz"

    tag = releases[0]["tag_name"]
    download_url = None
    fallback_url = None
    cudart_url = None
    for asset in releases[0].get("assets", []):
        name = asset["name"]
        if name.startswith(f"llama-{tag}-bin-") and name.endswith(target_suffix):
            download_url = asset["browser_download_url"]
        if name.startswith(f"llama-{tag}-bin-") and name.endswith("ubuntu-x64.tar.gz"):
            fallback_url = asset["browser_download_url"]
        if name.startswith(f"cudart-llama-{tag}-bin-") and name.endswith(target_suffix):
            cudart_url = asset["browser_download_url"]

    if not download_url:
        download_url = fallback_url
    if not download_url:
        raise Exception("Could not find suitable llama-server release")
        
    tar_path = os.path.join(bin_dir, "llama.tar.gz")
    with urllib.request.urlopen(download_url) as resp, open(tar_path, 'wb') as f:
        shutil.copyfileobj(resp, f)
        
    with tarfile.open(tar_path, "r:gz") as tar:
        safe_extract_tar(tar, bin_dir)
    os.remove(tar_path)
    
    actual_bin = None
    for root, dirs, files in os.walk(bin_dir):
        for file in files:
            if file == "llama-server":
                actual_bin = os.path.join(root, file)
            elif file.endswith(".so") or ".so." in file:
                if root != bin_dir:
                    try:
                        shutil.move(os.path.join(root, file), os.path.join(bin_dir, file))
                    except:
                        pass
            
    if actual_bin and os.path.abspath(actual_bin) != os.path.abspath(llama_bin):
        shutil.move(actual_bin, llama_bin)
    elif not actual_bin:
        raise Exception("llama-server binary not found in archive")
        
    os.chmod(llama_bin, 0o755)
    
    env = os.environ.copy()
    ld_paths = [bin_dir, "/usr/local/cuda/lib64", "/usr/lib64-nvidia"]
    env["LD_LIBRARY_PATH"] = ":".join(ld_paths) + ":" + env.get("LD_LIBRARY_PATH", "")
    
    ldd_res = subprocess.run(["ldd", llama_bin], capture_output=True, text=True, env=env)
    if "not found" in ldd_res.stdout and ("libcudart.so" in ldd_res.stdout or "libcublas.so" in ldd_res.stdout):
        if cudart_url:
            cudart_tar = os.path.join(bin_dir, "cudart.tar.gz")
            with urllib.request.urlopen(cudart_url) as resp, open(cudart_tar, 'wb') as f:
                shutil.copyfileobj(resp, f)
            with tarfile.open(cudart_tar, "r:gz") as tar:
                safe_extract_tar(tar, bin_dir)
            os.remove(cudart_tar)
            for root, dirs, files in os.walk(bin_dir):
                for file in files:
                    if file.endswith(".so") or ".so." in file:
                        if root != bin_dir:
                            try:
                                shutil.move(os.path.join(root, file), os.path.join(bin_dir, file))
                            except:
                                pass
    return llama_bin

def ensure_model_gguf(model_cfg):
    models_dir = os.path.join(cache_dir, "models")
    os.makedirs(models_dir, exist_ok=True)
    
    model_id = model_cfg["id"]
    hf_repo = model_cfg["hfRepo"]
    hf_file = model_cfg["hfFile"]
    
    hf_file_local = os.path.basename(hf_file)
    if not hf_file_local or hf_file_local in (".", ".."):
        raise ValueError("Invalid hfFile parameter")
        
    final_path = os.path.join(models_dir, hf_file_local)
    part_path = final_path + ".part"
    
    if os.path.exists(final_path):
        return final_path
        
    url = f"https://huggingface.co/{hf_repo}/resolve/main/{hf_file}"
    headers = {}
    downloaded = 0
    if os.path.exists(part_path):
        downloaded = os.path.getsize(part_path)
        headers["Range"] = f"bytes={downloaded}-"
        
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            total_size = int(resp.headers.get("Content-Length", 0)) + downloaded
            mode = "ab" if downloaded > 0 and resp.status == 206 else "wb"
            if mode == "wb":
                downloaded = 0
                
            with state_lock:
                server_state["stage"] = "downloading"
                server_state["targetModel"] = model_id
                
            with open(part_path, mode) as f:
                while True:
                    chunk = resp.read(4 * 1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    with state_lock:
                        server_state["downloadPercent"] = round(downloaded * 100.0 / total_size, 1) if total_size > 0 else 0.0
                        
        os.replace(part_path, final_path)
        return final_path
    except urllib.error.HTTPError as e:
        if e.code == 416:
            os.replace(part_path, final_path)
            return final_path
        raise e

def load_model_thread(model_id, llama_port):
    with state_lock:
        if server_state["activeModel"] == model_id and server_state["llama_process"] and server_state["llama_process"].poll() is None:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", llama_port, timeout=1)
                conn.request("GET", "/health")
                resp = conn.getresponse()
                if resp.status == 200:
                    return
            except Exception:
                pass
                
        if server_state["llama_process"]:
            try:
                server_state["llama_process"].terminate()
                server_state["llama_process"].wait(8)
            except subprocess.TimeoutExpired:
                server_state["llama_process"].kill()
                server_state["llama_process"].wait()
            except Exception:
                pass
            server_state["llama_process"] = None
            server_state["activeModel"] = None
            time.sleep(1) 

        server_state["error"] = None
        server_state["targetModel"] = model_id
        
    model_cfg = next((m for m in config_cache.get("colabModels", []) if m["id"] == model_id), None)
    if not model_cfg:
        with state_lock:
            server_state["error"] = f"Model {model_id} not found in config"
            server_state["stage"] = "error"
        return
        
    try:
        llama_bin = ensure_llama_server()
        gguf_path = ensure_model_gguf(model_cfg)
        
        with state_lock:
            server_state["stage"] = "loading"
            
        ctx = model_cfg.get("contextWindow", 8192)
        extra = model_cfg.get("extraLlamaArgs", [])
        
        args = [
            llama_bin, "-m", gguf_path, "--alias", model_id, 
            "--host", "127.0.0.1", "--port", str(llama_port), 
            "--jinja", "-ngl", "99", "-fa", "on", 
            "-ctk", "q8_0", "-ctv", "q8_0", "-c", str(ctx), 
            "-np", "1"
        ] + extra
        
        env = os.environ.copy()
        bin_dir = os.path.dirname(llama_bin)
        ld_paths = [bin_dir, "/usr/local/cuda/lib64", "/usr/lib64-nvidia"]
        env["LD_LIBRARY_PATH"] = ":".join(ld_paths) + ":" + env.get("LD_LIBRARY_PATH", "")
        
        with open(os.path.join(cache_dir, "llama-server.log"), "ab") as log_file:
            proc = subprocess.Popen(args, stdout=log_file, stderr=subprocess.STDOUT, env=env)
        
        time.sleep(2)
        if proc.poll() is not None:
            if "-fa" in args and "on" in args[args.index("-fa"):]:
                args[args.index("-fa") + 1] = ""
                args.remove("")
                with open(os.path.join(cache_dir, "llama-server.log"), "ab") as log_file:
                    proc = subprocess.Popen(args, stdout=log_file, stderr=subprocess.STDOUT, env=env)
            
        with state_lock:
            server_state["llama_process"] = proc
            
        for _ in range(180):
            try:
                conn = http.client.HTTPConnection("127.0.0.1", llama_port, timeout=1)
                conn.request("GET", "/health")
                resp = conn.getresponse()
                if resp.status == 200:
                    with state_lock:
                        server_state["stage"] = "ready"
                        server_state["activeModel"] = model_id
                        server_state["downloadPercent"] = 100.0
                    return
            except Exception:
                pass
            time.sleep(1)
            
        with state_lock:
            server_state["error"] = "Timeout waiting for llama-server to be healthy"
            server_state["stage"] = "error"
            
    except Exception as e:
        with state_lock:
            server_state["error"] = str(e)
            server_state["stage"] = "error"

def load_model_locked(model_id, llama_port):
    with load_lock:
        load_model_thread(model_id, llama_port)
        with state_lock:
            return server_state["error"] is None

class ServerHandler(http.server.BaseHTTPRequestHandler):
    def __init__(self, *args, llama_port=None, auth_token="", **kwargs):
        self.llama_port = llama_port
        self.auth_token = auth_token
        super().__init__(*args, **kwargs)

    def _check_auth(self):
        if not self.auth_token:
            return True
        provided = self.headers.get("X-UncenAI-Token", "")
        if not provided:
            auth_header = self.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                provided = auth_header[7:]
        return bool(provided) and hmac.compare_digest(provided.encode("utf-8"), self.auth_token.encode("utf-8"))

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if self.auth_token and not self._check_auth():
                self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))
                return
                
            with state_lock:
                st = server_state.copy()
            out = {
                "ok": st["error"] is None,
                "stage": st["stage"],
                "activeModel": st["activeModel"],
                "targetModel": st["targetModel"],
                "downloadPercent": st["downloadPercent"],
                "gpu": get_gpu_stats(),
                "error": st["error"]
            }
            self.wfile.write(json.dumps(out).encode("utf-8"))
        elif self.path == "/v1/models":
            if not self._check_auth():
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Unauthorized"}).encode("utf-8"))
                return
                
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            models_res = {
                "object": "list",
                "data": [
                    {
                        "id": m["id"],
                        "object": "model",
                        "created": int(time.time()),
                        "owned_by": "uncenai"
                    } for m in config_cache.get("colabModels", [])
                ]
            }
            self.wfile.write(json.dumps(models_res).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()
            
    def do_POST(self):
        if not self._check_auth():
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Unauthorized"}).encode("utf-8"))
            return
            
        if self.path == "/v1/load":
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length).decode("utf-8"))
            model_id = body.get("model", config_cache.get("proxy", {}).get("defaultModel"))
            
            success = load_model_locked(model_id, self.llama_port)
            if not success:
                self.send_response(500)
            else:
                self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            with state_lock:
                st = server_state.copy()
            out = {
                "ok": st["error"] is None,
                "stage": st["stage"],
                "activeModel": st["activeModel"],
                "targetModel": st["targetModel"],
                "downloadPercent": st["downloadPercent"],
                "gpu": get_gpu_stats(),
                "error": st["error"]
            }
            self.wfile.write(json.dumps(out).encode("utf-8"))
            
        elif self.path == "/v1/chat/completions":
            content_length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(content_length)
            body = json.loads(raw_body.decode("utf-8"))
            
            model_id = body.get("model")
            valid_models = [m["id"] for m in config_cache.get("colabModels", [])]
            if not model_id or model_id == "default" or model_id not in valid_models:
                with state_lock:
                    model_id = server_state["activeModel"]
                if not model_id:
                    model_id = config_cache.get("proxy", {}).get("defaultModel")
            
            success = load_model_locked(model_id, self.llama_port)
            if not success:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Failed to load model"}).encode("utf-8"))
                return
                
            conn = http.client.HTTPConnection("127.0.0.1", self.llama_port, timeout=600)
            headers = {}
            for k, v in self.headers.items():
                if k.lower() not in ["host", "connection", "content-length", "x-uncenai-token", "authorization"]:
                    headers[k] = v
            headers["Content-Length"] = str(len(raw_body))
            
            try:
                conn.request("POST", "/v1/chat/completions", body=raw_body, headers=headers)
                resp = conn.getresponse()
                
                self.send_response(resp.status)
                for k, v in resp.getheaders():
                    if k.lower() not in ["transfer-encoding", "connection"]:
                        self.send_header(k, v)
                self.send_header("Connection", "close")
                self.end_headers()
                
                while True:
                    chunk = resp.read1(4096) if hasattr(resp, "read1") else resp.read(4096)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except Exception as e:
                pass
            finally:
                conn.close()
                
        else:
            self.send_response(404)
            self.end_headers()

def make_handler_class(llama_port, auth_token):
    class Handler(ServerHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, llama_port=llama_port, auth_token=auth_token, **kwargs)
    return Handler

def main():
    global cache_dir
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/content/uncenai/models.json")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11481)
    parser.add_argument("--llama-port", type=int, default=11482)
    parser.add_argument("--preload")
    parser.add_argument("--cache-dir", default=os.environ.get("UNCENAI_CACHE_DIR", ""))
    parser.add_argument("--auth-token", default=os.environ.get("UNCENAI_AUTH_TOKEN", ""))
    args = parser.parse_args()
    
    if args.host not in ("127.0.0.1", "localhost", "::1") and len(args.auth_token) < 16:
        import sys
        print("Refusing to bind non-loopback host without >=16-char --auth-token / UNCENAI_AUTH_TOKEN", file=sys.stderr)
        sys.exit(2)
        
    with open(args.config, "r", encoding="utf-8") as f:
        global config_cache
        config_cache = json.load(f)
        
    if args.cache_dir:
        cache_dir = args.cache_dir
    elif os.path.exists("/content/drive/MyDrive") and os.access("/content/drive/MyDrive", os.W_OK):
        cache_dir = "/content/drive/MyDrive/uncenai-cache"
    elif os.path.exists("/content") and os.access("/content", os.W_OK):
        cache_dir = "/content/uncenai-cache"
    else:
        cache_dir = os.path.expanduser("~/uncenai-cache")
        
    if args.preload:
        threading.Thread(target=load_model_locked, args=(args.preload, args.llama_port), daemon=True).start()
        
    server = http.server.ThreadingHTTPServer((args.host, args.port), make_handler_class(args.llama_port, args.auth_token))
    print(f"Server listening on {args.host}:{args.port}")
    server.serve_forever()

if __name__ == "__main__":
    main()
