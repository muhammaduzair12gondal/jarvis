const API_URL = "http://localhost:5000";

// DOM Elements
const micBtn = document.getElementById('micBtn');
const commandInput = document.getElementById('commandInput');
const sendBtn = document.getElementById('sendBtn');
const chatLog = document.getElementById('chatLog');
const liveSpeechContainer = document.getElementById('liveSpeechContainer');
const liveSpeechText = document.getElementById('liveSpeechText');
const arcReactor = document.getElementById('arcReactor');
const statusDisplay = document.getElementById('statusDisplay');
const visualizerContainer = document.querySelector('.visualizer-container');
const memoryMeta = document.getElementById('memoryMeta');
const memoryList = document.getElementById('memoryList');
const memoryRefreshBtn = document.getElementById('memoryRefreshBtn');
const memoryClearBtn = document.getElementById('memoryClearBtn');

// System metrics elements
const cpuBar = document.getElementById('cpuBar');
const ramBar = document.getElementById('ramBar');
const batBar = document.getElementById('batBar');
const cpuValue = document.getElementById('cpuValue');
const ramValue = document.getElementById('ramValue');
const batValue = document.getElementById('batValue');

// Modal Elements
const confirmationModal = document.getElementById('confirmationModal');
const dangerousCommandPreview = document.getElementById('dangerousCommandPreview');
const btnCancel = document.getElementById('btnCancel');
const btnExecute = document.getElementById('btnExecute');

// State
let isListening = false;
let isWorking = false;
let pendingCommand = null;

// Speech Recognition Setup (Web Speech API)
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null;

if (SpeechRecognition) {
    recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = 'en-US';

    recognition.onstart = () => {
        isListening = true;
        updateStatusUI('LISTENING...', 'state-listening');
        micBtn.classList.add('active');
    };

    recognition.onresult = (event) => {
        let interimTranscript = '';
        let finalTranscript = '';

        for (let i = event.resultIndex; i < event.results.length; ++i) {
            if (event.results[i].isFinal) {
                finalTranscript += event.results[i][0].transcript;
            } else {
                interimTranscript += event.results[i][0].transcript;
            }
        }

        if (interimTranscript) {
            liveSpeechContainer.classList.add('active');
            liveSpeechText.textContent = interimTranscript;
        }

        if (finalTranscript) {
            liveSpeechContainer.classList.remove('active');
            commandInput.value = finalTranscript.trim();
            handleUserSubmit(finalTranscript.trim());
        }
    };

    recognition.onerror = (event) => {
        console.error("Speech Recognition Error:", event.error);
        stopListening();
    };

    recognition.onend = () => {
        // If we didn't intentionally stop and we're not working, restart it
        if (isListening && !isWorking) {
            try { recognition.start(); } catch (e) { }
        } else {
            isListening = false;
            micBtn.classList.remove('active');
            if (!isWorking) updateStatusUI('AWAITING COMMAND', '');
        }
    };
} else {
    appendMessage('system', 'Web Speech API is not supported in this browser. Voice commands disabled.');
    micBtn.disabled = true;
}

// ─── UI Helpers ─────────────────────────────────────────────────────────

function updateStatusUI(text, stateClass) {
    statusDisplay.textContent = text;
    visualizerContainer.className = 'visualizer-container ' + stateClass;

    // Update top header status indicator
    const indicator = document.querySelector('.status-indicator');
    indicator.className = 'status-indicator';
    if (stateClass === 'state-working') indicator.classList.add('working');
    else indicator.classList.add('online');
}

function appendMessage(sender, text, commandObj = null, meta = null) {
    const msgDiv = document.createElement('div');
    msgDiv.className = `message ${sender}`;

    const icon = sender === 'user' ? 'fa-user' : 'fa-robot';

    let html = `
        <div class="avatar"><i class="fas ${icon}"></i></div>
        <div class="bubble">
            <div class="text">${escapeHTML(text)}</div>
    `;

    if (commandObj && commandObj.output) {
        html += `<div class="command-result ${commandObj.success ? '' : 'error'}">
            &gt; ${escapeHTML(commandObj.cmd)}\n${escapeHTML(commandObj.output)}
        </div>`;
    }

    // Show provider + latency badge on system messages (Currently hidden by user request)
    // if (meta && sender === 'system') { ... }

    html += `</div>`;
    msgDiv.innerHTML = html;

    chatLog.appendChild(msgDiv);
    chatLog.scrollTop = chatLog.scrollHeight;
}

function escapeHTML(str) {
    return (str || '').replace(/[&<>'"]/g,
        tag => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            "'": '&#39;',
            '"': '&quot;'
        }[tag])
    );
}

function setMemoryMetaText(text) {
    if (memoryMeta) memoryMeta.textContent = text;
}

