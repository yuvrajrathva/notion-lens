// ========================================
// Notion Lens - Popup JavaScript
// ========================================


// ---------- Refresh button ----------

const refreshBtn = document.getElementById('refreshBtn');
const syncValue = document.getElementById('syncValue');

refreshBtn.addEventListener('click', () => {
  // Remove the animation first so it can be restarted
  refreshBtn.classList.remove('spinning');

  // Force browser reflow to restart animation
  void refreshBtn.offsetWidth;

  // Start spinning animation
  refreshBtn.classList.add('spinning');

  // Prevent multiple clicks while refreshing
  refreshBtn.disabled = true;

  // Demo refresh delay
  setTimeout(() => {
    syncValue.textContent = 'Just now';
    refreshBtn.disabled = false;
  }, 700);
});


// ---------- Input + send button ----------

const queryInput = document.getElementById('queryInput');
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
