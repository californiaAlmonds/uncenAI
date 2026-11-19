const vscode = require('vscode');
const http = require('http');
const cp = require('child_process');
const fs = require('fs');
const path = require('path');

let statusBarItem;
let pollTimer = null;
let currentGpuState = 'off';
let extensionContext = null;

/**
 * Detects appropriate Python binary name portably across OS.
 */
function findPythonCmd() {
    if (process.platform === 'win32') {
        return 'py';
    }
    return 'python3';
}

/**
 * Resolves backend directory and state directory with 3-tier fallback:
 * 1) Explicit global setting override `uncenai.projectRoot`
 * 2) Bundled extension backend (`<extensionDir>/backend`)
 * 3) Repo parent checkout (`<extensionDir>/..`)
 */
function resolveBackendPaths(context = extensionContext) {
    const inspected = vscode.workspace.getConfiguration('uncenai').inspect('projectRoot');
    const userOverride = (inspected?.globalValue || '').trim();

    if (userOverride) {
        const script = path.join(userOverride, 'src', 'local_controller.py');
        if (fs.existsSync(script)) {
            const stateDir = path.join(userOverride, '.state');
            try { fs.mkdirSync(stateDir, { recursive: true }); } catch {}
            return {
                root: userOverride,
                stateDir,
                scriptPath: script,
                configPath: path.join(userOverride, 'config', 'models.json'),
                configureScript: path.join(userOverride, 'scripts', 'configure_vscode.py')
            };
        }
    }

    // Check bundled backend (Marketplace .vsix installation)
    const bundledRoot = path.join(__dirname, 'backend');
    const bundledScript = path.join(bundledRoot, 'src', 'local_controller.py');
    if (fs.existsSync(bundledScript)) {
        const stateDir = context?.globalStorageUri?.fsPath || path.join(bundledRoot, '.state');
        try { fs.mkdirSync(stateDir, { recursive: true }); } catch {}
        return {
            root: bundledRoot,
            stateDir,
            scriptPath: bundledScript,
            configPath: path.join(bundledRoot, 'config', 'models.json'),
            configureScript: path.join(bundledRoot, 'scripts', 'configure_vscode.py')
        };
    }

    // Check parent repo checkout
    const repoRoot = path.resolve(__dirname, '..');
    const repoScript = path.join(repoRoot, 'src', 'local_controller.py');
    if (fs.existsSync(repoScript)) {
        const stateDir = path.join(repoRoot, '.state');
        try { fs.mkdirSync(stateDir, { recursive: true }); } catch {}
        return {
            root: repoRoot,
            stateDir,
            scriptPath: repoScript,
            configPath: path.join(repoRoot, 'config', 'models.json'),
            configureScript: path.join(repoRoot, 'scripts', 'configure_vscode.py')
        };
    }

    // Default fallback
    const fallbackRoot = path.resolve(__dirname, '..');
    const fallbackState = path.join(fallbackRoot, '.state');
    return {
        root: fallbackRoot,
        stateDir: fallbackState,
        scriptPath: path.join(fallbackRoot, 'src', 'local_controller.py'),
        configPath: path.join(fallbackRoot, 'config', 'models.json'),
        configureScript: path.join(fallbackRoot, 'scripts', 'configure_vscode.py')
    };
}

/**
 * Child process environment helper.
 */
function getChildEnv(context = extensionContext) {
    const paths = resolveBackendPaths(context);
    const env = {
        ...process.env,
        PYTHONUTF8: '1',
        UNCENAI_STATE_DIR: paths.stateDir
    };

    if (process.platform === 'win32' && fs.existsSync('E:\\uv')) {
        env.TEMP = 'E:\\tmp';
        env.TMP = 'E:\\tmp';
    }

    return env;
}

