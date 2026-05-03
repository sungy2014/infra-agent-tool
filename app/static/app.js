const API = window.location.origin;
const API_KEY = document.querySelector('meta[name="api-key"]')?.getAttribute('content') || '';
let eventSource = null;
let currentJobId = null;
let eventCount = 0;
let _detailsRendered = false;
let _elapsedTimer = null;

async function api(path, opts = {}) {
  const headers = { 'Content-Type': 'application/json', ...opts.headers };
  if (API_KEY) headers['Authorization'] = 'Bearer ' + API_KEY;
  const res = await fetch(API + path, { headers, ...opts });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`HTTP ${res.status}: ${body}`);
  }
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
    const list = document.getElementById('job-list');
    if (!data.jobs || data.jobs.length === 0) {
      list.innerHTML = '<div class="empty-state">No jobs yet — create one above</div>';
      return;
    }
    list.innerHTML = data.jobs
      .sort((a, b) => b.created_at.localeCompare(a.created_at))
      .slice(0, 20)
      .map(j => {
        const label = j.pending_question
          ? '❓ ' + j.pending_question.split('\n')[0].slice(0, 50)
          : j.result?.response
            ? j.result.response.split('\n')[0].slice(0, 60)
            : j.job_id.slice(0, 8);
        const active = j.job_id === currentJobId ? 'active' : '';
        const elapsed = j.started_at && !j.completed_at ? ' · ' + timeAgo(j.started_at) : '';
        return `<div class="job-item ${active}" onclick="selectJob('${j.job_id}')">
          <div class="job-title">${escHtml(label)}</div>
          <div class="job-meta">
            <span class="badge ${j.status}">${j.status}</span>
            <span>${timeAgo(j.created_at)}${elapsed}</span>
          </div>
        </div>`;
      })
      .join('');
  } catch { /* ignore */ }
}

function selectJob(jobId) {
  if (eventSource) { eventSource.close(); eventSource = null; }
  _detailsRendered = false;
  eventCount = 0;
  currentJobId = jobId;
  document.getElementById('form-view').classList.add('hidden');
  document.getElementById('result-view').classList.remove('hidden');
  document.getElementById('job-id-display').textContent = jobId.slice(0, 8);
  removeInputPrompt();
  startElapsedTimer();
  document.getElementById('cancel-btn').classList.remove('hidden');
  document.getElementById('conversation-view').innerHTML =
    `<div class="conv-status"><span class="badge queued">queued</span></div>
     <div class="typing-indicator"><span></span><span></span><span></span><span class="typing-label">Starting...</span></div>
     <div class="conv-divider">Steps</div>`;
  connectEvents(jobId);
}

function connectEvents(jobId) {
  if (eventSource) eventSource.close();
  let url = `${API}/api/jobs/${jobId}/events?index=${eventCount}`;
  const es = new EventSource(url);
  eventSource = es;

  es.onmessage = (event) => {
    try {
      const ev = JSON.parse(event.data);
      handleEvent(jobId, ev);
    } catch { /* ignore */ }
  };

  es.onerror = () => {
    es.close();
    eventSource = null;
    if (!currentJobId || currentJobId !== jobId) return;
    api(`/api/jobs/${jobId}`).then(job => {
      if (!job || ['completed', 'failed', 'cancelled'].includes(job.status)) {
        if (job && job.status === 'awaiting_input' && job.pending_question) {
          showInputPrompt(job.pending_question);
        }
        return;
      }
      if (currentJobId === jobId) connectEvents(jobId);
    }).catch(() => {});
  };
}

function updatePipeline(stepName, state) {
  const map = { 'clone': /clone/i, 'generate': /generat|terraform/i, 'publish': /publish|jenkins/i };
  let pipeKey = '';
  for (const [k, re] of Object.entries(map)) {
    if (re.test(stepName)) { pipeKey = k; break; }
  }
  if (!pipeKey) return;
  const node = document.querySelector(`[data-pipe="${pipeKey}"]`);
  if (!node) return;
  const dot = node.querySelector('.pipe-dot');
  node.classList.add('active');
  // Mark previous connector as done
  if (state === 'done' || state === 'running') {
    const prev = node.previousElementSibling;
    if (prev && prev.classList.contains('pipe-connector')) {
      prev.classList.add(state === 'done' ? 'done' : 'active');
    }
  }
  dot.className = 'pipe-dot ' + (state === 'running' ? 'running' : state === 'done' ? 'done' : state === 'error' ? 'fail' : '');
}

