// ========================================
// Notion Lens - Popup JavaScript
// ========================================

const BACKEND_BASE_URL = 'http://localhost:8000';
const APP_USER_ID_KEY = 'notionLensAppUserId';

const queryInput = document.getElementById('queryInput');
const refreshBtn = document.getElementById('refreshBtn');
const syncValue = document.getElementById('syncValue');
const thread = document.getElementById('thread');
const mainEmptyState = document.getElementById('mainEmptyState');


// ---------- Notion connect ----------

const connectBtn = document.getElementById('connectBtn');
const addPagesBtn = document.getElementById('addPagesBtn');
const statusDot = document.getElementById('statusDot');
const statusText = document.getElementById('statusText');

const ERROR_MESSAGES = {
  access_denied: 'You declined the Notion connection request.',
  invalid_state: 'The connection request expired. Please try again.',
  missing_code: 'Notion did not return an authorization code.',
  token_exchange_failed: 'Notion rejected the authorization code.',
  network_error: 'Could not reach Notion. Check your connection.',
};

let pollTimer = null;

function getAppUserId() {
  return new Promise((resolve) => {
    chrome.storage.local.get([APP_USER_ID_KEY], (result) => {
      if (result[APP_USER_ID_KEY]) {
        resolve(result[APP_USER_ID_KEY]);
        return;
      }
      const id = crypto.randomUUID();
      chrome.storage.local.set({ [APP_USER_ID_KEY]: id }, () => resolve(id));
    });
  });
}

function setConnectionState(state, detail) {
  statusDot.className = `status-dot ${state}`;
  connectBtn.disabled = state === 'connecting';
  connectBtn.classList.toggle('connected', state === 'connected');
  refreshBtn.disabled = state !== 'connected';
  addPagesBtn.hidden = state !== 'connected';
  if (state !== 'connected') {
    addPagesBtn.disabled = false;
    addPagesBtn.title = "Add Pages"
    addPagesBtn.textContent = '+';
  }

  if (state === 'connected') {
    statusText.textContent = detail && detail.workspace_name
      ? `${detail.workspace_name}`
      : '';
    connectBtn.textContent = 'Notion Connected';
    queryInput.disabled = false;
    queryInput.placeholder = 'Ask your notes…';
    renderSyncValue(detail && detail.last_synced_at);
  } else if (state === 'connecting') {
    statusText.textContent = 'Connecting to Notion…';
    connectBtn.textContent = 'Connecting…';
  } else if (state === 'error') {
    statusText.textContent = ERROR_MESSAGES[detail] || 'Connection failed. Please try again.';
    connectBtn.textContent = 'Connect Notion';
    queryInput.disabled = true;
    queryInput.placeholder = 'Connect Notion to start asking…';
  } else {
    // statusText.textContent = 'Notion not connected';
    connectBtn.textContent = 'Connect Notion';
    queryInput.disabled = true;
    queryInput.placeholder = 'Connect Notion to start asking…';
  }
}

async function checkNotionStatus() {
  const appUserId = await getAppUserId();

  try {
    const res = await fetch(
      `${BACKEND_BASE_URL}/auth/notion/status?app_user_id=${encodeURIComponent(appUserId)}`
    );
    const data = await res.json();

    if (data.connected) {
      setConnectionState('connected', data);
      // Opening the panel may have triggered a daily auto-sync server-side;
      // pick up its "running"/finished state if so.
      pollSyncStatus();
      return true;
    }
    if (data.error) {
      setConnectionState('error', data.error);
      return true;
    }
    return false;
  } catch (err) {
    console.error('Notion status check failed:', err);
    return false;
  }
}

async function startNotionConnect() {
  if (pollTimer) {
    return;
  }

  const appUserId = await getAppUserId();
  setConnectionState('connecting');

  let authorizeUrl;
  try {
    const res = await fetch(
      `${BACKEND_BASE_URL}/auth/notion/login?app_user_id=${encodeURIComponent(appUserId)}`
    );
    if (!res.ok) {
      throw new Error('login_request_failed');
    }
    const data = await res.json();
    if (!data.authorize_url) {
      throw new Error('missing_authorize_url');
    }

    authorizeUrl = data.authorize_url;
  } catch (err) {
    console.error('Failed to start Notion OAuth:', err);
    statusText.textContent = 'Could not reach the Notion Lens backend. Is it running?';
    connectBtn.textContent = 'Connect Notion';
    connectBtn.disabled = false;
    statusDot.className = 'status-dot error';
    return;
  }

  chrome.tabs.create({ url: authorizeUrl });

  const startedAt = Date.now();
  const POLL_INTERVAL_MS = 1500;
  const TIMEOUT_MS = 3 * 60 * 1000;

  pollTimer = setInterval(async () => {
    const settled = await checkNotionStatus();
    const timedOut = Date.now() - startedAt > TIMEOUT_MS;

    if (settled || timedOut) {
      clearInterval(pollTimer);
      pollTimer = null;

      if (!settled && timedOut) {
        setConnectionState('error', null);
        statusText.textContent = 'Connection timed out. Please try again.';
      }
    }
  }, POLL_INTERVAL_MS);
}