function getControllerUrl() {
    const inspected = vscode.workspace.getConfiguration('uncenai').inspect('controllerUrl');
    const raw = inspected?.globalValue || inspected?.defaultValue || 'http://127.0.0.1:11480';
    try {
        const url = new URL(raw);
        if (url.protocol === 'http:' && (url.hostname === '127.0.0.1' || url.hostname === 'localhost')) {
            return raw;
        }
    } catch {
        // Fallback below
    }
    return 'http://127.0.0.1:11480';
}

/**
 * Spawns the local daemon silently in background.
 */
function ensureDaemon(context = extensionContext) {
    const paths = resolveBackendPaths(context);
    if (!fs.existsSync(paths.scriptPath)) return;

    const pyCmd = findPythonCmd();
    const env = getChildEnv(context);

    try {
        const proc = cp.spawn(pyCmd, [paths.scriptPath, 'ensure-daemon'], {
            cwd: paths.root,
            detached: true,
            stdio: 'ignore',
            windowsHide: true,
            env
        });
        proc.unref();
    } catch {
        // Fallback to python
        try {
            const proc = cp.spawn('python', [paths.scriptPath, 'ensure-daemon'], {
                cwd: paths.root,
                detached: true,
                stdio: 'ignore',
                windowsHide: true,
                env
            });
            proc.unref();
        } catch {
            // Ignore error
        }
    }
}

/**
 * Simple HTTP GET JSON helper with timeout.
 */
function fetchStatus() {
    return new Promise((resolve) => {
        const urlObj = new URL('/uncenai/status', getControllerUrl());
        const req = http.get(urlObj.toString(), { timeout: 2500 }, (res) => {
            if (res.statusCode !== 200) {
                res.resume();
                return resolve(null);
            }
            let data = '';
            res.setEncoding('utf8');
            res.on('data', (chunk) => { data += chunk; });
            res.on('end', () => {
                try {
                    resolve(JSON.parse(data));
                } catch {
                    resolve(null);
                }
            });
        });
        req.on('error', () => resolve(null));
        req.on('timeout', () => {
            req.destroy();
            resolve(null);
        });
    });
}

/**
 * Simple HTTP POST JSON helper.
 */
function postJson(apiPath, body = {}) {
    return new Promise((resolve, reject) => {
        const urlObj = new URL(apiPath, getControllerUrl());
        const payload = Buffer.from(JSON.stringify(body), 'utf8');
        const req = http.request(urlObj.toString(), {
            method: 'POST',
            timeout: 10000,
            headers: {
                'Content-Type': 'application/json; charset=utf-8',
                'Content-Length': payload.length
            }
        }, (res) => {
            let data = '';
            res.setEncoding('utf8');
            res.on('data', (chunk) => { data += chunk; });
            res.on('end', () => {
                try {
                    const parsed = data ? JSON.parse(data) : {};
                    resolve({ statusCode: res.statusCode, data: parsed });
                } catch (e) {
                    resolve({ statusCode: res.statusCode, data });
                }
            });
        });
        req.on('error', (err) => reject(err));
        req.on('timeout', () => {
            req.destroy();
            reject(new Error('Request timed out'));
        });
        req.write(payload);
        req.end();
    });
}

/**
 * Reads models.json config file.
 */
function loadModelsConfig(context = extensionContext) {
    const paths = resolveBackendPaths(context);
    try {
        if (fs.existsSync(paths.configPath)) {
            const raw = fs.readFileSync(paths.configPath, 'utf8');
            return JSON.parse(raw);
        }
    } catch (e) {
        console.error('Failed to read models.json', e);
    }
    return { colabModels: [], backends: {}, proxy: {} };
}

/**
 * Backend UI helper.
 */
function getBackendMeta(backendId) {
    if (backendId === 'lightning') {
        return { id: 'lightning', icon: '$(zap)', shortLabel: 'Lightning T4', label: 'Lightning.ai Free T4 (~22h/mo)' };
    }
    if (backendId === 'modal') {
        return { id: 'modal', icon: '$(cloud)', shortLabel: 'Modal T4', label: 'Modal Serverless T4 ($30/mo Free)' };
    }
    return { id: 'colab', icon: '$(flame)', shortLabel: 'Colab T4', label: 'Google Colab Free T4 (Daily Quota)' };
}

