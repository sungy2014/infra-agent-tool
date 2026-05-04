const API = window.location.origin;
const API_KEY = localStorage.getItem('infra_agent_token') || '';
let eventSource = null;
let currentJobId = null;
let eventCount = 0;
let _detailsRendered = false;
let _elapsedTimer = null;
let _currentTab = 'generate';
let _allJobs = [];

async function api(path, opts = {}) {
  const headers = { 'Content-Type': 'application/json', ...opts.headers };
  if (API_KEY) headers['Authorization'] = 'Bearer ' + API_KEY;
  const res = await fetch(API + path, { headers, ...opts });
  if (res.status === 401) {
    localStorage.removeItem('infra_agent_token');
    localStorage.removeItem('infra_agent_user');
    window.location.href = '/login';
    return;
  }
  if (!res.ok) { const body = await res.text(); throw new Error(body); }
  return res.json();
}

async function checkServer() {
  try {
    await api('/health');
    document.getElementById('server-status').className = 'status-dot online';
  } catch {
    document.getElementById('server-status').className = 'status-dot offline';
  }
}

async function loadJobs() {
  try {
    const data = await api('/api/jobs');
    _allJobs = data.jobs || [];
    renderSidebar();
    renderHistory();
    updateStats();
  } catch { /* ignore */ }
}

function updateStats() {
  const jobs = _allJobs;
  const running = jobs.filter(j => j.status === 'running' || j.status === 'awaiting_input').length;
  const completed = jobs.filter(j => j.status === 'completed').length;
  const failed = jobs.filter(j => j.status === 'failed').length;
  const user = localStorage.getItem('infra_agent_user') || '';
  const logoutBtn = user ? `<span style="cursor:pointer;font-size:12px" onclick="logout()">🚪 logout</span>` : '';
  document.getElementById('sidebar-stats').innerHTML =
    `<div class="stat-row"><span>Running</span><span style="color:var(--accent)">${running}</span></div>
     <div class="stat-row"><span>Completed</span><span style="color:var(--success)">${completed}</span></div>
     <div class="stat-row"><span>Failed</span><span style="color:var(--danger)">${failed}</span></div>
     ${user ? `<div class="stat-row" style="margin-top:4px;border-top:1px solid var(--border);padding-top:4px"><span>${user}</span>${logoutBtn}</div>` : ''}`;
}

function renderSidebar() {
  const list = document.getElementById('job-list');
  const jobs = _allJobs.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || '')).slice(0, 20);
  if (!jobs.length) { list.innerHTML = '<div class="empty-state">No jobs yet</div>'; return; }
  list.innerHTML = jobs.map(j => {
      const label = j.pending_question ? '❓ ' + j.pending_question.split('\n')[0].slice(0, 40)
        : j.result?.response ? j.result.response.split('\n')[0].slice(0, 40) : j.job_id.slice(0, 8);
      const active = j.job_id === currentJobId ? 'active' : '';
      const elapsed = j.started_at && !j.completed_at ? ' · ' + timeAgo(j.started_at) : '';
      return `<div class="job-item ${active}" onclick="selectJob('${j.job_id}')">
        <div class="job-title">${escHtml(label)}</div>
        <div class="job-meta">
          <span class="badge ${j.status}">${j.status}</span>
          <span>${timeAgo(j.created_at)}${elapsed}</span>
        </div></div>`;
    }).join('');
}

