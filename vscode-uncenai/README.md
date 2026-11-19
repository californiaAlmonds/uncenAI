# UncenAI — 1-Click Free T4 GPU Engine for VS Code Copilot Agent-Mode

**UncenAI** turns free cloud T4 GPU compute (**Google Colab**, **Lightning.ai Studio**, and **Modal Serverless**) into a unified, uncensored, abliterated local coding engine with **full agent tool calling** (`find-replace`, `multi-find-replace`), thinking modes, and 1-click status bar controls directly inside VS Code and Copilot Chat.

Enjoy **~130+ Free T4 GPU Hours per Month** with **zero local VRAM required**.

---

## ⚡ The 3-Provider Free T4 GPU Stack

| Provider | Free Monthly Quota | Setup & Auth | Auto-Stop | Status Icon |
| :--- | :--- | :--- | :--- | :---: |
| **Google Colab Free** | ~3–4 hours / day (~90–120h/mo) | `colab.exe` (No credit card) | 20 minutes | 🔥 |
| **Lightning.ai Studio** | 22 hours / month (15 free credits) | `lightning login` (No credit card) | 20 minutes | ⚡ |
| **Modal Serverless** | $30 / month free credit (~50h/mo) | `modal setup` | 3 minutes | ☁️ |

---

## 🤖 Pre-Configured Abliterated Coding Models

All models run quantized GGUF on 15 GB cloud T4 VRAM with full native agent mode tools:

1. **Qwen 2.5 Coder 14B Instruct Abliterated** (24k context) — Premier coding reasoning.
2. **Josiefied Qwen3 14B v3 Abliterated** (24k context, Thinking) — Deep multi-turn agent logic.
3. **Qwen 3.5 9B Abliterated Fast 32k** (32k context, Thinking) — Ultra-fast long-context editor.
4. **Gemma 4 12B Heretic Abliterated** (24k context, Thinking) — Uncensored general reasoning.
5. **Qwen 3.6 35B-A3B MoE Abliterated** (12k context, Thinking) — Massive MoE capacity.
6. **DeepSeek R1 Qwen 14B Abliterated v2** (24k context, Thinking) — Step-by-step reasoning specialist.

---

## 🚀 3-Step Quick Start

### 1. Prerequisites (Install Cloud CLI Tools)
Install the free cloud CLI tools using `uv` or `pip`:
```bash
# Using uv (recommended)
uv tool install google-colab-cli
uv tool install lightning-sdk
uv tool install modal

# Or using pip
pip install google-colab-cli lightning-sdk modal
```

### 2. Authenticate Your Accounts
Open the VS Code command palette (`Ctrl+Shift+P` / `Cmd+Shift+P`) and run:
`UncenAI: Login to Cloud GPU Providers`
*(or run `py src/local_controller.py auth --backend colab` in your terminal)*

### 3. Register Models in Copilot Chat
Open the command palette and run:
`UncenAI: Register Models in Copilot Chat (Agent Mode)`
Then click **Reload Window** when prompted.

---

## 🎮 Status Bar Controls & Agent Mode Usage

Look at the bottom-left status bar in VS Code:
- **`⊘ UncenAI [Colab T4]: OFF`**: Click to open the quickpick menu and boot the GPU.
- **`🔥 UncenAI [Colab T4]: Qwen 2.5 Coder 14B (12m/180m)`**: GPU online! Hover to view quota breakdown across Colab, Lightning.ai, and Modal.

### Using in Copilot Chat
1. Open GitHub Copilot Chat (`Ctrl+Alt+I` / `Cmd+Alt+I`).
2. Switch to **Agent Mode** (`@agent`).
3. Select any **`UncenAI: ...`** model in the model picker.
4. Prompt Copilot to write code, edit workspace files, or debug complex issues.

---

## 🛡️ Quota Protection Guarantee

- **Watchdog Auto-Stop:** Remote runtimes auto-terminate after 20 minutes of idle time (3 minutes for Modal scale-to-zero).
- **Hard Daily / Monthly Caps:** Proactively prevents exhausting free quotas.
- **Safe Offline Mocking:** If Copilot sends a request while offline, a polite notification appears with a 1-click boot prompt.

---

## 📄 License
MIT License. Built with ❤️ for the open-source and local AI community.