/**
 * Builds compact 3-provider quota summary for tooltips.
 */
function buildQuotaTooltipLines(status) {
    const lines = [];
    const backends = status?.backends || {};

    const colabInfo = backends.colab || {};
    const colabUsed = Math.round(colabInfo.usedMinutes || status?.quota?.usedMinutesToday || 0);
    const colabSoft = colabInfo.softLimitMinutes || status?.quota?.softLimitMinutes || 180;
    lines.push(`- **Colab T4 (Daily):** ${colabUsed}m / ${colabSoft}m`);

    const ltInfo = backends.lightning || {};
    const ltUsed = Math.round(ltInfo.usedMinutes || 0);
    const ltSoft = ltInfo.softLimitMinutes || 1200;
    lines.push(`- **Lightning T4 (Monthly):** ${ltUsed}m / ${ltSoft}m (~22h/mo)`);

    const modalInfo = backends.modal || {};
    const modalUsed = Math.round(modalInfo.usedMinutes || 0);
    const modalSoft = modalInfo.softLimitMinutes || 2700;
    lines.push(`- **Modal T4 (Monthly):** ${modalUsed}m / ${modalSoft}m ($30/mo)`);

    return lines.join('\n');
}

/**
 * Updates status bar visual state from status response.
 */
function updateStatusBar(status) {
    if (!statusBarItem) return;

    if (!status || !status.daemonRunning) {
        currentGpuState = 'off';
        statusBarItem.text = '$(circle-slash) UncenAI [Colab T4]: OFF';
        statusBarItem.tooltip = new vscode.MarkdownString(
            '### UncenAI Cloud T4 GPU: OFF\n\n' +
            'Preserving your free GPU quota across Colab, Lightning.ai & Modal.\n\n' +
            '**Click to open GPU menu & boot in 1 click**',
            true
        );
        statusBarItem.backgroundColor = undefined;
        statusBarItem.show();
        return;
    }

    const state = status.gpuState || 'off';
    currentGpuState = state;
    const activeBackend = status.activeBackend || 'colab';
    const bMeta = getBackendMeta(activeBackend);
    const shortBackend = status.activeBackendShort || status.backends?.[activeBackend]?.shortLabel || bMeta.shortLabel;

    const q = status.quota || {};
    const used = q.usedMinutes !== undefined ? Math.round(q.usedMinutes) : (q.usedMinutesToday !== undefined ? Math.round(q.usedMinutesToday) : 0);
    const soft = q.softLimitMinutes || 180;
    const idle = q.idleMinutes !== undefined ? Math.round(q.idleMinutes) : 0;
    const idleLimit = q.idleAutoStopMinutes || 20;

    const activeModel = status.activeModel || status.targetModel || 'Default Model';
    const shortModel = activeModel.length > 20 ? activeModel.substring(0, 18) + '…' : activeModel;
    const quotaPeriodLabel = q.quotaPeriod === 'monthly' ? 'this month' : 'today';

    if (state === 'ready') {
        statusBarItem.text = `${bMeta.icon} UncenAI [${shortBackend}]: ${shortModel} (${used}m/${soft}m)`;
        const tip = new vscode.MarkdownString(undefined, true);
        tip.supportThemeIcons = true;
        tip.appendMarkdown(`### $(check) UncenAI [${shortBackend}]: ONLINE & READY\n\n`);
        tip.appendMarkdown(`- **Active Backend:** **${bMeta.label}**\n`);
        tip.appendMarkdown(`- **Active Model:** \`${activeModel}\`\n`);
        tip.appendMarkdown(`- **Backend Quota Used:** **${used}m** / ${soft}m (${quotaPeriodLabel})\n`);
        tip.appendMarkdown(`- **Idle Auto-Stop:** **${idle}m** / ${idleLimit}m\n\n`);
        tip.appendMarkdown(`#### 🌐 Multi-Provider Quota Overview:\n`);
        tip.appendMarkdown(`${buildQuotaTooltipLines(status)}\n\n`);
        tip.appendMarkdown(`---\n*Click status bar to open GPU & Quota Menu*`);
        statusBarItem.tooltip = tip;
        statusBarItem.backgroundColor = undefined;
    } else if (state === 'starting') {
        const detail = status.stageDetail || 'Starting T4 runtime...';
        statusBarItem.text = `$(sync~spin) UncenAI [${shortBackend}]: ${detail}`;
        const pct = status.downloadPercent ? ` (${status.downloadPercent}%)` : '';
        statusBarItem.tooltip = new vscode.MarkdownString(
            `### $(sync~spin) UncenAI [${shortBackend}]: STARTING\n\n` +
            `- **Backend:** **${bMeta.label}**\n` +
            `- **Stage:** ${detail}${pct}\n` +
            `- **Target Model:** \`${status.targetModel || 'Default'}\`\n\n` +
            `*Booting remote T4 environment & initializing model weights...*`,
            true
        );
        statusBarItem.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
    } else if (state === 'error') {
        statusBarItem.text = `$(warning) UncenAI [${shortBackend}]: Error`;
        statusBarItem.tooltip = new vscode.MarkdownString(
            `### $(error) UncenAI GPU Error [${shortBackend}]\n\n` +
            `**Details:** ${status.lastError || 'Unknown error occurred'}\n\n` +
            `*Click to retry or switch backend.*`,
            true
        );
        statusBarItem.backgroundColor = new vscode.ThemeColor('statusBarItem.errorBackground');
    } else {
        // Off / Stopped
        statusBarItem.text = `$(circle-slash) UncenAI [${shortBackend}]: OFF (${used}m/${soft}m)`;
        statusBarItem.tooltip = new vscode.MarkdownString(
            `### $(circle-slash) UncenAI [${shortBackend}]: OFF\n\n` +
            `- **Selected Backend:** **${bMeta.label}**\n` +
            `- **Quota Used:** **${used}m** / ${soft}m (${quotaPeriodLabel})\n\n` +
            `#### 🌐 Multi-Provider Quota Overview:\n` +
            `${buildQuotaTooltipLines(status)}\n\n` +
            `**Click to boot T4 GPU in 1 click**`,
            true
        );
        statusBarItem.backgroundColor = undefined;
    }
    statusBarItem.show();
}