function renderHistory() {
  const container = document.getElementById('history-table');
  const search = (document.getElementById('history-search').value || '').toLowerCase();
  const filter = document.getElementById('history-filter').value;
  let jobs = _allJobs.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
  if (filter) jobs = jobs.filter(j => j.status === filter);
  if (search) jobs = jobs.filter(j =>
    (j.job_id || '').toLowerCase().includes(search) ||
    (j.result?.response || '').toLowerCase().includes(search) ||
    (j.error || '').toLowerCase().includes(search)
  );
  if (!jobs.length) {
    container.innerHTML = '<div class="empty-table">No matching jobs</div>';
    return;
  }
  container.innerHTML =
    `<table class="history-table">
      <thead><tr><th>Status</th><th>Job ID</th><th>Summary</th><th>Created</th><th></th></tr></thead>
      <tbody>${jobs.map(j => {
        const summary = j.result?.response
          ? j.result.response.split('\n')[0].slice(0, 80)
          : j.pending_question ? j.pending_question.slice(0, 80)
          : j.error ? j.error.slice(0, 80) : '(running)';
        const active = j.job_id === currentJobId ? 'active' : '';
        return `<tr class="${active}" onclick="selectJob('${j.job_id}')">
          <td><span class="badge ${j.status}">${j.status}</span></td>
          <td class="td-id">${escHtml(j.job_id.slice(0, 8))}</td>
          <td>${escHtml(summary)}</td>
          <td style="color:var(--text-muted);white-space:nowrap">${timeAgo(j.created_at)}</td>
          <td class="td-action" onclick="event.stopPropagation();selectJob('${j.job_id}')">View ↗</td>
        </tr>`;
      }).join('')}</tbody></table>`;
}

function showTab(name) {
  _currentTab = name;
  document.querySelectorAll('#main-tabs .tab-btn').forEach(b => b.classList.remove('active'));
  if (name === 'generate') {
    document.querySelector('#main-tabs .tab-btn:nth-child(1)').classList.add('active');
    document.getElementById('tab-generate').classList.remove('hidden');
    document.getElementById('tab-history').classList.add('hidden');
  } else {
    document.querySelector('#main-tabs .tab-btn:nth-child(2)').classList.add('active');
    document.getElementById('tab-generate').classList.add('hidden');
    document.getElementById('tab-history').classList.remove('hidden');
    renderHistory();
  }
}

function selectJob(jobId) {
  if (eventSource) { eventSource.close(); eventSource = null; }
  _detailsRendered = false;
  eventCount = 0;
  currentJobId = jobId;
  // Find job in cached _allJobs for immediate status
  const cached = _allJobs.find(j => j.job_id === jobId);
  document.getElementById('form-view').classList.add('hidden');
  document.getElementById('result-view').classList.remove('hidden');
  document.getElementById('job-id-display').textContent = jobId.slice(0, 8);
  removeInputPrompt();
  document.getElementById('conversation-view').innerHTML =
    `<div class="conv-status"><span class="badge ${cached ? cached.status : 'queued'}">${cached ? cached.status : 'queued'}</span></div>`;

  const terminal = ['completed', 'failed', 'cancelled'];
  if (cached && terminal.includes(cached.status)) {
    document.getElementById('cancel-btn').classList.add('hidden');
    loadFullResult(jobId).then(() => {
      const el = document.getElementById('job-elapsed');
      if (el && cached.completed_at && cached.started_at) {
        const s = Math.floor((new Date(cached.completed_at) - new Date(cached.started_at)) / 1000);
        el.textContent = s > 60 ? Math.floor(s/60) + 'm ' + (s%60) + 's' : s + 's';
      }
    });
  } else {
    document.getElementById('conversation-view').insertAdjacentHTML('beforeend',
      `<div class="typing-indicator"><span></span><span></span><span></span><span class="typing-label">Starting...</span></div>
       <div class="conv-divider">Steps</div>`);
    document.getElementById('cancel-btn').classList.remove('hidden');
    startElapsedTimer(cached ? cached.started_at : null);
    connectEvents(jobId);
  }
}