function handleEvent(jobId, ev) {
  eventCount++;
  const view = document.getElementById('conversation-view');
  if (eventCount === 1) hideTypingIndicator();

  if (ev.type === 'message') {
    appendMessage(ev.data, view);
  } else if (ev.type === 'step') {
    updatePipeline(ev.data.label, 'running');
    appendStep(ev.data.label, 'running', view);
  } else if (ev.type === 'step_done') {
    updatePipeline(ev.data.label, 'done');
    appendStep(ev.data.label, 'done', view);
  } else if (ev.type === 'step_error') {
    updatePipeline(ev.data.label, 'error');
    appendStep(ev.data.label, 'error', view);
  } else if (ev.type === 'awaiting_input') {
    showInputPrompt(ev.data.question);
  } else if (ev.type === 'approval_required') {
    showApprovalPrompt(ev.data, jobId);
  } else if (ev.type === 'commit') {
    const d = ev.data || {};
    const link = d.url ? \`<a href="${escHtml(d.url)}" target="_blank" style="color:var(--accent);text-decoration:none">${escHtml(d.hash)}</a>\` : escHtml(d.hash || '');
    view.insertAdjacentHTML('beforeend',
      \`<div class="conv-message conv-tool">
        <div class="conv-role">Git commit</div>
        <div class="conv-body">📝 ${escHtml(d.message || '')}<br>🔗 ${link} on <strong>${escHtml(d.branch || '')}</strong></div>
      </div>\`
    );
    view.scrollTop = view.scrollHeight;
  } else if (ev.type === 'jenkins_build') {
    const d = ev.data || {};
    const icon = d.result === 'SUCCESS' ? '✅' : '❌';
    const consoleText = d.console ? \`<details class="thinking-block"><summary>📋 console output</summary><pre style="font-size:11px;white-space:pre-wrap;margin-top:4px">${escHtml(d.console)}</pre></details>\` : '';
    view.insertAdjacentHTML('beforeend',
      \`<div class="conv-message conv-tool" style="border-color:${d.result === 'SUCCESS' ? 'var(--success)' : 'var(--danger)'}">
        <div class="conv-role">Jenkins build</div>
        <div class="conv-body">${icon} Build #${escHtml(d.build_number || '?')}: <strong>${escHtml(d.result || 'UNKNOWN')}</strong>\` +
        (d.url ? \` <a href="${escHtml(d.url)}" target="_blank" style="color:var(--accent);font-size:12px">open ↗</a>\` : '') +
        \`</div>${consoleText}
      </div>\`
    );
    view.scrollTop = view.scrollHeight;
  } else if (ev.type === 'complete') {
    hideTypingIndicator();
    document.getElementById('cancel-btn').classList.add('hidden');
    removeInputPrompt();
    stopElapsedTimer();
    loadJobs();
    setTimeout(() => loadFullResult(jobId), 500);
  }
}

function appendMessage(data, view) {
  const role = data.role || 'unknown';
  const msg = escHtml(data.content || '');
  let extra = '';

  if (data.reasoning) {
    extra += `<details class="thinking-block"><summary>💭 thinking</summary><div>${escHtml(data.reasoning)}</div></details>`;
  }
  if (data.tool_calls) {
    data.tool_calls.forEach(t => {
      if (t.name === 'ask_user') {
        extra += `<div class="conv-answer">❓ ${escHtml(t.args || '')}</div>`;
      } else {
        extra += `<div class="conv-toolcall">🔧 ${escHtml(t.name)}(${escHtml((t.args || '').slice(0, 100))})</div>`;
      }
    });
  }
  if (data.user_answer) {
    extra += `<div class="conv-answer">👤 ${escHtml(data.user_answer)}</div>`;
  }

  const label = role === 'assistant' ? 'Agent' :
                role === 'user' ? 'You' :
                role === 'tool' ? 'Tool result' :
                role === 'system' ? 'System' : role;

  // Hide typing indicator before first real message
  hideTypingIndicator();

  view.insertAdjacentHTML('beforeend',
    `<div class="conv-message conv-${role}">
      <div class="conv-role">${label}</div>
      <div class="conv-body${role === 'assistant' ? ' conv-assistant-body' : ''}">${msg || '(empty)'}</div>
      ${extra}
    </div>`
  );
  view.scrollTop = view.scrollHeight;
}

function appendStep(label, state, view) {
  const existing = view.querySelector(`[data-step="${escHtml(label)}"]`);
  if (existing) {
    const dot = existing.querySelector('.step-dot');
    const text = existing.querySelector('span');
    if (state === 'done') {
      dot.className = 'step-dot done';
      text.innerHTML = '✅ ' + escHtml(label);
    } else if (state === 'error') {
      dot.className = 'step-dot fail';
      text.innerHTML = '❌ ' + escHtml(label);
    } else if (state === 'running') {
      dot.className = 'step-dot running';
      text.innerHTML = '⏳ ' + escHtml(label);
    }
    return;
  }
  const icon = state === 'done' ? '✅' : state === 'error' ? '❌' : state === 'running' ? '⏳' : '○';
  view.insertAdjacentHTML('beforeend',
    `<div class="conv-step" data-step="${escHtml(label)}">
      <div class="step-dot ${state === 'done' ? 'done' : state === 'error' ? 'fail' : state === 'running' ? 'running' : ''}"></div>
      <span>${icon} ${escHtml(label)}</span>
    </div>`
  );
  view.scrollTop = view.scrollHeight;
}

function showInputPrompt(question) {
  let el = document.getElementById('input-prompt');
  if (!el) {
    el = document.createElement('div');
    el.id = 'input-prompt';
    el.className = 'hidden';
    el.innerHTML = `
      <span class="input-label"></span>
      <div class="input-row">
        <input type="text" placeholder="Type your answer..." onkeydown="if(event.key==='Enter'){event.preventDefault();submitAnswer(currentJobId)}">
        <button type="button" onclick="submitAnswer(currentJobId)">Send</button>
      </div>`;
    document.getElementById('result-view').appendChild(el);
  }
  el.querySelector('.input-label').textContent = question || 'Please provide more details';
  el.querySelector('input').value = '';
  el.querySelector('input').disabled = false;
  el.classList.remove('hidden');
  setTimeout(() => el.querySelector('input').focus(), 100);

  // Also append question to conversation
  const view = document.getElementById('conversation-view');
  view.insertAdjacentHTML('beforeend',
    `<div class="conv-message conv-user">
      <div class="conv-role">Question</div>
      <div class="conv-body">${escHtml(question || '')}</div>
    </div>`
  );
  view.scrollTop = view.scrollHeight;
}

function removeInputPrompt() {
  const el = document.getElementById('input-prompt');
  if (el) el.classList.add('hidden');
}

function showApprovalPrompt(data, jobId) {
  let el = document.getElementById('input-prompt');
  if (!el) {
    el = document.createElement('div');
    el.id = 'input-prompt';
    el.className = 'hidden';
    el.innerHTML = `<span class="input-label"></span>
      <div class="input-row" style="gap:12px">
        <button type="button" class="approve-btn" onclick="sendApproval('${jobId}', 'approve')">✅ Approve & Deploy</button>
        <button type="button" class="reject-btn" onclick="sendApproval('${jobId}', 'reject')">❌ Reject</button>
      </div>`;
    document.getElementById('result-view').appendChild(el);
  } else {
    el.querySelector('.input-row').innerHTML =
      `<button type="button" class="approve-btn" onclick="sendApproval('${jobId}', 'approve')">✅ Approve & Deploy</button>
       <button type="button" class="reject-btn" onclick="sendApproval('${jobId}', 'reject')">❌ Reject</button>`;
  }
  el.querySelector('.input-label').textContent = (data.summary || '').replace(/Reply.*/, '').trim();
  el.classList.remove('hidden');

  const view = document.getElementById('conversation-view');
  view.insertAdjacentHTML('beforeend',
    `<div class="conv-message conv-user" style="border-color:var(--warning)">
      <div class="conv-role">⚠️ Human approval required</div>
      <div class="conv-body">${escHtml((data.summary || '').replace(/Reply.*/, '').trim())}</div>
    </div>`
  );
  view.scrollTop = view.scrollHeight;
}

async function sendApproval(jobId, decision) {
  const el = document.getElementById('input-prompt');
  el.querySelectorAll('button').forEach(b => b.disabled = true);
  try {
    await api(`/api/jobs/${jobId}/input`, { method: 'POST', body: JSON.stringify({ answer: decision }) });
  } catch (e) { /* ignore */ }
  el.classList.add('hidden');
  // Add decision to conversation
  const view = document.getElementById('conversation-view');
  const icon = decision === 'approve' ? '✅ Approved — deploying' : '❌ Rejected';
  view.insertAdjacentHTML('beforeend',
    `<div class="conv-message conv-user">
      <div class="conv-role">You</div>
      <div class="conv-body">${icon}</div>
    </div>`
  );
  view.scrollTop = view.scrollHeight;
}

async function submitAnswer(jobId) {
  const el = document.getElementById('input-prompt');
  if (!el) return;
  const input = el.querySelector('input');
  const answer = input.value.trim();
  if (!answer) return;
  input.disabled = true;
  try {
    await api(`/api/jobs/${jobId}/input`, { method: 'POST', body: JSON.stringify({ answer }) });
  } catch (e) { /* ignore */ }
  const view = document.getElementById('conversation-view');
  view.insertAdjacentHTML('beforeend',
    `<div class="conv-message conv-user">
      <div class="conv-role">You</div>
      <div class="conv-body">${escHtml(answer)}</div>
    </div>`
  );
  view.scrollTop = view.scrollHeight;
  el.classList.add('hidden');
}

function showTypingIndicator(text) {
  const view = document.getElementById('conversation-view');
  const existing = view.querySelector('.typing-indicator');
  if (existing) existing.querySelector('.typing-label').textContent = text || 'Thinking...';
}

function hideTypingIndicator() {
  const existing = document.querySelector('.typing-indicator');
  if (existing) existing.remove();
}

function startElapsedTimer() {
  stopElapsedTimer();
  const el = document.getElementById('job-elapsed');
  const start = Date.now();
  _elapsedTimer = setInterval(() => {
    const diff = Date.now() - start;
    const s = Math.floor(diff / 1000);
    const m = Math.floor(s / 60);
    if (m > 0) el.textContent = m + 'm ' + (s % 60) + 's elapsed';
    else el.textContent = s + 's elapsed';
  }, 1000);
}

function stopElapsedTimer() {
  if (_elapsedTimer) { clearInterval(_elapsedTimer); _elapsedTimer = null; }
  document.getElementById('job-elapsed').textContent = '';
}

function loadFullResult(jobId) {
  api(`/api/jobs/${jobId}`).then(j => renderFullDetails(j)).catch(() => {});
}

function renderFullDetails(job) {
  if (_detailsRendered) return;
  _detailsRendered = true;
  const view = document.getElementById('conversation-view');
  const r = job.result || {};

  const statusEl = view.querySelector('.conv-status');
  if (statusEl) statusEl.innerHTML = `<span class="badge ${job.status}">${job.status}</span>`;

  // Mark all pipeline nodes as done
  document.querySelectorAll('.pipe-node').forEach(node => {
    const dot = node.querySelector('.pipe-dot');
    if (!dot.classList.contains('done') && !dot.classList.contains('fail')) {
      dot.className = 'pipe-dot done';
      node.classList.add('active');
    }
    const prev = node.previousElementSibling;
    if (prev && prev.classList.contains('pipe-connector') && !prev.classList.contains('done')) {
      prev.classList.add('done');
    }
  });

  // Mark all step dots as done since the job is finished
  view.querySelectorAll('.conv-step').forEach(step => {
    const dot = step.querySelector('.step-dot');
    const span = step.querySelector('span');
    const text = span ? span.textContent : '';
    if (text.startsWith('✅') || text.startsWith('❌')) return;
    if (dot.classList.contains('fail')) {
      span.textContent = '❌ ' + text.replace(/^[✅❌⏳○]\s*/, '');
    } else {
      dot.className = 'step-dot done';
      span.textContent = '✅ ' + text.replace(/^[✅❌⏳○]\s*/, '');
    }
  });

  const sel = document.getElementById('status-select');
  const terminal = ['completed', 'failed', 'cancelled'];
  if (terminal.includes(job.status)) {
    sel.classList.remove('hidden');
    sel.innerHTML = '<option value="">Change status...</option>';
    const targets = job.status === 'completed' ? ['failed'] :
                    job.status === 'failed' ? ['queued', 'running'] : ['queued'];
    targets.forEach(s => sel.add(new Option('to ' + s, s)));
    sel.value = '';
  } else { sel.classList.add('hidden'); }

  if (r.files && r.files.length) {
    view.insertAdjacentHTML('beforeend',
      `<div class="conv-divider">Files (${r.files.length})</div>
      <div class="conv-files">${r.files.map(f => '📄 ' + escHtml(f)).join('<br>')}</div>`
    );
  }

  if (r.response) {
    view.insertAdjacentHTML('beforeend',
      `<div class="conv-divider">Summary</div>
      <div class="conv-message conv-summary"><div class="conv-body">${escHtml(r.response)}</div></div>`
    );
  }

  if (job.error) {
    view.insertAdjacentHTML('beforeend',
      `<div class="conv-divider">Error</div>
      <div class="conv-error">${escHtml(job.error)}</div>`
    );
  }

  view.scrollTop = view.scrollHeight;
}

async function cancelJob(jobId) {
  if (!confirm('Cancel this job?')) return;
  try { await api(`/api/jobs/${jobId}/cancel`, { method: 'POST' }); } catch (e) { alert('Cancel failed: ' + e.message); }
}

async function transitionJob(jobId, newStatus) {
  if (!newStatus) return;
  try { await api(`/api/jobs/${jobId}/transition`, { method: 'POST', body: JSON.stringify({ status: newStatus }) }); } catch (e) { alert('Transition failed: ' + e.message); }
}

async function submitJob() {
  const prompt = document.getElementById('prompt').value.trim();
  if (!prompt) return;
  const btn = document.getElementById('submit-btn');
  const err = document.getElementById('error-msg');
  err.classList.add('hidden');
  btn.disabled = true;
  btn.textContent = 'Submitting...';
  try {
    const resp = await api('/api/generate', {
      method: 'POST',
      body: JSON.stringify({
        prompt,
        skip_git: document.getElementById('skip-git').checked,
        skip_jenkins: document.getElementById('skip-jenkins').checked,
      }),
    });
    selectJob(resp.job_id);
  } catch (e) {
    err.textContent = e.message;
    err.classList.remove('hidden');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Generate';
  }
}

function resetForm() {
  if (eventSource) { eventSource.close(); eventSource = null; }
  _detailsRendered = false;
  stopElapsedTimer();
  removeInputPrompt();
  // Reset pipeline bar
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

function escHtml(s) {
  if (!s) return '';
  const div = document.createElement('div');
  div.textContent = s;
  return div.innerHTML;
}

function timeAgo(iso) {
  const diff = Date.now() - new Date(iso).getTime();
  const s = Math.floor(diff / 1000);
  if (s < 60) return s + 's ago';
  const m = Math.floor(s / 60);
  if (m < 60) return m + 'm ago';
  return Math.floor(m / 60) + 'h ago';
}

checkServer();
loadJobs();
setInterval(loadJobs, 10000);