connectBtn.addEventListener('click', () => {
  if (connectBtn.classList.contains('connected')) {
    return;
  }
  startNotionConnect();
});

// Re-opening Notion's OAuth consent screen for an already-connected workspace
// re-shows its page picker with prior selections kept, letting the user grant
// access to more pages without disconnecting. The backend detects this as a
// reconnect and force-syncs everything the integration can now see (a newly
// shared but not-recently-edited page wouldn't surface via the normal
// incremental sync).
let addPagesPollTimer = null;

async function startAddPages() {
  if (addPagesPollTimer || addPagesBtn.disabled) {
    return;
  }

  const appUserId = await getAppUserId();
  addPagesBtn.disabled = true;
  addPagesBtn.textContent = 'Waiting for Notion…';

  let authorizeUrl;
  try {
    const res = await fetch(
      `${BACKEND_BASE_URL}/auth/notion/login?app_user_id=${encodeURIComponent(appUserId)}`
    );
    if (!res.ok) {
      throw new Error('login_request_failed');
    }
    const data = await res.json();
    if (!data.authorize_url) {
      throw new Error('missing_authorize_url');
    }
    authorizeUrl = data.authorize_url;
  } catch (err) {
    console.error('Failed to start add-pages flow:', err);
    addPagesBtn.disabled = false;
    addPagesBtn.title = "Add Pages"
    addPagesBtn.textContent = '+';
    return;
  }

  chrome.tabs.create({ url: authorizeUrl });

  const startedAt = Date.now();
  const POLL_INTERVAL_MS = 1500;
  const TIMEOUT_MS = 3 * 60 * 1000;
  let sawRunning = false;

  addPagesPollTimer = setInterval(async () => {
    let data;
    try {
      const res = await fetch(
        `${BACKEND_BASE_URL}/notion/sync/status?app_user_id=${encodeURIComponent(appUserId)}`
      );
      data = await res.json();
    } catch (err) {
      console.error('Add-pages sync status check failed:', err);
      return;
    }

    if (data.state === 'running') {
      sawRunning = true;
      const count = data.pages_processed || 0;
      syncValue.textContent = `Adding pages… ${count} page${count === 1 ? '' : 's'}`;
      return;
    }

    const timedOut = Date.now() - startedAt > TIMEOUT_MS;
    if (!sawRunning && !timedOut) {
      return;
    }

    clearInterval(addPagesPollTimer);
    addPagesPollTimer = null;
    addPagesBtn.disabled = false;
    addPagesBtn.title = "Add Pages"
    addPagesBtn.textContent = '+';

    if (sawRunning && data.state === 'success') {
      renderSyncValue(data.finished_at);
    } else if (sawRunning && data.state === 'error') {
      syncValue.textContent = 'Sync failed';
      console.error('Add-pages sync failed:', data.message);
    }
  }, POLL_INTERVAL_MS);
}

addPagesBtn.addEventListener('click', startAddPages);

setConnectionState('disconnected');
checkNotionStatus();


// ---------- Notion sync ----------

let syncPollTimer = null;
let lastSyncedIso = null;

