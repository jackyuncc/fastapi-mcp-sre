const chatLog = document.getElementById('chat-log');
const chatInput = document.getElementById('chat-input');
const chatForm = document.getElementById('chat-form');
const incidentsEl = document.getElementById('incidents');
const servicePill = document.getElementById('service-pill');
const mcpForm = document.getElementById('mcp-form');
const mcpName = document.getElementById('mcp-name');
const mcpType = document.getElementById('mcp-type');
const mcpUrl = document.getElementById('mcp-url');
const mcpTimeout = document.getElementById('mcp-timeout');
const mcpStatus = document.getElementById('mcp-status');
const mcpList = document.getElementById('mcp-list');
const mcpPanel = document.querySelector('.mcp-panel');
const mcpToggle = document.getElementById('toggle-mcp');
const sessionInput = document.getElementById('session-id');
const sessionLabel = document.getElementById('session-label');
const newSessionBtn = document.getElementById('new-session');

function uuid() {
  return crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2);
}

function ensureSession() {
  let sid = localStorage.getItem('chat_session_id');
  if (!sid) {
    sid = uuid();
    localStorage.setItem('chat_session_id', sid);
  }
  sessionInput.value = sid;
  if (sessionLabel) sessionLabel.textContent = `Session: ${sid.slice(0, 8)}`;
}

async function fetchServices() {
  try {
    const res = await fetch('/api/services');
    const data = await res.json();
    servicePill.textContent = data.length ? `Services: ${data.join(', ')}` : 'No services configured';
  } catch (err) {
    servicePill.textContent = 'Failed to load services';
  }
}

function appendBubble(role, text) {
  const div = document.createElement('div');
  div.className = `bubble ${role}`;
  div.textContent = text;
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
}

async function sendMessage(evt) {
  evt.preventDefault();
  const message = chatInput.value.trim();
  if (!message) return;
  appendBubble('user', message);
  chatInput.value = '';
  chatInput.disabled = true;
  const sendButton = chatForm.querySelector('button[type="submit"]');
  if (sendButton) sendButton.disabled = true;
  appendBubble('bot', 'Thinking…');
  try {
    const res = await fetch('/api/web/chat-llm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, session_id: sessionInput.value })
    });
    if (!res.ok) {
      const errorText = await res.text();
      throw new Error(errorText || 'Request failed');
    }
    const data = await res.json();
    chatLog.removeChild(chatLog.lastChild); // remove placeholder
    if (data.called_tools && data.called_tools.length) {
      appendBubble('bot', `Using tools: ${data.called_tools.join(', ')}`);
    }
    appendBubble('bot', data.reply);
    if (data.session_id) {
      localStorage.setItem('chat_session_id', data.session_id);
      ensureSession();
    }
    renderMetrics(data.grafana);
    renderIncidents(data.incidents);
  } catch (err) {
    chatLog.removeChild(chatLog.lastChild);
    appendBubble('bot', 'Failed to reach backend. Check server logs.');
  } finally {
    chatInput.disabled = false;
    if (sendButton) sendButton.disabled = false;
    chatInput.focus();
  }
}

function renderMetrics(snapshot) {
  if (!snapshot) return;
  const card = document.createElement('div');
  card.className = 'metric-card';
  card.innerHTML = `
    <strong>${snapshot.service}</strong><br/>
    p99: ${snapshot.p99_latency_ms} ms<br/>
    errors: ${(snapshot.error_rate * 100).toFixed(2)}%<br/>
    CPU: ${(snapshot.cpu_utilization * 100).toFixed(1)}%<br/>
    traffic: ${snapshot.traffic_rps} rps<br/>
    <a href="${snapshot.panel_url}" target="_blank">Grafana panel</a>
  `;
  const wrap = document.createElement('div');
  wrap.className = 'metrics';
  wrap.appendChild(card);
  chatLog.appendChild(wrap);
}

async function loadIncidents() {
  try {
    const res = await fetch('/api/incidents');
    const data = await res.json();
    renderIncidents(data);
  } catch (err) {
    incidentsEl.textContent = 'Failed to load incidents';
  }
}

function renderIncidents(list) {
  incidentsEl.innerHTML = '';
  if (!list || !list.length) {
    incidentsEl.textContent = 'No active incidents.';
    return;
  }
  list.forEach((inc) => {
    const div = document.createElement('div');
    div.className = 'incident';
    div.innerHTML = `
      <h3>${inc.id} · ${inc.service}</h3>
      <p>${inc.summary}</p>
      <div class="badges">
        <span class="badge ${inc.severity}">${inc.severity}</span>
        <span class="badge">${inc.status}</span>
      </div>
      <p>Runbook: ${inc.runbook}</p>
      <button data-id="${inc.id}" class="ghost">Acknowledge</button>
    `;
    const btn = div.querySelector('button');
    btn.addEventListener('click', () => ackIncident(inc.id));
    incidentsEl.appendChild(div);
  });
}

async function ackIncident(incidentId) {
  const btn = document.querySelector(`button[data-id="${incidentId}"]`);
  if (btn) btn.textContent = 'Acking…';
  try {
    await fetch('/api/incidents/ack', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ incident_id: incidentId })
    });
    await loadIncidents();
  } catch (err) {
    if (btn) btn.textContent = 'Failed, retry';
  }
}

async function loadMcpSettings() {
  if (!mcpForm) return;
  try {
    const res = await fetch('/api/web/mcp');
    const data = await res.json();
    mcpTimeout.value = data.timeout_seconds ?? '';
    mcpStatus.textContent = 'Loaded';
    await loadMcpRegistry();
  } catch (err) {
    mcpStatus.textContent = 'Failed to load';
  }
}