function renderMemoryList(memories) {
    if (!memoryList) return;
    memoryList.innerHTML = '';

    if (!memories || memories.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'memory-item';
        empty.innerHTML = '<span class="fact">No stored memories yet.</span>';
        memoryList.appendChild(empty);
        return;
    }

    memories.forEach((mem) => {
        const row = document.createElement('div');
        row.className = 'memory-item';

        const fact = document.createElement('span');
        fact.className = 'fact';
        fact.title = mem.fact || '';
        fact.textContent = mem.fact || 'Untitled memory';

        const pin = document.createElement('button');
        pin.className = 'mini-btn';
        pin.textContent = mem.pinned ? 'Unpin' : 'Pin';
        pin.addEventListener('click', async () => {
            try {
                pin.disabled = true;
                await fetchWithTimeout(`${API_URL}/memory/pin`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ id: mem.id, pinned: !mem.pinned })
                });
                await refreshMemoryPanel();
            } catch (e) {
                console.error('Pin toggle failed', e);
            } finally {
                pin.disabled = false;
            }
        });

        const pinStatus = document.createElement('span');
        pinStatus.className = 'pin-status';
        pinStatus.textContent = mem.pinned ? 'Pinned' : '';

        row.appendChild(fact);
        row.appendChild(pinStatus);
        row.appendChild(pin);
        memoryList.appendChild(row);
    });
}

async function refreshMemoryPanel() {
    if (!memoryMeta || !memoryList) return;
    try {
        const [statsRes, listRes] = await Promise.all([
            fetchWithTimeout(`${API_URL}/memory`),
            fetchWithTimeout(`${API_URL}/memory/list?limit=12`)
        ]);

        if (!statsRes.ok || !listRes.ok) throw new Error('Failed to load memory');

        const stats = await statsRes.json();
        const listData = await listRes.json();
        setMemoryMetaText(`Stored: ${stats.count ?? 0} | Pinned: ${stats.pinned_count ?? 0}`);
        renderMemoryList(listData.memories || []);
    } catch (err) {
        console.error('Memory refresh failed', err);
        setMemoryMetaText('Memory unavailable');
        renderMemoryList([]);
    }
}

async function clearMemoryPanelData() {
    if (!confirm('Clear all long-term memories?')) return;
    try {
        await fetchWithTimeout(`${API_URL}/memory/clear`, { method: 'POST' });
        await refreshMemoryPanel();
    } catch (err) {
        console.error('Memory clear failed', err);
    }
}

// ─── Fetch with Timeout ─────────────────────────────────────────────────

function fetchWithTimeout(url, options = {}, timeoutMs = 45000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    return fetch(url, { ...options, signal: controller.signal })
        .finally(() => clearTimeout(timer));
}

// ─── System Metrics Polling ─────────────────────────────────────────────

async function pollSystemMetrics() {
    try {
        const res = await fetchWithTimeout(`${API_URL}/system-info`, {}, 10000);
        if (!res.ok) return;
        const data = await res.json();

        if (data.cpu_percent != null) {
            cpuBar.style.width = data.cpu_percent + '%';
            cpuValue.textContent = Math.round(data.cpu_percent) + '%';
            cpuBar.className = 'bar-fill' + (data.cpu_percent > 80 ? ' bar-danger' : '');
        }

        if (data.ram_percent != null) {
            ramBar.style.width = data.ram_percent + '%';
            ramValue.textContent = Math.round(data.ram_percent) + '%';
            ramBar.className = 'bar-fill' + (data.ram_percent > 85 ? ' bar-danger' : '');
        }

        if (data.battery_percent != null) {
            batBar.style.width = data.battery_percent + '%';
            const chargingIcon = data.battery_charging ? '⚡' : '';
            batValue.textContent = Math.round(data.battery_percent) + '%' + chargingIcon;
            batBar.className = 'bar-fill battery-bar' +
                (data.battery_percent < 20 ? ' bar-danger' : '');
        }
    } catch (e) {
        // Silently fail — metrics are non-critical
    }
}

// ─── Voice Synthesis ────────────────────────────────────────────────────

function speakText(text, callback) {
    if (!('speechSynthesis' in window)) {
        if (callback) callback();
        return;
    }

    // Stop current speech if any
    window.speechSynthesis.cancel();

    // Apply phonetic corrections so the voice pronounces names correctly
    // "Uzair" -> "Sir" (user requested)
    const phoneticText = text.replace(/\bUzair\b/gi, "Sir");

    const utterance = new SpeechSynthesisUtterance(phoneticText);
    utterance.rate = 1.0;
    utterance.pitch = 0.9; // Slightly deeper for Jarvis

    // Try to find a good English voice
    const voices = window.speechSynthesis.getVoices();
    const goodVoice = voices.find(v => v.name.includes('Google UK English Male') || v.name.includes('Microsoft Mark') || v.lang === 'en-GB');
    if (goodVoice) utterance.voice = goodVoice;

    // Safety timeout: if speech synthesis hangs (known Chrome bug), call callback after 30s
    let done = false;
    const safetyTimer = setTimeout(() => {
        if (!done) {
            done = true;
            window.speechSynthesis.cancel();
            if (callback) callback();
        }
    }, 30000);

    utterance.onend = () => {
        if (!done) {
            done = true;
            clearTimeout(safetyTimer);
            if (callback) callback();
        }
    };

    utterance.onerror = (e) => {
        console.error("Speech Synthesis Error:", e);
        if (!done) {
            done = true;
            clearTimeout(safetyTimer);
            if (callback) callback();
        }
    };

    window.speechSynthesis.speak(utterance);
}