/**
 * Initiates model boot on specified backend with progress tracker.
 */
async function startGpuWithProgress(modelId = null, backendId = null, ignoreQuota = false) {
    const config = loadModelsConfig();
    const finalBackend = backendId || config?.proxy?.defaultBackend || 'colab';
    const finalModel = modelId || config?.proxy?.defaultModel || 'qwen2.5-coder-14b-abliterated';
    const bMeta = getBackendMeta(finalBackend);

    try {
        ensureDaemon();
        await postJson('/uncenai/start', { model: finalModel, backend: finalBackend, ignoreQuota });
    } catch (err) {
        vscode.window.showErrorMessage(`Failed to send start request to UncenAI controller: ${err.message}`);
        return;
    }

    vscode.window.withProgress({
        location: vscode.ProgressLocation.Notification,
        title: `UncenAI [${bMeta.shortLabel}]: ${finalModel}`,
        cancellable: false
    }, async (progress) => {
        let lastStage = '';
        const startTime = Date.now();
        const timeoutMs = 360000; // 6 minutes max

        while (Date.now() - startTime < timeoutMs) {
            await new Promise((r) => setTimeout(r, 1500));
            const status = await fetchStatus();
            if (!status) continue;

            updateStatusBar(status);

            const state = status.gpuState;
            const stage = status.stageDetail || 'Provisioning...';
            const pct = status.downloadPercent ? ` (${status.downloadPercent}%)` : '';

            if (stage !== lastStage) {
                progress.report({ message: `${stage}${pct}` });
                lastStage = stage;
            }

            if (state === 'ready') {
                vscode.window.showInformationMessage(
                    `⚡ UncenAI T4 Ready on ${bMeta.label}! Select your model in Copilot Chat (Agent Mode).`
                );
                return;
            }

            if (state === 'error') {
                vscode.window.showErrorMessage(
                    `UncenAI [${bMeta.shortLabel}] failed to boot: ${status.lastError || 'Unknown error'}`
                );
                return;
            }
        }

        vscode.window.showWarningMessage(`UncenAI [${bMeta.shortLabel}] boot timed out. Check daemon logs.`);
    });
}

