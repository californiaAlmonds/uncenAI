#!/usr/bin/env python3
"""
build_vsix.py - Packaging script for UncenAI VS Code Marketplace .vsix distribution.

Stages backend runtime components into vscode-uncenai/backend/, updates package metadata,
and packages the .vsix bundle via @vscode/vsce.

Usage:
    py scripts/build_vsix.py
    py scripts/build_vsix.py --publisher my-publisher --repo https://github.com/my-user/uncenAI
"""

import os
import sys
import json
import shutil
import argparse
import subprocess

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXT_DIR = os.path.join(REPO_ROOT, "vscode-uncenai")
BACKEND_DIR = os.path.join(EXT_DIR, "backend")
PACKAGE_JSON = os.path.join(EXT_DIR, "package.json")
LICENSE_SRC = os.path.join(REPO_ROOT, "LICENSE")
LICENSE_DEST = os.path.join(EXT_DIR, "LICENSE")
def get_output_vsix():
    with open(PACKAGE_JSON, "r", encoding="utf-8") as f:
        pkg = json.load(f)
    version = pkg.get("version", "1.0.0")
    name = pkg.get("name", "uncenai-free-gpu")
    return os.path.join(REPO_ROOT, f"{name}-{version}.vsix")

SRC_FILES = [
    "local_controller.py",
    "colab_remote_server.py",
    "lightning_bridge.py",
    "modal_app.py"
]


def stage_backend_files():
    """Copies required backend controller and configuration files into the extension backend/ dir."""
    print("[1/3] Staging backend components into vscode-uncenai/backend/...")

    # Copy LICENSE
    if os.path.exists(LICENSE_SRC):
        shutil.copy2(LICENSE_SRC, LICENSE_DEST)
        print(f"  - Copied LICENSE -> {LICENSE_DEST}")

    # Prepare backend/ subdirs
    os.makedirs(os.path.join(BACKEND_DIR, "src"), exist_ok=True)
    os.makedirs(os.path.join(BACKEND_DIR, "config"), exist_ok=True)
    os.makedirs(os.path.join(BACKEND_DIR, "scripts"), exist_ok=True)

    # Copy src files
    for filename in SRC_FILES:
        src_path = os.path.join(REPO_ROOT, "src", filename)
        if os.path.exists(src_path):
            dest_path = os.path.join(BACKEND_DIR, "src", filename)
            shutil.copy2(src_path, dest_path)
            print(f"  - Staged src/{filename}")
        else:
            print(f"  [WARN] Source file not found: {src_path}", file=sys.stderr)

    # Copy config/models.json
    models_src = os.path.join(REPO_ROOT, "config", "models.json")
    if os.path.exists(models_src):
        shutil.copy2(models_src, os.path.join(BACKEND_DIR, "config", "models.json"))
        print("  - Staged config/models.json")

    # Copy scripts/configure_vscode.py
    cfg_src = os.path.join(REPO_ROOT, "scripts", "configure_vscode.py")
    if os.path.exists(cfg_src):
        shutil.copy2(cfg_src, os.path.join(BACKEND_DIR, "scripts", "configure_vscode.py"))
        print("  - Staged scripts/configure_vscode.py")


def update_package_metadata(publisher=None, repo_url=None):
    """Updates publisher and repository in vscode-uncenai/package.json if provided."""
    if not publisher and not repo_url:
        return

    print("[2/3] Updating package.json metadata...")
    with open(PACKAGE_JSON, "r", encoding="utf-8") as f:
        pkg = json.load(f)

    if publisher:
        pkg["publisher"] = publisher
        print(f"  - Updated publisher: {publisher}")

    if repo_url:
        pkg["repository"] = {
            "type": "git",
            "url": repo_url
        }
        print(f"  - Updated repository: {repo_url}")

    with open(PACKAGE_JSON, "w", encoding="utf-8") as f:
        json.dump(pkg, f, indent=2, ensure_ascii=False)
        f.write("\n")


def run_vsce_package():
    """Runs @vscode/vsce package to create the distributable .vsix bundle."""
    output_vsix = get_output_vsix()
    print(f"[3/3] Packaging VSIX bundle -> {output_vsix}...")

    env = os.environ.copy()
    if os.path.exists("E:\\"):
        os.makedirs("E:\\npm-cache", exist_ok=True)
        env["npm_config_cache"] = "E:\\npm-cache"

    npx_bin = shutil.which("npx.cmd" if sys.platform == "win32" else "npx") or shutil.which("npx") or "npx"
    cmd = [
        npx_bin,
        "--yes",
        "@vscode/vsce",
        "package",
        "--allow-missing-repository",
        "-o",
        output_vsix
    ]

    print(f"  Running: {' '.join(cmd)} in {EXT_DIR}")
    res = subprocess.run(cmd, cwd=EXT_DIR, env=env, shell=False)
    if res.returncode != 0:
        print(f"[ERROR] vsce package failed with exit code {res.returncode}", file=sys.stderr)
        sys.exit(res.returncode)

    print(f"\n[SUCCESS] Successfully built: {output_vsix}")


def main():
    parser = argparse.ArgumentParser(description="Package UncenAI VS Code Extension (.vsix)")
    parser.add_argument("--publisher", help="VS Code Marketplace publisher ID")
    parser.add_argument("--repo", help="GitHub repository HTTPS URL")
    parser.add_argument("--stage-only", action="store_true", help="Only stage backend files without invoking vsce package")

    args = parser.parse_args()

    stage_backend_files()
    update_package_metadata(args.publisher, args.repo)

    if not args.stage_only:
        run_vsce_package()


if __name__ == "__main__":
    main()