// Ensure voices are loaded
if (speechSynthesis.onvoiceschanged !== undefined) {
    speechSynthesis.onvoiceschanged = () => window.speechSynthesis.getVoices();
}


// ─── Core Logic ─────────────────────────────────────────────────────────

function toggleListening() {
    if (!recognition) return;

    if (isListening) {
        stopListening();
    } else {
        startListening();
    }
}

function startListening() {
    if (isWorking) return;
    try {
        recognition.start();
    } catch (e) { }
}

function stopListening() {
    isListening = false;
    liveSpeechContainer.classList.remove('active');
    try {
        recognition.stop();
    } catch (e) { }
}

async function handleUserSubmit(text) {
    if (!text) return;

    commandInput.value = '';
    liveSpeechContainer.classList.remove('active');
    appendMessage('user', text);

    // Pause listening while processing
    if (recognition) stopListening();

    isWorking = true;
    updateStatusUI('PROCESSING...', 'state-working');

    try {
        const response = await fetchWithTimeout(`${API_URL}/ask`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: text })
        }, 45000);

        if (!response.ok) throw new Error('Network response was not ok');

        const data = await response.json();

        // Handle dangerous command confirmation
        if (data.requires_confirmation) {
            pendingCommand = data.command;
            showModal(data.command);
            updateStatusUI('AUTHORIZATION REQUIRED', 'state-error');
            // Speak the response, don't restart listening until modal resolved
            speakText(data.response);
            appendMessage('system', data.response, null, { provider: data.provider, latency_ms: data.latency_ms });
            return;
        }

        // Output system response
        let cmdObj = null;
        if (data.command) {
            cmdObj = {
                cmd: data.command,
                output: data.command_output,
                success: data.command_success
            };
        }

        appendMessage('system', data.response, cmdObj, { provider: data.provider, latency_ms: data.latency_ms });

        // Keep memory panel fresh only when new memory likely learned.
        if ((data.learned_memories || 0) > 0) {
            refreshMemoryPanel();
        }

        // Speak response and resume listening
        speakText(data.response, () => {
            isWorking = false;
            updateStatusUI('AWAITING COMMAND', '');
            // Resume listening if it was active
            startListening();
        });

    } catch (error) {
        console.error("Backend Error:", error);
        const errMsg = error.name === 'AbortError'
            ? 'Request timed out. The backend may be busy — please try again.'
            : 'Error connecting to the backend server. Is it running?';
        appendMessage('system', errMsg);
        isWorking = false;
        updateStatusUI('ERROR OR OFFLINE', '');
    }
}


// ─── Modal Handling ─────────────────────────────────────────────────────

function showModal(commandText) {
    dangerousCommandPreview.textContent = commandText;
    confirmationModal.classList.add('active');
}

function hideModal() {
    pendingCommand = null;
    confirmationModal.classList.remove('active');
    isWorking = false;
    updateStatusUI('AWAITING COMMAND', '');
    startListening();
}

async function forceExecute() {
    if (!pendingCommand) return;
    const cmd = pendingCommand;
    confirmationModal.classList.remove('active');
    updateStatusUI('EXECUTING OVERRIDE...', 'state-working');

    try {
        const response = await fetchWithTimeout(`${API_URL}/execute`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command: cmd })
        });

        const data = await response.json();
        const cmdObj = { cmd: cmd, output: data.command_output, success: data.command_success };

        let msgText = data.command_success ? "Override executed successfully." : "Override pattern failed.";
        appendMessage('system', msgText, cmdObj);
        speakText(msgText, () => {
            isWorking = false;
            updateStatusUI('AWAITING COMMAND', '');
            startListening();
        });

    } catch (error) {
        appendMessage('system', 'Error executing command override.');
        isWorking = false;
        updateStatusUI('ERROR', '');
    }
}


// ─── Event Listeners ────────────────────────────────────────────────────

micBtn.addEventListener('click', toggleListening);

sendBtn.addEventListener('click', () => {
    handleUserSubmit(commandInput.value.trim());
});

commandInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') handleUserSubmit(commandInput.value.trim());
});

btnCancel.addEventListener('click', hideModal);
btnExecute.addEventListener('click', forceExecute);
memoryRefreshBtn?.addEventListener('click', refreshMemoryPanel);
memoryClearBtn?.addEventListener('click', clearMemoryPanelData);

// Initialize
setTimeout(() => {
    updateStatusUI('AWAITING COMMAND', '');
    refreshMemoryPanel();
    pollSystemMetrics();
    // Auto-start microphone
    startListening();
}, 1000);

// Poll system metrics every 10 seconds
setInterval(pollSystemMetrics, 10000);