async function loadMcpRegistry() {
  if (!mcpList) return;
  try {
    const res = await fetch('/api/web/listmcp');
    const data = await res.json();
    renderMcpList(data.registry);
    mcpStatus.textContent = 'Loaded';
  } catch (err) {
    mcpStatus.textContent = 'Failed to load';
  }
}

async function saveMcpSettings(evt) {
  if (!mcpForm) return;
  evt.preventDefault();
  mcpStatus.textContent = 'Saving…';
  try {
    const payload = {
      timeout_seconds: mcpTimeout.value ? Number(mcpTimeout.value) : undefined,
      enable_grafana: true,
      enable_oncall: true,
      enable_k8s: true,
    };
    const resSettings = await fetch('/api/web/mcp', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!resSettings.ok) throw new Error('Save failed');

    // Add registry entry
    const entryPayload = {
      name: mcpName.value || `${mcpType.value}-entry`,
      type: mcpType.value,
      url: mcpUrl.value,
      enabled: true,
    };
    if (!entryPayload.url) throw new Error('URL required');
    const resEntry = await fetch('/api/web/mcp/registry', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(entryPayload),
    });
    if (!resEntry.ok) throw new Error('Add entry failed');

    await loadMcpRegistry();
    mcpStatus.textContent = 'Saved & added entry';
  } catch (err) {
    mcpStatus.textContent = 'Save failed';
  }
}

async function listMcpSettings() {
  if (!mcpForm) return;
  try {
    const res = await fetch('/api/web/listmcp');
    const data = await res.json();
    appendBubble('bot', 'MCP settings:\n' + JSON.stringify(data, null, 2));
  } catch (err) {
    appendBubble('bot', 'Failed to list MCP settings');
  }
}

chatForm.addEventListener('submit', sendMessage);
document.getElementById('refresh-incidents').addEventListener('click', loadIncidents);
if (mcpForm) {
  mcpForm.addEventListener('submit', saveMcpSettings);
  document.getElementById('load-mcp').addEventListener('click', (e) => { e.preventDefault(); loadMcpSettings(); });
  document.getElementById('list-mcp').addEventListener('click', (e) => { e.preventDefault(); listMcpSettings(); });
  if (mcpToggle && mcpPanel) {
    mcpToggle.addEventListener('click', (e) => {
      e.preventDefault();
      mcpPanel.classList.toggle('collapsed');
      mcpToggle.textContent = mcpPanel.classList.contains('collapsed') ? 'Expand' : 'Collapse';
    });
  }
}
if (newSessionBtn) {
  newSessionBtn.addEventListener('click', (e) => {
    e.preventDefault();
    localStorage.removeItem('chat_session_id');
    ensureSession();
    appendBubble('bot', 'Started a new session.');
  });
}

fetchServices();
loadIncidents();
loadMcpSettings();
ensureSession();
appendBubble('bot', 'Hi! Ask me about a service (orders, payments) and I will pull Grafana + incident data.');
function renderMcpList(registry) {
  if (!mcpList) return;
  mcpList.innerHTML = '';
  const { entries = [], active = {}, reachable = {} } = registry || {};
  if (!entries.length) {
    mcpList.textContent = 'No MCP servers registered.';
    return;
  }
  const grouped = { grafana: [], oncall: [], k8s: [] };
  entries.forEach((e) => {
    if (!grouped[e.type]) grouped[e.type] = [];
    grouped[e.type].push(e);
  });
  Object.keys(grouped).forEach((type) => {
    const typeEntries = grouped[type];
    if (!typeEntries.length) return;
    typeEntries.forEach((e) => {
      const div = document.createElement('div');
      div.className = 'mcp-entry';
      const isActive = active && active[type] === e.id;
      div.innerHTML = `
        <div class="header">
          <span><strong>${e.name}</strong> <span class="pill-sm">${e.type}</span> <span class="pill-sm pill-dot"><span class="dot ${reachable[e.id] === false ? 'down' : reachable[e.id] === true ? 'up' : ''}"></span>${reachable[e.id] === false ? 'Down' : 'Unknown'}</span></span>
          <span class="meta">${isActive ? 'Active' : ''}</span>
        </div>
        <div class="meta">${e.url}</div>
        <div class="actions">
          <button data-type="${type}" data-id="${e.id}" class="ghost">${isActive ? 'Active' : 'Set active'}</button>
          <button data-id="${e.id}" class="ghost danger">Delete</button>
        </div>
      `;
      const btn = div.querySelector('button');
      btn.addEventListener('click', () => setActiveMcp(type, e.id));
      const delBtn = div.querySelector('.danger');
      delBtn.addEventListener('click', () => deleteMcp(e.id));
      mcpList.appendChild(div);
    });
  });
}

async function deleteMcp(id) {
  try {
    await fetch(`/api/web/mcp/registry/${id}`, { method: 'DELETE' });
    await loadMcpRegistry();
    mcpStatus.textContent = 'Deleted';
  } catch (err) {
    mcpStatus.textContent = 'Delete failed';
  }
}

async function setActiveMcp(type, id) {
  try {
    await fetch('/api/web/mcp/registry/select', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type, entry_id: id }),
    });
    await loadMcpRegistry();
    mcpStatus.textContent = `Set ${type} active`;
  } catch (err) {
    mcpStatus.textContent = 'Set active failed';
  }
}
