const messagesContainer = document.getElementById('messages');
const userInput = document.getElementById('user-input');
const sendBtn = document.getElementById('send-btn');
const modelSelect = document.getElementById('model-select');
const clearBtn = document.getElementById('clear-btn');
const statusText = document.getElementById('status-text');
const rememberCheck = document.getElementById('remember-check');
const streamToggle = document.getElementById('stream-toggle');

let isTyping = false;

function createTagState() {
    return {
        plain: '',
        segments: [], // {name, content, inTag, expanded, userToggled, sawClose}
        current: null,
        buffer: ''
    };
}

function processTagChunk(state, chunk, flush = false) {
    const data = state.buffer + chunk;
    let idx = 0;

    while (true) {
        const nextOpen = data.indexOf('<', idx);
        if (nextOpen === -1) break;
        const nextClose = data.indexOf('>', nextOpen + 1);
        if (nextClose === -1) break; // wait for more data

        const tokenRaw = data.slice(nextOpen, nextClose + 1);
        const openMatch = tokenRaw.match(/^<([a-zA-Z0-9_-]+)>$/);
        const closeMatch = tokenRaw.match(/^<\/([a-zA-Z0-9_-]+)>$/);

        const text = data.slice(idx, nextOpen);
        if (state.current) {
            state.current.content += text;
        } else {
            state.plain += text;
        }

        if (openMatch) {
            const name = openMatch[1];
            const seg = { name, content: '', inTag: true, expanded: true, userToggled: false, sawClose: false };
            state.segments.push(seg);
            state.current = seg;
        } else if (closeMatch) {
            const name = closeMatch[1];
            if (state.current && state.current.name === name) {
                state.current.inTag = false;
                state.current.sawClose = true;
                if (!state.current.userToggled) {
                    state.current.expanded = false;
                }
                state.current = null;
            }
        }

        idx = nextClose + 1;
    }

    const remaining = data.slice(idx);

    if (flush) {
        if (state.current) {
            state.current.content += remaining;
            if (!state.current.userToggled) {
                state.current.expanded = false;
            }
            state.current.inTag = false;
            state.current.sawClose = true;
            state.current = null;
        } else {
            state.plain += remaining;
        }
        state.buffer = '';
        return state;
    }

    // keep larger tail for partial tags to handle longer tag names
    const tailKeep = 64; // Increased from 16 to handle longer tag names
    const safeLen = Math.max(remaining.length - tailKeep, 0);
    const consumable = remaining.slice(0, safeLen);
    const leftover = remaining.slice(safeLen);
    if (consumable) {
        if (state.current) {
            state.current.content += consumable;
        } else {
            state.plain += consumable;
        }
    }
    state.buffer = leftover;
    return state;
}

function renderTags(contentDiv, state) {
    contentDiv.innerHTML = '';

    const mainBlock = document.createElement('div');
    mainBlock.className = 'main-text';
    mainBlock.textContent = state.plain;
    contentDiv.appendChild(mainBlock);

    state.segments.forEach((seg) => {
        const block = document.createElement('div');
        block.className = 'think-block';
        const forceExpanded = seg.inTag;
        const isExpanded = forceExpanded || seg.expanded;
        if (isExpanded) block.classList.add('expanded');

        const label = document.createElement('div');
        label.className = 'think-label';
        label.textContent = seg.name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

        const body = document.createElement('div');
        body.className = 'think-body';
        const trailing = seg.inTag ? ' …' : '';
        body.textContent = seg.content + trailing;

        const toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'think-toggle';
        toggle.textContent = isExpanded ? 'Hide' : 'Expand';
        toggle.disabled = forceExpanded;
        toggle.onclick = () => {
            seg.userToggled = true;
            seg.expanded = !isExpanded;
            renderTags(contentDiv, state);
        };

        block.appendChild(label);
        block.appendChild(body);
        block.appendChild(toggle);
        contentDiv.appendChild(block);
    });
}

// Auto-resize textarea
userInput.addEventListener('input', () => {
    userInput.style.height = 'auto';
    userInput.style.height = userInput.scrollHeight + 'px';
});

