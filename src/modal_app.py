import os
import json
import subprocess
import secrets
import modal

MODELS_JSON_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "models.json")
SERVER_PY_PATH = os.path.join(os.path.dirname(__file__), "colab_remote_server.py")
STATE_DIR = os.path.abspath(
    os.environ.get("UNCENAI_STATE_DIR")
    or os.path.join(os.path.dirname(os.path.dirname(__file__)), ".state")
)

if modal.is_local():
    auth_token = os.environ.get("UNCENAI_AUTH_TOKEN", "")
    token_path = os.path.join(STATE_DIR, "modal_auth_token.txt")
    if len(auth_token) < 16:
        if os.path.exists(token_path):
            with open(token_path, "r", encoding="utf-8") as f:
                auth_token = f.read().strip()
        
        if len(auth_token) < 16:
            os.makedirs(STATE_DIR, exist_ok=True)
            auth_token = secrets.token_urlsafe(32)
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(auth_token)
                
        os.environ["UNCENAI_AUTH_TOKEN"] = auth_token

    if len(auth_token) < 16:
        raise RuntimeError("UNCENAI_AUTH_TOKEN must be >= 16 characters")

modal_cfg = {}
if os.path.exists(MODELS_JSON_PATH):
    with open(MODELS_JSON_PATH, "r", encoding="utf-8") as f:
        models_data = json.load(f)
        modal_cfg = models_data.get("backends", {}).get("modal", {})

GPU_TYPE = os.environ.get("UNCENAI_MODAL_GPU", modal_cfg.get("gpu", "T4"))
SCALEDOWN_SEC = int(modal_cfg.get("scaledownWindowSeconds", 180))
APP_NAME = modal_cfg.get("appName", "uncenai-gpu")

app = modal.App(APP_NAME)
volume = modal.Volume.from_name("uncenai-models", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("curl", "ca-certificates", "tar")
    .add_local_file(SERVER_PY_PATH, remote_path="/root/uncenai/colab_remote_server.py")
    .add_local_file(MODELS_JSON_PATH, remote_path="/root/uncenai/models.json")
)

auth_secret = modal.Secret.from_dict({"UNCENAI_AUTH_TOKEN": os.environ.get("UNCENAI_AUTH_TOKEN", "")})

@app.function(
    image=image,
    gpu=GPU_TYPE,
    volumes={"/cache": volume},
    secrets=[auth_secret],
    scaledown_window=SCALEDOWN_SEC,
    timeout=600,
    max_containers=1
)
@modal.web_server(11481, startup_timeout=300)
def serve():
    subprocess.Popen([
        "python3", "/root/uncenai/colab_remote_server.py", 
        "--config", "/root/uncenai/models.json", 
        "--host", "0.0.0.0", 
        "--port", "11481", 
        "--llama-port", "11482", 
        "--cache-dir", "/cache"
    ])
