// ========================================
// Notion Lens - Popup JavaScript
// ========================================

const BACKEND_BASE_URL = 'http://localhost:8000';
const APP_USER_ID_KEY = 'notionLensAppUserId';

const queryInput = document.getElementById('queryInput');
const refreshBtn = document.getElementById('refreshBtn');
const syncValue = document.getElementById('syncValue');


// ---------- Notion connect ----------

const connectBtn = document.getElementById('connectBtn');
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

  if (state === 'connected') {
    statusText.textContent = detail && detail.workspace_name
      ? `Connected · ${detail.workspace_name}`
      : 'Notion Connected';
    connectBtn.textContent = 'Notion Connected';
    queryInput.disabled = false;
    queryInput.placeholder = 'Ask your notes…';
    syncValue.textContent = formatSyncedAt(detail && detail.last_synced_at);
  } else if (state === 'connecting') {
    statusText.textContent = 'Connecting to Notion…';
    connectBtn.textContent = 'Connecting…';
  } else if (state === 'error') {
    statusText.textContent = ERROR_MESSAGES[detail] || 'Connection failed. Please try again.';
    connectBtn.textContent = 'Connect Notion';
    queryInput.disabled = true;
    queryInput.placeholder = 'Connect Notion to start asking…';
  } else {
    statusText.textContent = 'Notion not connected';
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

setConnectionState('disconnected');
checkNotionStatus();


// ---------- Notion sync ----------

let syncPollTimer = null;

function formatSyncedAt(iso) {
  if (!iso) {
    return 'Never synced';
  }
  return new Date(iso).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

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
      syncValue.textContent = formatSyncedAt(data.finished_at);
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

function submitQuery() {
  const query = queryInput.value.trim();

  if (!query) {
    return;
  }

  // Placeholder:
  // Connect this to your Notion Lens query endpoint later.
  console.log('Query submitted:', query);

  // Clear input
  queryInput.value = '';

  // Reset send button
  sendBtn.classList.remove('active');
  sendBtn.disabled = true;
}


// Submit when clicking send
sendBtn.addEventListener('click', submitQuery);


// Submit when pressing Enter
queryInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    submitQuery();
  }
});