function connectEvents(jobId) {
  if (eventSource) eventSource.close();
  const es = new EventSource(`${API}/api/jobs/${jobId}/events?index=${eventCount}`);
  eventSource = es;
  es.onmessage = (event) => { try { handleEvent(jobId, JSON.parse(event.data)); } catch { /* ignore */ } };
  es.onerror = () => {
    es.close(); eventSource = null;
    if (!currentJobId || currentJobId !== jobId) return;
    api(`/api/jobs/${jobId}`).then(job => {
      if (!job || ['completed', 'failed', 'cancelled'].includes(job.status)) {
        if (job && job.status === 'awaiting_input' && job.pending_question) showInputPrompt(job.pending_question);
        return;
      }
      if (currentJobId === jobId) connectEvents(jobId);
    }).catch(() => {});
  };
}

function updatePipeline(stepName, state) {
  const map = { 'clone': /clone/i, 'generate': /generat|terraform/i, 'publish': /publish|jenkins/i };
  let pipeKey = '';
  for (const [k, re] of Object.entries(map)) { if (re.test(stepName)) { pipeKey = k; break; } }
  if (!pipeKey) return;
  const node = document.querySelector(`[data-pipe="${pipeKey}"]`);
  if (!node) return;
  const dot = node.querySelector('.pipe-dot');
  node.classList.add('active');
  if (state === 'done' || state === 'running') {
    const prev = node.previousElementSibling;
    if (prev && prev.classList.contains('pipe-connector')) prev.classList.add(state === 'done' ? 'done' : 'active');
  }
  dot.className = 'pipe-dot ' + (state === 'running' ? 'running' : state === 'done' ? 'done' : state === 'error' ? 'fail' : '');
}

function handleEvent(jobId, ev) {
  eventCount++;
  const view = document.getElementById('conversation-view');
  if (eventCount === 1) hideTypingIndicator();
  if (ev.type === 'message') { appendMessage(ev.data, view); }
  else if (ev.type === 'step') { updatePipeline(ev.data.label, 'running'); appendStep(ev.data.label, 'running', view); }
  else if (ev.type === 'step_done') { updatePipeline(ev.data.label, 'done'); appendStep(ev.data.label, 'done', view); }
  else if (ev.type === 'step_error') { updatePipeline(ev.data.label, 'error'); appendStep(ev.data.label, 'error', view); }
  else if (ev.type === 'awaiting_input') { showInputPrompt(ev.data.question); }
  else if (ev.type === 'approval_required') { showApprovalPrompt(ev.data, jobId); }
  else if (ev.type === 'commit') {
    const d = ev.data || {};
    showCommitCard(d, view);
  } else if (ev.type === 'jenkins_build') {
    const d = ev.data || {};
    showJenkinsCard(d, view);
  } else if (ev.type === 'complete') {
    hideTypingIndicator(); document.getElementById('cancel-btn').classList.add('hidden');
    removeInputPrompt(); stopElapsedTimer(); loadJobs();
    setTimeout(() => loadFullResult(jobId), 500);
  }
}

function showCommitCard(d, view) {
  const link = d.url ? `<a href="${escHtml(d.url)}" target="_blank" style="color:var(--accent);text-decoration:none">${escHtml(d.hash)}</a>` : escHtml(d.hash || '');
  view.insertAdjacentHTML('beforeend',
    `<div class="conv-message conv-tool"><div class="conv-role">Git commit</div><div class="conv-body">📝 ${escHtml(d.message || '')}<br>🔗 ${link} on <strong>${escHtml(d.branch || '')}</strong></div></div>`);
  view.scrollTop = view.scrollHeight;
}

function showJenkinsCard(d, view) {
  const icon = d.result === 'SUCCESS' ? '✅' : '❌';
  const consoleText = d.console ? `<details class="thinking-block"><summary>📋 console output</summary><pre style="font-size:11px;white-space:pre-wrap;margin-top:4px">${escHtml(d.console)}</pre></details>` : '';
  const statusColor = d.result === 'SUCCESS' ? 'var(--success)' : 'var(--danger)';
  const linkHtml = d.url ? ` <a href="${escHtml(d.url)}" target="_blank" style="color:var(--accent);font-size:12px">open ↗</a>` : '';
  view.insertAdjacentHTML('beforeend',
    `<div class="conv-message conv-tool" style="border-color:${statusColor}"><div class="conv-role">Jenkins build</div><div class="conv-body">${icon} Build #${escHtml(d.build_number || '?')}: <strong>${escHtml(d.result || 'UNKNOWN')}</strong>${linkHtml}</div>${consoleText}</div>`);
  view.scrollTop = view.scrollHeight;
}