function formatSyncedAt(iso) {
  if (!iso) {
    return 'Never synced';
  }

  const then = new Date(iso).getTime();
  const diffSeconds = Math.round((Date.now() - then) / 1000);

  if (diffSeconds < 30) {
    return 'Just now';
  }
  if (diffSeconds < 60) {
    return `${diffSeconds} seconds ago`;
  }
  const diffMinutes = Math.round(diffSeconds / 60);
  if (diffMinutes < 60) {
    return `${diffMinutes} minute${diffMinutes === 1 ? '' : 's'} ago`;
  }
  const diffHours = Math.round(diffMinutes / 60);
  if (diffHours < 24) {
    return `${diffHours} hour${diffHours === 1 ? '' : 's'} ago`;
  }
  const diffDays = Math.round(diffHours / 24);
  if (diffDays === 1) {
    return 'Yesterday';
  }
  if (diffDays < 7) {
    return `${diffDays} days ago`;
  }
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function renderSyncValue(iso) {
  lastSyncedIso = iso || null;
  syncValue.textContent = formatSyncedAt(lastSyncedIso);
}

// Keeps the relative label ("Just now" -> "2 minutes ago" -> ...) fresh while
// the side panel stays open, without re-polling the backend.
setInterval(() => {
  if (lastSyncedIso && !syncPollTimer) {
    syncValue.textContent = formatSyncedAt(lastSyncedIso);
  }
}, 60 * 1000);

function pollSyncStatus() {
  if (syncPollTimer) {
    return;
  }

  syncPollTimer = setInterval(async () => {
    const appUserId = await getAppUserId();
    let data;
    try {
      const res = await fetch(
        `${BACKEND_BASE_URL}/notion/sync/status?app_user_id=${encodeURIComponent(appUserId)}`
      );
      data = await res.json();
    } catch (err) {
      console.error('Sync status check failed:', err);
      return;
    }

    if (data.state === 'running') {
      const count = data.pages_processed || 0;
      syncValue.textContent = `Syncing… ${count} page${count === 1 ? '' : 's'}`;
      return;
    }

    clearInterval(syncPollTimer);
    syncPollTimer = null;
    refreshBtn.classList.remove('spinning');
    refreshBtn.disabled = false;

    if (data.state === 'success') {
      renderSyncValue(data.finished_at);
      refreshBtn.title = data.message || '';
    } else if (data.state === 'error') {
      syncValue.textContent = 'Sync failed';
      refreshBtn.title = data.message || 'Sync failed';
      console.error('Notion sync failed:', data.message);
    }
  }, 1500);
}

refreshBtn.addEventListener('click', async () => {
  if (refreshBtn.disabled) {
    return;
  }

  refreshBtn.classList.remove('spinning');
  void refreshBtn.offsetWidth;
  refreshBtn.classList.add('spinning');
  refreshBtn.disabled = true;
  refreshBtn.title = '';
  syncValue.textContent = 'Syncing…';

  const appUserId = await getAppUserId();
  try {
    const res = await fetch(
      `${BACKEND_BASE_URL}/notion/sync?app_user_id=${encodeURIComponent(appUserId)}`,
      { method: 'POST' }
    );
    if (res.status === 409) {
      syncValue.textContent = 'Connect Notion first';
      refreshBtn.classList.remove('spinning');
      refreshBtn.disabled = false;
      return;
    }
    if (!res.ok) {
      throw new Error(`sync_start_failed_${res.status}`);
    }
  } catch (err) {
    console.error('Failed to start Notion sync:', err);
    syncValue.textContent = 'Could not reach backend';
    refreshBtn.classList.remove('spinning');
    refreshBtn.disabled = false;
    return;
  }

  pollSyncStatus();
});


// ---------- Input + send button ----------

const sendBtn = document.getElementById('sendBtn');

queryInput.addEventListener('input', () => {
  const hasText = queryInput.value.trim().length > 0;

  sendBtn.classList.toggle('active', hasText);
  sendBtn.disabled = !hasText;
});


// ---------- Submit query ----------

const MAX_HISTORY_TURNS_SENT = 6;
let conversationHistory = [];
let queryInFlight = false;

function appendMessage({ role, text, citations, isLoading, isError }) {
  if (thread.hidden) {
    mainEmptyState.hidden = true;
    thread.hidden = false;
  }

  const msg = document.createElement('div');
  msg.className = `msg ${role}`;
  if (isLoading) msg.classList.add('loading');
  if (isError) msg.classList.add('error');

  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble';

  if (isLoading) {
    bubble.innerHTML = '<span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>';
  } else {
    bubble.textContent = text;
  }
  msg.appendChild(bubble);

  if (citations && citations.length) {
    const seenPages = new Set();
    const citationsRow = document.createElement('div');
    citationsRow.className = 'citations';
    citations.forEach((c) => {
      if (!c.url || seenPages.has(c.notion_page_id)) return;
      seenPages.add(c.notion_page_id);
      const link = document.createElement('a');
      link.className = 'citation-link';
      link.href = c.url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = `[${c.index}] ${c.title || 'Untitled'}`;
      citationsRow.appendChild(link);
    });
    if (citationsRow.children.length) {
      msg.appendChild(citationsRow);
    }
  }

  thread.appendChild(msg);
  thread.scrollTop = thread.scrollHeight;
  return msg;
}

async function submitQuery() {
  const query = queryInput.value.trim();

  if (!query || queryInFlight) {
    return;
  }

  queryInFlight = true;
  queryInput.value = '';
  sendBtn.classList.remove('active');
  sendBtn.disabled = true;
  queryInput.disabled = true;

  appendMessage({ role: 'user', text: query });
  const loadingMsg = appendMessage({ role: 'assistant', isLoading: true });

  const historyToSend = conversationHistory.slice(-MAX_HISTORY_TURNS_SENT);

  try {
    const appUserId = await getAppUserId();
    const res = await fetch(
      `${BACKEND_BASE_URL}/notion/query?app_user_id=${encodeURIComponent(appUserId)}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: query, history: historyToSend }),
      }
    );

    loadingMsg.remove();

    if (res.status === 409) {
      appendMessage({ role: 'assistant', text: 'Connect Notion first to ask questions.', isError: true });
      return;
    }
    if (!res.ok) {
      throw new Error(`query_failed_${res.status}`);
    }

    const data = await res.json();
    appendMessage({ role: 'assistant', text: data.answer, citations: data.citations });

    conversationHistory.push({ role: 'user', content: query });
    conversationHistory.push({ role: 'assistant', content: data.answer });
  } catch (err) {
    console.error('Query failed:', err);
    loadingMsg.remove();
    appendMessage({
      role: 'assistant',
      text: 'Something went wrong reaching Notion Lens. Please try again.',
      isError: true,
    });
  } finally {
    queryInFlight = false;
    queryInput.disabled = false;
    queryInput.focus();
  }
}


// Submit when clicking send
sendBtn.addEventListener('click', submitQuery);


// Submit when pressing Enter
queryInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    submitQuery();
  }
});
