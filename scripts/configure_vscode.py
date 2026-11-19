#!/usr/bin/env python3
"""
configure_vscode.py - Idempotent VS Code Copilot Agent-Mode & Extension Installer for UncenAI.

Usage:
    py scripts/configure_vscode.py --dry-run
    py scripts/configure_vscode.py --apply
    py scripts/configure_vscode.py --apply --models-only
"""

import os
import sys
import json
import shutil
import argparse
from pathlib import Path

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "models.json")

# Respect UNCENAI_STATE_DIR env var if set
STATE_DIR = os.environ.get("UNCENAI_STATE_DIR", os.path.join(BASE_DIR, ".state"))
BACKUP_PATH = os.path.join(STATE_DIR, "chatLanguageModels.backup.json")
EXTENSION_SRC = os.path.join(BASE_DIR, "vscode-uncenai")


def resolve_vscode_user_dir():
    """Resolves VS Code User configuration directory portably."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", os.path.expanduser("~\\AppData\\Roaming"))
        return os.path.join(appdata, "Code", "User")
    elif sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Code/User")
    else:
        # Linux / FreeBSD / other UNIX
        config_home = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
        return os.path.join(config_home, "Code", "User")


def resolve_vscode_extensions_dir():
    """Resolves VS Code extensions directory portably."""
    return os.path.expanduser("~/.vscode/extensions")


VSCODE_USER_DIR = resolve_vscode_user_dir()
CHAT_MODELS_PATH = os.path.join(VSCODE_USER_DIR, "chatLanguageModels.json")

EXTENSIONS_DIR = resolve_vscode_extensions_dir()
EXTENSION_DEST = os.path.join(EXTENSIONS_DIR, "uncenai.uncenai-free-gpu-1.0.0")
OLD_EXTENSION_DEST = os.path.join(EXTENSIONS_DIR, "uncenai.colab-launcher-1.0.0")

UNCENAI_GROUP_NAME = "UncenAI (Colab T4 GPU)"
UNCENAI_VENDOR = "customendpoint"


def build_uncenai_group(config):
    """Builds the customendpoint group for VS Code Copilot Agent-Mode."""
    colab_models = config.get("colabModels", [])
    models_list = []

    for m in colab_models:
        model_entry = {
            "id": m["id"],
            "name": m["name"],
            "url": "http://127.0.0.1:11480/v1/chat/completions",
            "toolCalling": m.get("toolCalling", True),
            "vision": m.get("vision", False),
            "thinking": m.get("thinking", False),
            "contextWindow": m.get("contextWindow", 24576),
            "maxOutputTokens": m.get("maxOutputTokens", 8192),
            "editTools": ["find-replace", "multi-find-replace"]
        }
        models_list.append(model_entry)

    group = {
        "name": UNCENAI_GROUP_NAME,
        "vendor": UNCENAI_VENDOR,
        "apiKey": "uncenai-local",
        "apiType": "chat-completions",
        "models": models_list
    }
    return group


def load_chat_language_models():
    """Loads existing chatLanguageModels.json safely. Exits with error if parsing fails."""
    if not os.path.exists(CHAT_MODELS_PATH):
        return []
    try:
        with open(CHAT_MODELS_PATH, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
            if not isinstance(data, list):
                print(
                    f"[ERROR] Existing {CHAT_MODELS_PATH} root is not a JSON list. Aborting to prevent data corruption.",
                    file=sys.stderr,
                )
                sys.exit(1)
            return data
    except Exception as e:
        print(
            f"[ERROR] Failed to parse existing {CHAT_MODELS_PATH}: {e}. Aborting to prevent silent clobber.",
            file=sys.stderr,
        )
        sys.exit(1)


def upsert_uncenai_group(existing_groups, new_group):
    """Replaces or appends the uncenai group while preserving all other groups."""
    updated = []
    replaced = False

    for g in existing_groups:
        if isinstance(g, dict) and g.get("name") == UNCENAI_GROUP_NAME:
            updated.append(new_group)
            replaced = True
        else:
            updated.append(g)

    if not replaced:
        updated.append(new_group)

    return updated


def install_extension(dry_run=False):
    """Copies the VS Code status bar extension into the user's extensions folder."""
    if not os.path.exists(EXTENSION_SRC):
        print(f"[WARN] Extension source folder {EXTENSION_SRC} not found. Skipping extension file copy.")
        return

    if dry_run:
        print(f"[DRY-RUN] Would copy extension from {EXTENSION_SRC} -> {EXTENSION_DEST}")
        if os.path.exists(OLD_EXTENSION_DEST):
            print(f"[DRY-RUN] Would remove legacy extension directory: {OLD_EXTENSION_DEST}")
        return

    # Clean up legacy folder if present
    if os.path.exists(OLD_EXTENSION_DEST):
        try:
            shutil.rmtree(OLD_EXTENSION_DEST)
            print(f"[CLEANUP] Removed legacy extension folder: {OLD_EXTENSION_DEST}")
        except Exception as e:
            print(f"[WARN] Could not remove legacy extension folder {OLD_EXTENSION_DEST}: {e}")

    os.makedirs(EXTENSION_DEST, exist_ok=True)
    for item in os.listdir(EXTENSION_SRC):
        s = os.path.join(EXTENSION_SRC, item)
        d = os.path.join(EXTENSION_DEST, item)
        if os.path.isdir(s):
            if os.path.exists(d):
                shutil.rmtree(d)
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)
    print(f"[APPLY] Installed UncenAI extension to: {EXTENSION_DEST}")


def main():
    parser = argparse.ArgumentParser(description="Configure VS Code for UncenAI (Copilot Agent-Mode & Extension)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Print planned changes without writing to disk")
    group.add_argument("--apply", action="store_true", help="Apply configurations and install extension")
    parser.add_argument("--models-only", action="store_true", help="Register models in chatLanguageModels.json without copying extension folder")

    args = parser.parse_args()

    if not os.path.exists(CONFIG_PATH):
        print(f"[ERROR] Config file not found at {CONFIG_PATH}", file=sys.stderr)
        sys.exit(1)

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    existing_groups = load_chat_language_models()
    uncenai_group = build_uncenai_group(config)
    updated_groups = upsert_uncenai_group(existing_groups, uncenai_group)

    if args.dry_run:
        print("=== DRY-RUN: chatLanguageModels.json Target Content ===")
        print(f"Target File: {CHAT_MODELS_PATH}")
        print(json.dumps(updated_groups, indent=2))
        if not args.models_only:
            print("\n=== DRY-RUN: Extension Installation Target ===")
            install_extension(dry_run=True)
        print("\n[SUCCESS] Dry-run completed. No files modified.")
        return

    # Apply mode
    os.makedirs(STATE_DIR, exist_ok=True)
    if os.path.exists(CHAT_MODELS_PATH):
        shutil.copy2(CHAT_MODELS_PATH, BACKUP_PATH)
        print(f"[BACKUP] Existing chatLanguageModels.json backed up to {BACKUP_PATH}")

    os.makedirs(VSCODE_USER_DIR, exist_ok=True)
    tmp_path = CHAT_MODELS_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(updated_groups, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, CHAT_MODELS_PATH)
    print(f"[APPLY] Atomically wrote updated models to: {CHAT_MODELS_PATH}")

    if not args.models_only:
        install_extension(dry_run=False)

    print("[SUCCESS] UncenAI VS Code configuration successfully completed!")


if __name__ == "__main__":
    main()