function appendMessage(data, view) {
  const role = data.role || 'unknown';
  const msg = escHtml(data.content || '');
  let extra = '';
  const long = msg.length > 300 && role !== 'tool';
  const body = long
    ? `<details class="msg-collapse"><summary>${msg.slice(0, 300)}...</summary><div class="msg-full">${msg}</div></details>`
    : `<div class="conv-body${role === 'assistant' ? ' conv-assistant-body' : ''}">${msg || '(empty)'}</div>`;
  if (data.reasoning) extra += `<details class="thinking-block"><summary>💭 thinking</summary><div>${escHtml(data.reasoning)}</div></details>`;
  if (data.tool_calls) {
    data.tool_calls.forEach(t => {
      if (t.name === 'ask_user') {
        let q = t.args || '';
        try { q = JSON.parse(q).question || q; } catch { /* keep as-is */ }
        extra += `<div class="conv-answer">❓ ${escHtml(q)}</div>`;
      } else extra += `<div class="conv-toolcall">🔧 ${escHtml(t.name)}(${escHtml((t.args || '').slice(0, 100))})</div>`;
    });
  }
  if (data.user_answer) extra += `<div class="conv-answer">👤 ${escHtml(data.user_answer)}</div>`;
  // For ask_user tool results, show as "You" not "Tool result"
  const askUserResult = role === 'tool' && !msg.startsWith('Written:') && !msg.startsWith('Written repo');
  const label = askUserResult ? 'You' :
                role === 'assistant' ? 'Agent' :
                role === 'user' ? 'You' :
                role === 'tool' ? 'Tool result' :
                role === 'system' ? 'System' : role;
  hideTypingIndicator();
  view.insertAdjacentHTML('beforeend',
    `<div class="conv-message conv-${role}"><div class="conv-role">${label}</div>${body}${extra}</div>`);
  view.scrollTop = view.scrollHeight;
}

function appendStep(label, state, view) {
  const existing = view.querySelector(`[data-step="${escHtml(label)}"]`);
  if (existing) {
    const dot = existing.querySelector('.step-dot'), text = existing.querySelector('span');
    if (state === 'done') { dot.className = 'step-dot done'; text.innerHTML = '✅ ' + escHtml(label); }
    else if (state === 'error') { dot.className = 'step-dot fail'; text.innerHTML = '❌ ' + escHtml(label); }
    else { dot.className = 'step-dot running'; text.innerHTML = '⏳ ' + escHtml(label); }
    return;
  }
  const icon = state === 'done' ? '✅' : state === 'error' ? '❌' : state === 'running' ? '⏳' : '○';
  view.insertAdjacentHTML('beforeend',
    `<div class="conv-step" data-step="${escHtml(label)}"><div class="step-dot ${state === 'done' ? 'done' : state === 'error' ? 'fail' : state === 'running' ? 'running' : ''}"></div><span>${icon} ${escHtml(label)}</span></div>`);
  view.scrollTop = view.scrollHeight;
}

