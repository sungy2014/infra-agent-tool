const API = window.location.origin;
const API_KEY = document.querySelector('meta[name="api-key"]')?.getAttribute('content') || '';
let pollTimer = null;
let currentJobId = null;

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
      list.innerHTML = '<div class="empty-state">No jobs yet</div>';
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
        return `<div class="job-item ${active}" onclick="selectJob('${j.job_id}')">
          <div class="job-title">${escHtml(label)}</div>
          <div class="job-meta">
            <span class="badge ${j.status}">${j.status}</span>
            <span>${timeAgo(j.created_at)}</span>
          </div>
        </div>`;
      })
      .join('');
  } catch { /* ignore */ }
}

function selectJob(jobId) {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  currentJobId = jobId;
  document.getElementById('form-view').classList.add('hidden');
  document.getElementById('result-view').classList.remove('hidden');
  document.getElementById('job-id-display').textContent = jobId.slice(0, 8);
  document.getElementById('input-section').classList.add('hidden');
  pollJob(jobId);
}

function pollJob(jobId) {
  async function fetchJob() {
    try {
      const job = await api(`/api/jobs/${jobId}`);
      renderJob(job);
      if (job.status === 'completed' || job.status === 'failed') {
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
        loadJobs();
      }
    } catch { /* ignore */ }
  }
  fetchJob();
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(fetchJob, 3000);
}

async function submitAnswer(jobId) {
  const input = document.getElementById('input-field');
  const answer = input.value.trim();
  if (!answer) return;
  input.disabled = true;
  try {
    await api(`/api/jobs/${jobId}/input`, {
      method: 'POST',
      body: JSON.stringify({ answer }),
    });
    document.getElementById('input-section').classList.add('hidden');
  } catch (e) {
    alert('Failed to submit: ' + e.message);
  } finally {
    input.disabled = false;
    input.value = '';
  }
}

function renderJob(job) {
  document.getElementById('job-status-badge').className = `badge ${job.status}`;
  document.getElementById('job-status-badge').textContent = job.status;

  const details = document.getElementById('job-details');
  const r = job.result || {};
  const parts = [];

  // Pending question
  if (job.status === 'awaiting_input' && job.pending_question) {
    const inputSec = document.getElementById('input-section');
    inputSec.classList.remove('hidden');
    document.getElementById('question-text').textContent = job.pending_question;
    document.getElementById('input-field').value = '';
    document.getElementById('input-field').disabled = false;
    document.getElementById('input-field').focus();

    parts.push(`<div class="section">
      <h3>Question</h3>
      <div class="box" style="border-color:var(--warning)">${escHtml(job.pending_question)}</div>
    </div>`);
  } else {
    document.getElementById('input-section').classList.add('hidden');
  }

  // Response / summary
  if (r.response) {
    parts.push(`<div class="section">
      <h3>Response</h3>
      <div class="box" id="response-text">${escHtml(r.response)}</div>
    </div>`);
  }

  // Files
  if (r.files && r.files.length) {
    parts.push(`<div class="section">
      <h3>Files (${r.files.length})</h3>
      <ul class="file-list">
        ${r.files.map(f => `<li>${escHtml(f)}</li>`).join('')}
      </ul>
    </div>`);
  }

  // Timeline
  const hasFiles = r.files?.length > 0;
  const resp = r.response || "";
  const gitDone = /Git: committed/.test(resp) || /Git: no changes/.test(resp);
  const jenDone = /Jenkins: triggered/.test(resp);
  const gitSkip = /Git: skipped/.test(resp);
  const jenSkip = /Jenkins: skipped/.test(resp);
  const steps = [
    { label: 'Generate Terraform code', done: hasFiles, skip: false },
    { label: 'Write files', done: hasFiles, skip: false },
    { label: 'Git commit & push', done: gitDone, skip: gitSkip },
    { label: 'Trigger Jenkins', done: jenDone, skip: jenSkip },
  ];
  parts.push(`<div class="section">
    <h3>Timeline</h3>
    <div class="timeline">
      ${steps.map(s => `
        <div class="step">
          <div class="step-dot ${s.skip ? 'skip' : s.done ? 'done' : ''}"></div>
          <div>
            <div>${s.label}</div>
            <div class="step-label">${s.skip ? 'skipped' : s.done ? 'done' : 'pending'}</div>
          </div>
        </div>`).join('')}
    </div>
  </div>`);

  // Error
  if (job.error) {
    parts.push(`<div class="section">
      <h3>Error</h3>
      <div class="box" style="color:var(--danger)">${escHtml(job.error)}</div>
    </div>`);
  }

  details.innerHTML = parts.join('');
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
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  currentJobId = null;
  document.getElementById('form-view').classList.remove('hidden');
  document.getElementById('result-view').classList.add('hidden');
  document.getElementById('input-section').classList.add('hidden');
  document.getElementById('prompt').value = '';
  document.getElementById('error-msg').classList.add('hidden');
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
