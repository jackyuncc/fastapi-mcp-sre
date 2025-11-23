const chatLog = document.getElementById('chat-log');
const chatInput = document.getElementById('chat-input');
const chatForm = document.getElementById('chat-form');
const incidentsEl = document.getElementById('incidents');
const servicePill = document.getElementById('service-pill');

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
  appendBubble('bot', 'Thinking…');
  try {
    const res = await fetch('/api/chat-llm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message })
    });
    if (!res.ok) {
      const errorText = await res.text();
      throw new Error(errorText || 'Request failed');
    }
    const data = await res.json();
    chatLog.removeChild(chatLog.lastChild); // remove placeholder
    appendBubble('bot', data.reply);
    renderMetrics(data.grafana);
    renderIncidents(data.incidents);
  } catch (err) {
    chatLog.removeChild(chatLog.lastChild);
    appendBubble('bot', 'Failed to reach backend. Check server logs.');
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

chatForm.addEventListener('submit', sendMessage);
document.getElementById('refresh-incidents').addEventListener('click', loadIncidents);

fetchServices();
loadIncidents();
appendBubble('bot', 'Hi! Ask me about a service (orders, payments) and I will pull Grafana + incident data.');