function showInputPrompt(question) {
  let el = document.getElementById('input-prompt');
  if (!el) {
    el = document.createElement('div'); el.id = 'input-prompt'; el.className = 'hidden';
    el.innerHTML = `<span class="input-label"></span><div class="input-row"><input type="text" placeholder="Type your answer..." onkeydown="if(event.key==='Enter'){event.preventDefault();submitAnswer(currentJobId)}"><button type="button" onclick="submitAnswer(currentJobId)">Send</button></div>`;
    document.getElementById('result-view').appendChild(el);
  }
  el.querySelector('.input-label').textContent = question || 'Please provide more details';
  el.querySelector('input').value = ''; el.querySelector('input').disabled = false; el.classList.remove('hidden');
  setTimeout(() => el.querySelector('input').focus(), 100);
  const view = document.getElementById('conversation-view');
  view.insertAdjacentHTML('beforeend', `<div class="conv-message conv-user"><div class="conv-role">Question</div><div class="conv-body">${escHtml(question || '')}</div></div>`);
  view.scrollTop = view.scrollHeight;
}

function removeInputPrompt() { const el = document.getElementById('input-prompt'); if (el) el.classList.add('hidden'); }

function showApprovalPrompt(data, jobId) {
  let el = document.getElementById('input-prompt');
  if (!el) {
    el = document.createElement('div'); el.id = 'input-prompt'; el.className = 'hidden';
    el.innerHTML = `<span class="input-label"></span><div class="input-row" style="gap:12px"><button type="button" class="approve-btn" onclick="sendApproval('${jobId}', 'approve')">✅ Approve & Deploy</button><button type="button" class="reject-btn" onclick="sendApproval('${jobId}', 'reject')">❌ Reject</button></div>`;
    document.getElementById('result-view').appendChild(el);
  } else {
    el.querySelector('.input-row').innerHTML = `<button type="button" class="approve-btn" onclick="sendApproval('${jobId}', 'approve')">✅ Approve & Deploy</button><button type="button" class="reject-btn" onclick="sendApproval('${jobId}', 'reject')">❌ Reject</button>`;
  }
  el.querySelector('.input-label').textContent = (data.summary || '').replace(/Reply.*/, '').trim();
  el.classList.remove('hidden');
  const view = document.getElementById('conversation-view');
  view.insertAdjacentHTML('beforeend', `<div class="conv-message conv-user" style="border-color:var(--warning)"><div class="conv-role">⚠️ Human approval required</div><div class="conv-body">${escHtml((data.summary || '').replace(/Reply.*/, '').trim())}</div></div>`);
  view.scrollTop = view.scrollHeight;
}

async function sendApproval(jobId, decision) {
  const el = document.getElementById('input-prompt'); el.querySelectorAll('button').forEach(b => b.disabled = true);
  try { await api(`/api/jobs/${jobId}/input`, { method: 'POST', body: JSON.stringify({ answer: decision }) }); } catch (e) { /* ignore */ }
  el.classList.add('hidden');
  const view = document.getElementById('conversation-view'), icon = decision === 'approve' ? '✅ Approved — deploying' : '❌ Rejected';
  view.insertAdjacentHTML('beforeend', `<div class="conv-message conv-user"><div class="conv-role">You</div><div class="conv-body">${icon}</div></div>`);
  view.scrollTop = view.scrollHeight;
}

async function submitAnswer(jobId) {
  const el = document.getElementById('input-prompt'); if (!el) return;
  const input = el.querySelector('input'), answer = input.value.trim(); if (!answer) return;
  input.disabled = true;
  try { await api(`/api/jobs/${jobId}/input`, { method: 'POST', body: JSON.stringify({ answer }) }); }
  catch (e) { input.disabled = false; el.querySelector('.input-label').textContent = 'Failed to send — ' + e.message; return; }
  const view = document.getElementById('conversation-view');
  view.insertAdjacentHTML('beforeend', `<div class="conv-message conv-user"><div class="conv-role">You</div><div class="conv-body">${escHtml(answer)}</div></div>`);
  view.scrollTop = view.scrollHeight; el.classList.add('hidden');
}

function showTypingIndicator(text) {
  const view = document.getElementById('conversation-view'), existing = view ? view.querySelector('.typing-indicator') : null;
  if (existing) existing.querySelector('.typing-label').textContent = text || 'Thinking...';
}
function hideTypingIndicator() { const existing = document.querySelector('.typing-indicator'); if (existing) existing.remove(); }