/**
 * Registers models in VS Code Copilot Chat via configure_vscode.py.
 */
async function configureCopilotChat(context = extensionContext) {
    const paths = resolveBackendPaths(context);
    if (!fs.existsSync(paths.configureScript)) {
        vscode.window.showErrorMessage(`configure_vscode.py script not found at ${paths.configureScript}`);
        return;
    }

    const pyCmd = findPythonCmd();
    const env = getChildEnv(context);

    vscode.window.withProgress({
        location: vscode.ProgressLocation.Notification,
        title: "Registering UncenAI Models in VS Code Copilot Chat...",
        cancellable: false
    }, () => {
        return new Promise((resolve) => {
            cp.execFile(pyCmd, [paths.configureScript, '--apply', '--models-only'], {
                cwd: paths.root,
                env
            }, (error, stdout, stderr) => {
                if (error) {
                    vscode.window.showErrorMessage(`Failed to register Copilot models: ${stderr || error.message}`);
                } else {
                    vscode.window.showInformationMessage(
                        "UncenAI models successfully registered in VS Code Copilot Chat!",
                        "Reload Window"
                    ).then((selection) => {
                        if (selection === "Reload Window") {
                            vscode.commands.executeCommand('workbench.action.reloadWindow');
                        }
                    });
                }
                resolve();
            });
        });
    });
}

/**
 * QuickPick interactive menu.
 */
