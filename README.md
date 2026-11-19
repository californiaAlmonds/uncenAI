# UncenAI: 1-Click Multi-Cloud Free T4 GPU Engine for VS Code Copilot Agent-Mode

**UncenAI** turns free cloud T4 GPU compute (**Google Colab**, **Lightning.ai Studio**, and **Modal Serverless**) into a unified, uncensored, abliterated local coding engine with **full agent tool calling** (`find-replace`, `multi-find-replace`), thinking modes, and 1-click status bar switching directly in VS Code and OpenCode.

Combined, the 3 free tiers give you **~130+ Free T4 GPU Hours per Month** with **zero local GPU VRAM requirements**.

---

## ⚡ The 3-Provider Free T4 GPU Stack

| Provider | Free Monthly Quota | Setup & Auth | Auto-Stop | Status Icon |
| :--- | :--- | :--- | :--- | :---: |
| **Google Colab Free** | ~3–4 hours / day (~90–120h/mo) | `colab.exe` (No credit card) | 20 minutes | 🔥 |
| **Lightning.ai Studio** | 22 hours / month (15 free credits) | `lightning login` (No credit card) | 20 minutes | ⚡ |
| **Modal Serverless** | $30 / month free credit (~50h/mo) | `modal setup` | 3 minutes | ☁️ |

---

## 🌟 Key Features

- **1-Click Multi-Cloud Status Bar Integration:** Boot, stop, hot-swap models, or switch between Colab, Lightning.ai, and Modal right from the VS Code status bar.
- **VS Code Copilot Agent-Mode Ready:** Native `customendpoint` provider support in VS Code 1.146+ with tool execution, code modifications, and agent loops.
- **Top Abliterated Coding Models:** Pre-configured with Qwen 2.5 Coder 14B, Josiefied Qwen3 14B v3, Qwen 3.5 9B (32k), Gemma 4 12B Heretic, Qwen 3.6 35B-A3B MoE, and DeepSeek R1 Qwen 14B v2.
- **Unified Multi-Provider Quota Safeguards:**
  - Independent daily/monthly quota tracking per backend.
  - Automatic idle shutdown (20m for Colab/Lightning, 3m for Modal).
  - Multi-provider quota dashboard in status bar tooltips and quickpick menu.
- **Zero-Latency Fallback & OpenCode Support:** Configured for OpenCode (`opencode.json`) with OpenRouter `:free` fallback routing when cloud GPUs are offline.

---

## 🚀 Quick Start Guide

### Step 1: Install Cloud CLI Tools
Install the required free cloud CLI tools using `uv` (recommended) or `pip`:
```bash
# Using uv (fastest)
uv tool install google-colab-cli lightning-sdk modal

# Or using pip
pip install google-colab-cli lightning-sdk modal
```

---

### Step 2: One-Time Provider Logins
Authenticate your free cloud provider accounts in terminal:
```bash
# 1. Google Colab Login
python src/local_controller.py auth --backend colab

# 2. Lightning.ai Login (Optional - 22h/mo free)
python src/local_controller.py auth --backend lightning

# 3. Modal Login (Optional - $30/mo free)
python src/local_controller.py auth --backend modal
```
*(Or use the VS Code tasks: `Terminal > Run Task... > Login: ...`)*

---

### Step 3: Configure VS Code Copilot & Install Extension
Register the models in VS Code and install the status bar extension:
```bash
python scripts/configure_vscode.py --apply
```
*(Or verify first via dry-run: `python scripts/configure_vscode.py --dry-run`)*

Restart or reload VS Code (`Developer: Reload Window`).

---

## 🎮 Using UncenAI in VS Code

### 1-Click Status Bar Button
Look at the bottom-left status bar in VS Code:
- **`⊘ UncenAI [Colab T4]: OFF (0m/180m)`**: GPU is offline. Click to open menu and boot in 1 click!
- **`🔄 UncenAI [Lightning T4]: Starting T4 runtime...`**: Remote instance is provisioning.
- **`⚡ UncenAI [Lightning T4]: Qwen 2.5 Coder 14B (45m/1200m)`**: Online and ready! Shows active backend, model, and runtime quota.
- **`☁️ UncenAI [Modal T4]: Qwen 3.5 9B (120m/2700m)`**: Serverless instance active with 3-minute auto-sleep.

### Interactive QuickPick Menu
Click the status bar item (or press `Ctrl+Shift+P` / `Cmd+Shift+P` -> `UncenAI: Open GPU & Quota Menu`):
1. **Start Google Colab Free T4** (Daily free quota).
2. **Start Lightning.ai Free T4** (Monthly free studio quota).
3. **Start / Deploy Modal Serverless T4** (Monthly $30 serverless budget).
4. **Switch T4 Backend / Hot-Swap Models** (Seamlessly pivot between backends or models).
5. **Register UncenAI Models in Copilot Chat** (1-click agent configuration).
6. **Multi-Provider Quota Summary** (Full breakdown of Colab, Lightning, and Modal usage).

### Copilot Chat Agent Mode
1. Open GitHub Copilot Chat in VS Code (`Ctrl+Alt+I` / `Cmd+Alt+I` or Chat panel).
2. Switch to **Agent Mode** (`@agent` / Agent tab).
3. In the model dropdown, select any **`UncenAI: ...`** model (e.g., `UncenAI: Qwen 2.5 Coder 14B Abliterated (T4 GPU)`).
4. Ask Copilot to refactor code, generate tests, or edit files across your workspace. Agent tool calling works automatically via `http://127.0.0.1:11480/v1/chat/completions`.

---

## 🧩 OpenCode Configuration

UncenAI includes an `opencode.json` configuration for OpenCode AI SDK:
```json
{
  "providers": {
    "uncenai": {
      "baseURL": "http://127.0.0.1:11480/v1"
    },
    "openrouter-free": {
      "baseURL": "https://openrouter.ai/api/v1"
    }
  }
}
```

---

## 📦 Building VSIX Package for Distribution
To build a self-contained `.vsix` extension bundle for installation or marketplace publishing:
```bash
python scripts/build_vsix.py --stage-only
# Or complete package build (requires vsce):
python scripts/build_vsix.py --publisher your-publisher --repo https://github.com/your-user/uncenAI
```

---

## 🛡️ Quota Safety Guarantee

- **Auto-Stop on Idle:** Automatically stops remote sessions when idle (20m for Colab & Lightning; 3m scale-down for Modal).
- **Hard Daily & Monthly Caps:** Safeguards prevent runaway usage or unexpected account lockouts.
- **Safe Offline Mocking:** When all cloud GPUs are stopped, requests receive an immediate notification with 1-click boot buttons.

---

## 📄 License
MIT License. Built with ❤️ for the open-source and local AI community.