function startElapsedTimer(startedAt) {
  stopElapsedTimer();
  const el = document.getElementById('job-elapsed');
  const startTime = startedAt ? new Date(startedAt).getTime() : Date.now();
  _elapsedTimer = setInterval(() => {
    const diff = Date.now() - startTime, s = Math.floor(diff / 1000), m = Math.floor(s / 60);
    el.textContent = m > 0 ? m + 'm ' + (s % 60) + 's elapsed' : s + 's elapsed';
  }, 1000);
}
function stopElapsedTimer() { if (_elapsedTimer) { clearInterval(_elapsedTimer); _elapsedTimer = null; } document.getElementById('job-elapsed').textContent = ''; }

async function loadFullResult(jobId) {
  const job = await api(`/api/jobs/${jobId}`);
  
  // For older completed jobs, try to get the conversation log from the /log endpoint
  const convLog = (job.result?.conversation_log) || [];
  if (!convLog.length && (job.result?.files?.length || job.result?.response || job.status === 'failed')) {
    // Fetch conversation log separately
    try {
      const logData = await api(`/api/jobs/${jobId}/log`);
      if (logData.log && logData.log.length) {
        if (!job.result) job.result = {};
        job.result.conversation_log = logData.log;
      }
    } catch { /* ignore */ }
  }
  
  renderFullDetails(job);
  return job;
}

function renderFullDetails(job) {
  if (_detailsRendered) return; _detailsRendered = true;
  const view = document.getElementById('conversation-view'), r = job.result || {};
  const statusEl = view.querySelector('.conv-status');
  if (statusEl) statusEl.innerHTML = `<span class="badge ${job.status}">${job.status}</span>`;

  // Elapsed time display
  const elapsedEl = document.getElementById('job-elapsed');
  if (elapsedEl) {
    if (job.status === 'completed' && job.completed_at && job.started_at) {
      const start = new Date(job.started_at).getTime();
      const end = new Date(job.completed_at).getTime();
      const s = Math.floor((end - start) / 1000);
      elapsedEl.textContent = s > 60 ? Math.floor(s / 60) + 'm ' + (s % 60) + 's' : s + 's';
    } else if (job.status !== 'running' && job.status !== 'awaiting_input') {
      elapsedEl.textContent = '';
    }
  }

  // Render conversation log messages for completed jobs
  const convLog = r.conversation_log || [];
  if (convLog.length) {
    // Remove any existing conversation messages that might be from SSE
    view.querySelectorAll('.conv-message').forEach(el => el.remove());
    convLog.forEach(d => appendMessage(d, view));
  }

  // Pipeline nodes
  document.querySelectorAll('.pipe-node').forEach(node => {
    const dot = node.querySelector('.pipe-dot');
    if (!dot.classList.contains('done') && !dot.classList.contains('fail')) { dot.className = 'pipe-dot done'; node.classList.add('active'); }
    const prev = node.previousElementSibling;
    if (prev && prev.classList.contains('pipe-connector') && !prev.classList.contains('done')) prev.classList.add('done');
  });
  view.querySelectorAll('.conv-step').forEach(step => {
    const dot = step.querySelector('.step-dot'), span = step.querySelector('span'), text = span ? span.textContent : '';
    if (text.startsWith('✅') || text.startsWith('❌')) return;
    if (dot.classList.contains('fail')) span.textContent = '❌ ' + text.replace(/^[✅❌⏳○]\s*/, '');
    else { dot.className = 'step-dot done'; span.textContent = '✅ ' + text.replace(/^[✅❌⏳○]\s*/, ''); }
  });
  const sel = document.getElementById('status-select'), terminal = ['completed', 'failed', 'cancelled'];
  if (terminal.includes(job.status)) { sel.classList.remove('hidden'); sel.innerHTML = '<option value="">Change...</option>'; const t = job.status === 'completed' ? ['failed'] : job.status === 'failed' ? ['queued', 'running'] : ['queued']; t.forEach(s => sel.add(new Option('to ' + s, s))); sel.value = ''; } else sel.classList.add('hidden');
  if (r.files && r.files.length) view.insertAdjacentHTML('beforeend', `<div class="conv-divider">Files (${r.files.length})</div><div class="conv-files">${r.files.map(f => '📄 ' + escHtml(f)).join('<br>')}</div>`);
  if (r.response) view.insertAdjacentHTML('beforeend', `<div class="conv-divider">Summary</div><div class="conv-message conv-summary"><div class="conv-body">${escHtml(r.response)}</div></div>`);
  if (job.error) view.insertAdjacentHTML('beforeend', `<div class="conv-divider">Error</div><div class="conv-error">${escHtml(job.error)}</div>`);
  view.scrollTop = view.scrollHeight;
}

