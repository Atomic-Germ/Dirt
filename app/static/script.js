const messagesContainer = document.getElementById('messages');
const userInput = document.getElementById('user-input');
const sendBtn = document.getElementById('send-btn');
const modelSelect = document.getElementById('model-select');
const clearBtn = document.getElementById('clear-btn');
const statusText = document.getElementById('status-text');
const rememberCheck = document.getElementById('remember-check');

let isTyping = false;

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
    contentDiv.textContent = content;
    
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
                remember: remember
            })
        });

        if (!response.ok) throw new Error('Failed to get response');

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        const assistantContentDiv = addMessage('assistant', '');
        statusText.textContent = 'Weaving...';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            
            const chunk = decoder.decode(value, { stream: true });
            assistantContentDiv.textContent += chunk;
            
            // Scroll to bottom as content grows
            const main = document.querySelector('main');
            main.scrollTop = main.scrollHeight;
        }

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