async function showUncenAiMenu() {
    const config = loadModelsConfig();
    const status = await fetchStatus();
    updateStatusBar(status);

    const gpuState = status?.gpuState || 'off';
    const activeBackend = status?.activeBackend || config?.proxy?.defaultBackend || 'colab';
    const bMeta = getBackendMeta(activeBackend);
    const shortBackend = status?.activeBackendShort || bMeta.shortLabel;

    const backends = status?.backends || {};
    const colabUsed = Math.round(backends.colab?.usedMinutes || status?.quota?.usedMinutesToday || 0);
    const colabSoft = backends.colab?.softLimitMinutes || 180;

    const ltUsed = Math.round(backends.lightning?.usedMinutes || 0);
    const ltSoft = backends.lightning?.softLimitMinutes || 1200;

    const modalUsed = Math.round(backends.modal?.usedMinutes || 0);
    const modalSoft = backends.modal?.softLimitMinutes || 2700;

    const defaultModelId = config?.proxy?.defaultModel || 'qwen2.5-coder-14b-abliterated';

    const items = [];

    if (gpuState === 'ready' || gpuState === 'starting') {
        items.push({
            label: `$(stop) Stop Active GPU (${shortBackend}) — Save Quota`,
            description: `Currently ${gpuState.toUpperCase()} — click to immediately stop remote session`,
            action: 'stop'
        });
        items.push({
            label: `$(sync) Hot-Swap Active Model on ${shortBackend}...`,
            description: `Switch remote LLM on ${shortBackend} (Active: ${status?.activeModel || 'None'})`,
            action: 'select-model'
        });
        items.push({
            label: `$(arrow-swap) Switch T4 Backend (Colab / Lightning.ai / Modal)...`,
            description: `Switch active provider from ${shortBackend} to another T4 runtime`,
            action: 'switch-backend'
        });
    } else {
        items.push({
            label: `$(flame) Start Google Colab Free T4`,
            description: `${colabUsed}m / ${colabSoft}m today — Default Model (${defaultModelId})`,
            action: 'start-colab'
        });
        items.push({
            label: `$(zap) Start Lightning.ai Free T4`,
            description: `${ltUsed}m / ${ltSoft}m this month — ~22h/mo Free, No Card`,
            action: 'start-lightning'
        });
        items.push({
            label: `$(cloud) Start / Deploy Modal Serverless T4`,
            description: `${modalUsed}m / ${modalSoft}m this month — $30/mo Free (~50h/mo), Auto-Sleep`,
            action: 'start-modal'
        });
        items.push({
            label: `$(layers) Start Specific Model & Backend...`,
            description: 'Pick provider first, then choose from 6 abliterated coding models',
            action: 'start-custom'
        });
    }

    items.push({
        label: `$(sparkle) Register UncenAI Models in Copilot Chat`,
        description: 'Configure VS Code Copilot Chat (Agent Mode) to load UncenAI models',
        action: 'configure-copilot'
    });

    items.push({
        label: `$(graph) Multi-Provider Quota Summary (Colab + Lightning + Modal)`,
        description: `Colab: ${colabUsed}m/${colabSoft}m | Lightning: ${ltUsed}m/${ltSoft}m | Modal: ${modalUsed}m/${modalSoft}m`,
        action: 'quota-summary'
    });

    items.push({
        label: `$(key) Provider Logins (Colab / Lightning.ai / Modal)...`,
        description: 'Authenticate CLI credentials for Google Colab, Lightning.ai, or Modal',
        action: 'auth-menu'
    });

    items.push({
        label: `$(file-code) Open Models Configuration (models.json)`,
        description: 'View and customize models, backends & quota limits',
        action: 'open-models'
    });

    const picked = await vscode.window.showQuickPick(items, {
        placeHolder: `UncenAI GPU [${shortBackend}: ${gpuState.toUpperCase()}] — Select an action`
    });

    if (!picked) return;

    if (picked.action === 'start-colab') {
        await startGpuWithProgress(defaultModelId, 'colab');
    } else if (picked.action === 'start-lightning') {
        await startGpuWithProgress(defaultModelId, 'lightning');
    } else if (picked.action === 'start-modal') {
        await startGpuWithProgress(defaultModelId, 'modal');
    } else if (picked.action === 'switch-backend') {
        const backendChoices = [
            { label: '$(flame) Google Colab Free T4', description: `${colabUsed}m / ${colabSoft}m daily`, backendId: 'colab' },
            { label: '$(zap) Lightning.ai Free T4', description: `${ltUsed}m / ${ltSoft}m monthly (~22h/mo)`, backendId: 'lightning' },
            { label: '$(cloud) Modal Serverless T4', description: `${modalUsed}m / ${modalSoft}m monthly ($30/mo free)`, backendId: 'modal' }
        ];
        const chosen = await vscode.window.showQuickPick(backendChoices, {
            placeHolder: `Select new T4 backend provider (Current: ${shortBackend})`
        });
        if (chosen) {
            await startGpuWithProgress(status?.activeModel || defaultModelId, chosen.backendId);
        }
    } else if (picked.action === 'select-model') {
        const models = config.colabModels || [];
        const modelItems = models.map((m) => ({
            label: `$(symbol-event) ${m.name}`,
            description: `${m.sizeGB} GB GGUF | ${m.contextWindow.toLocaleString()} ctx | ${m.thinking ? 'Thinking' : 'Direct'}`,
            detail: m.id,
            modelId: m.id
        }));

        const pickedModel = await vscode.window.showQuickPick(modelItems, {
            placeHolder: `Select abliterated model to boot / hot-swap on ${shortBackend}`
        });

        if (pickedModel) {
            await startGpuWithProgress(pickedModel.modelId, activeBackend);
        }
    } else if (picked.action === 'start-custom') {
        const backendChoices = [
            { label: '$(flame) Google Colab Free T4', description: `${colabUsed}m / ${colabSoft}m today`, backendId: 'colab' },
            { label: '$(zap) Lightning.ai Free T4', description: `${ltUsed}m / ${ltSoft}m this month (~22h/mo)`, backendId: 'lightning' },
            { label: '$(cloud) Modal Serverless T4', description: `${modalUsed}m / ${modalSoft}m this month ($30/mo free)`, backendId: 'modal' }
        ];
        const chosenBackend = await vscode.window.showQuickPick(backendChoices, {
            placeHolder: 'Step 1 of 2: Select T4 Cloud Provider'
        });
        if (!chosenBackend) return;

        const models = config.colabModels || [];
        const modelItems = models.map((m) => ({
            label: `$(symbol-event) ${m.name}`,
            description: `${m.sizeGB} GB GGUF | ${m.contextWindow.toLocaleString()} ctx | ${m.thinking ? 'Thinking' : 'Direct'}`,
            detail: m.id,
            modelId: m.id
        }));

        const chosenModel = await vscode.window.showQuickPick(modelItems, {
            placeHolder: `Step 2 of 2: Select Model for ${chosenBackend.label}`
        });

        if (chosenModel) {
            await startGpuWithProgress(chosenModel.modelId, chosenBackend.backendId);
        }
    } else if (picked.action === 'configure-copilot') {
        await configureCopilotChat();
    } else if (picked.action === 'stop') {
        try {
            await postJson('/uncenai/stop');
            vscode.window.showInformationMessage(`UncenAI GPU (${shortBackend}) stopped. Quota preserved.`);
            const newStatus = await fetchStatus();
            updateStatusBar(newStatus);
        } catch (e) {
            vscode.window.showErrorMessage(`Failed to stop GPU: ${e.message}`);
        }
    } else if (picked.action === 'quota-summary') {
        const stats = await fetchStatus();
        if (!stats) {
            vscode.window.showWarningMessage('UncenAI Controller daemon is not responding.');
            return;
        }
        const b = stats.backends || {};
        const msg = `UncenAI Multi-Provider T4 Quota Summary:\n\n` +
            `• Current State: ${stats.gpuState.toUpperCase()} [${stats.activeBackendShort || 'Colab T4'}]\n` +
            `• Active Model: ${stats.activeModel || 'None'}\n\n` +
            `1. Google Colab (Daily Quota):\n` +
            `   Used: ${Math.round(b.colab?.usedMinutes || stats.quota?.usedMinutesToday || 0)}m / ${b.colab?.softLimitMinutes || 180}m (Hard limit: 240m)\n\n` +
            `2. Lightning.ai Studio (Monthly Quota):\n` +
            `   Used: ${Math.round(b.lightning?.usedMinutes || 0)}m / ${b.lightning?.softLimitMinutes || 1200}m (~22h/month)\n\n` +
            `3. Modal Serverless (Monthly Quota):\n` +
            `   Used: ${Math.round(b.modal?.usedMinutes || 0)}m / ${b.modal?.softLimitMinutes || 2700}m ($30/month = ~50h/month)\n\n` +
            `Combined Free GPU Pool: ~130+ Free T4 Hours / Month!`;
        vscode.window.showInformationMessage(msg);
    } else if (picked.action === 'auth-menu') {
        const authChoices = [
            { label: '$(flame) Google Colab CLI', description: 'Run colab auth & session check', backendId: 'colab' },
            { label: '$(zap) Lightning.ai CLI', description: 'Run lightning login in terminal', backendId: 'lightning' },
            { label: '$(cloud) Modal Serverless CLI', description: 'Run modal setup token authentication', backendId: 'modal' }
        ];
        const chosenAuth = await vscode.window.showQuickPick(authChoices, {
            placeHolder: 'Select cloud provider CLI to authenticate'
        });
        if (chosenAuth) {
            runAuthTerminal(chosenAuth.backendId);
        }
    } else if (picked.action === 'open-models') {
        const paths = resolveBackendPaths();
        if (fs.existsSync(paths.configPath)) {
            const doc = await vscode.workspace.openTextDocument(paths.configPath);
            await vscode.window.showTextDocument(doc);
        }
    }
}