async function cancelJob(jobId) { if (!confirm('Cancel?')) return; try { await api(`/api/jobs/${jobId}/cancel`, { method: 'POST' }); } catch (e) { alert('Cancel failed: ' + e.message); } }
async function transitionJob(jobId, ns) { if (!ns) return; try {
  await api(`/api/jobs/${jobId}/transition`, { method: 'POST', body: JSON.stringify({ status: ns }) });
  // Refresh the view
  _detailsRendered = false;
  api(`/api/jobs/${jobId}`).then(j => renderFullDetails(j)).catch(() => {});
  loadJobs();
} catch (e) { alert('Transition failed: ' + e.message); } }
function logout() { localStorage.removeItem('infra_agent_token'); localStorage.removeItem('infra_agent_user'); window.location.href = '/login'; }

async function submitJob() {
  const prompt = document.getElementById('prompt').value.trim(); if (!prompt) return;
  const btn = document.getElementById('submit-btn'), err = document.getElementById('error-msg');
  err.classList.add('hidden'); btn.disabled = true; btn.textContent = 'Submitting...';
  try {
    const resp = await api('/api/generate', { method: 'POST', body: JSON.stringify({ prompt, skip_git: document.getElementById('skip-git').checked, skip_jenkins: document.getElementById('skip-jenkins').checked }) });
    selectJob(resp.job_id);
  } catch (e) { err.textContent = e.message; err.classList.remove('hidden'); }
  finally { btn.disabled = false; btn.textContent = 'Generate'; }
}

function resetForm() {
  if (eventSource) { eventSource.close(); eventSource = null; }
  _detailsRendered = false; stopElapsedTimer(); removeInputPrompt();
  document.querySelectorAll('.pipe-node').forEach(n => { n.classList.remove('active'); n.querySelector('.pipe-dot').className = 'pipe-dot'; });
  document.querySelectorAll('.pipe-connector').forEach(c => c.className = 'pipe-connector');
  currentJobId = null;
  document.getElementById('form-view').classList.remove('hidden');
  document.getElementById('result-view').classList.add('hidden');
  document.getElementById('cancel-btn').classList.add('hidden');
  document.getElementById('prompt').value = '';
  document.getElementById('error-msg').classList.add('hidden');
  document.getElementById('conversation-view').innerHTML = '';
  loadJobs();
}

function escHtml(s) { if (!s) return ''; const div = document.createElement('div'); div.textContent = s; return div.innerHTML; }
function timeAgo(iso) { if (!iso) return '?'; const d = new Date(iso).getTime(); if (isNaN(d)) return '?'; const diff = Date.now() - d, s = Math.floor(diff / 1000); if (s < 60) return s + 's ago'; const m = Math.floor(s / 60); return m < 60 ? m + 'm ago' : Math.floor(m / 60) + 'h ago'; }

checkServer(); loadJobs(); setInterval(loadJobs, 10000);