// Load models
async function loadModels() {
    try {
        const response = await fetch('/models');
        const models = await response.json();
        modelSelect.innerHTML = '';
        models.forEach(model => {
            const option = document.createElement('option');
            option.value = model;
            option.textContent = model;
            modelSelect.appendChild(option);
        });
        
        // Set default model if available
        if (models.includes('kimi-k2-thinking:cloud')) {
            modelSelect.value = 'kimi-k2-thinking:cloud';
        } else if (models.length > 0) {
            modelSelect.value = models[0];
        }
    } catch (error) {
        console.error('Error loading models:', error);
        statusText.textContent = 'Error loading models';
    }
}

// Load history
async function loadHistory() {
    try {
        const response = await fetch('/history');
        const history = await response.json();
        if (history.length > 0) {
            messagesContainer.innerHTML = '';
            history.forEach(msg => addMessage(msg.role, msg.content));
        }
    } catch (error) {
        console.error('Error loading history:', error);
    }
}

function addMessage(role, content = '') {
    const msgDiv = document.createElement('div');
    msgDiv.className = `message ${role}`;
    
    const contentDiv = document.createElement('div');
    contentDiv.className = 'content';
    if (role === 'assistant') {
        const state = createTagState();
        processTagChunk(state, content, true);
        renderTags(contentDiv, state);
    } else {
        contentDiv.textContent = content;
    }
    
    msgDiv.appendChild(contentDiv);

    if (role === 'assistant') {
        const seedBtn = document.createElement('button');
        seedBtn.className = 'seed-btn';
        seedBtn.textContent = 'Seed Heritage';
        seedBtn.onclick = () => seedContent(contentDiv.textContent, seedBtn);
        msgDiv.appendChild(seedBtn);
    }
    
    messagesContainer.appendChild(msgDiv);
    
    // Scroll to bottom
    const main = document.querySelector('main');
    main.scrollTop = main.scrollHeight;

    return contentDiv; // Return the content div so we can update it for streaming
}

async function seedContent(content, btn) {
    try {
        const response = await fetch('/seed', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content, tags: ['user-seeded'] })
        });
        if (response.ok) {
            btn.textContent = 'Seeded';
            btn.disabled = true;
            btn.style.opacity = '1';
            btn.style.color = '#4ade80';
        }
    } catch (error) {
        console.error('Seed error:', error);
    }
}

async function sendMessage() {
    const text = userInput.value.trim();
    if (!text || isTyping) return;

    const model = modelSelect.value;
    const remember = rememberCheck.checked;
    const streamOnly = streamToggle.checked;

    // Add user message to UI
    addMessage('user', text);
    userInput.value = '';
    userInput.style.height = 'auto';
    
    isTyping = true;
    statusText.textContent = 'Thinking...';
    sendBtn.disabled = true;

    try {
        const response = await fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                model: model,
                messages: [{ role: 'user', content: text }],
                remember: remember,
                stream_only: streamOnly
            })
        });

        if (!response.ok) throw new Error('Failed to get response');

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        const assistantContentDiv = addMessage('assistant', '');
        const tagState = createTagState();
        statusText.textContent = streamOnly ? 'Streaming...' : 'Weaving...';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            
            const chunk = decoder.decode(value, { stream: true });
            processTagChunk(tagState, chunk, false);
            renderTags(assistantContentDiv, tagState);
            
            // Scroll to bottom as content grows
            const main = document.querySelector('main');
            main.scrollTop = main.scrollHeight;
        }

        // Flush any buffered partial tokens
        processTagChunk(tagState, '', true);
        renderTags(assistantContentDiv, tagState);

        statusText.textContent = 'Ready';
    } catch (error) {
        console.error('Error:', error);
        statusText.textContent = 'Error: ' + error.message;
        addMessage('assistant', 'Sorry, I encountered an error while trying to connect to the Bridge.');
    } finally {
        isTyping = false;
        sendBtn.disabled = false;
    }
}

sendBtn.addEventListener('click', sendMessage);

userInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

clearBtn.addEventListener('click', async () => {
    if (confirm('Clear all memories from the Bridge?')) {
        await fetch('/clear', { method: 'POST' });
        messagesContainer.innerHTML = '<div class="message assistant"><div class="content">Memories cleared. The Bridge is silent.</div></div>';
    }
});

// Initialize
loadModels();
loadHistory();