/**
 * Runs auth terminal securely via shellPath and shellArgs (no shell injection via sendText).
 */
function runAuthTerminal(backendId = 'colab', context = extensionContext) {
    const paths = resolveBackendPaths(context);
    const pyCmd = findPythonCmd();
    const env = getChildEnv(context);

    const terminal = vscode.window.createTerminal({
        name: `UncenAI Auth (${backendId})`,
        cwd: paths.root,
        shellPath: pyCmd,
        shellArgs: [paths.scriptPath, 'auth', '--backend', backendId],
        env
    });
    terminal.show();
}

/**
 * Extension activation.
 */
function activate(context) {
    extensionContext = context;

    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    statusBarItem.command = 'uncenai.menu';
    statusBarItem.text = '$(circle-slash) UncenAI [Colab T4]: OFF';
    statusBarItem.tooltip = 'UncenAI Cloud T4 GPU: Click to open menu';
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);

    // Register commands
    context.subscriptions.push(
        vscode.commands.registerCommand('uncenai.menu', () => showUncenAiMenu()),
        vscode.commands.registerCommand('uncenai.start', async () => {
            const config = loadModelsConfig(context);
            const defaultModelId = config?.proxy?.defaultModel || 'qwen2.5-coder-14b-abliterated';
            const defaultBackend = config?.proxy?.defaultBackend || 'colab';
            await startGpuWithProgress(defaultModelId, defaultBackend);
        }),
        vscode.commands.registerCommand('uncenai.stop', async () => {
            try {
                await postJson('/uncenai/stop');
                vscode.window.showInformationMessage('UncenAI GPU stopped. Quota preserved.');
                const st = await fetchStatus();
                updateStatusBar(st);
            } catch (e) {
                vscode.window.showErrorMessage(`Failed to stop GPU: ${e.message}`);
            }
        }),
        vscode.commands.registerCommand('uncenai.auth', async () => {
            const authChoices = [
                { label: '$(flame) Google Colab CLI', description: 'Run colab auth & session check', backendId: 'colab' },
                { label: '$(zap) Lightning.ai CLI', description: 'Run lightning login in terminal', backendId: 'lightning' },
                { label: '$(cloud) Modal Serverless CLI', description: 'Run modal setup token authentication', backendId: 'modal' }
            ];
            const chosenAuth = await vscode.window.showQuickPick(authChoices, {
                placeHolder: 'Select cloud provider CLI to authenticate'
            });
            if (chosenAuth) {
                runAuthTerminal(chosenAuth.backendId, context);
            }
        }),
        vscode.commands.registerCommand('uncenai.configureCopilot', () => configureCopilotChat(context))
    );

    // Initial daemon ensure
    ensureDaemon(context);

    // Start background polling
    const poll = async () => {
        try {
            const status = await fetchStatus();
            updateStatusBar(status);
        } catch {
            updateStatusBar(null);
        }
    };

    poll();
    pollTimer = setInterval(poll, 4000);
    context.subscriptions.push({
        dispose: () => {
            if (pollTimer) clearInterval(pollTimer);
        }
    });
}

function deactivate() {
    if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
}

module.exports = {
    activate,
    deactivate
};
