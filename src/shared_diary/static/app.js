const key = location.pathname.split('/').filter(Boolean)[1];
const apiBase = `/api/${encodeURIComponent(key)}`;
let state = {
  me: null,
  participants: [],
  entries: [],
  timezone: 'Asia/Shanghai',
  authorId: null,
};

const presets = {
  "暖白墨绿": { paper: "#f6f0df", ink: "#26342f", accent: "#8a5b3d", reply: "#e8eee5" },
  "奶油琥珀": { paper: "#fff2d6", ink: "#3f3328", accent: "#a36520", reply: "#f4dfbd" },
  "浅粉薄荷": { paper: "#fff3f5", ink: "#35524c", accent: "#b96f86", reply: "#e2f1ea" },
  "夜蓝月光": { paper: "#18212b", ink: "#e6edf1", accent: "#d8b777", reply: "#243443" },
};
let currentTheme = { ...presets['暖白墨绿'] };

function htmlEscape(value) {
  const div = document.createElement('div');
  div.textContent = value ?? '';
  return div.innerHTML;
}

function participantName(id) {
  return state.participants.find(item => item.id === id)?.display_name || id;
}

function formatDiaryTime(value) {
  return new Date(value).toLocaleString(undefined, { timeZone: state.timezone });
}

function formatDiaryDay(day) {
  const [year, month, date] = day.split('-').map(Number);
  return new Date(Date.UTC(year, month - 1, date)).toLocaleDateString(undefined, {
    year: 'numeric', month: 'long', day: 'numeric', timeZone: 'UTC',
  });
}

async function api(path, options = {}) {
  const response = await fetch(`${apiBase}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  const data = await response.json();
  if (!response.ok || !data.ok) throw new Error(data.error || '请求失败');
  return data;
}

function localDateTimeValue() {
  const now = new Date();
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
  return now.toISOString().slice(0, 16);
}

function renderAudience() {
  const root = document.querySelector('#audienceList');
  root.innerHTML = '';
  state.participants.filter(item => item.id !== state.me.id).forEach(item => {
    const label = document.createElement('label');
    label.className = 'chip';
    label.innerHTML = `<input type="checkbox" value="${htmlEscape(item.id)}">${htmlEscape(item.display_name)}`;
    root.append(label);
  });
}

function setAuthorFilter(authorId) {
  state.authorId = authorId;
  const url = new URL(location.href);
  if (authorId) url.searchParams.set('author', authorId);
  else url.searchParams.delete('author');
  history.replaceState(null, '', url);
  renderAuthorFilters();
  loadTimeline().catch(error => {
    document.querySelector('#emptyState').textContent = `翻页失败：${error.message}`;
  });
}

function renderAuthorFilters() {
  const root = document.querySelector('#authorFilters');
  root.replaceChildren();
  const choices = [{ id: null, display_name: '全部' }, ...state.participants];
  choices.forEach(person => {
    const button = document.createElement('button');
    const selected = state.authorId === person.id;
    button.type = 'button';
    button.className = `author-filter${selected ? ' active' : ''}`;
    button.textContent = person.display_name;
    button.setAttribute('aria-pressed', String(selected));
    button.addEventListener('click', () => setAuthorFilter(person.id));
    root.append(button);
  });
}

function visibilityLabel(value) {
  return { shared: '共同可见', private: '仅自己', selected: '指定可见', challenge: '趣味锁' }[value] || value;
}

async function loadReplies(entry, root) {
  if (entry.locked) return;
  try {
    const data = await api(`/entries/${entry.id}/replies`);
    root.innerHTML = data.replies.map(reply => `
      <div class="reply">
        <small>${htmlEscape(participantName(reply.author_id))} · ${formatDiaryTime(reply.created_at)}</small>
        <p>${htmlEscape(reply.body)}</p>
      </div>`).join('');
  } catch (_) {
    root.innerHTML = '';
  }
}

function renderEntry(entry) {
  const fragment = document.querySelector('#entryTemplate').content.cloneNode(true);
  const article = fragment.querySelector('.entry');
  article.dataset.entryId = entry.id;
  fragment.querySelector('.entry-author-name').textContent = participantName(entry.author_id);
  if (entry.unread) fragment.querySelector('.unread-dot').classList.remove('hidden');
  fragment.querySelector('.entry-time').textContent = formatDiaryTime(entry.occurred_at);
  fragment.querySelector('.visibility-badge').textContent = visibilityLabel(entry.visibility);

  const preview = fragment.querySelector('.entry-preview');
  if (entry.preview) { preview.textContent = `“${entry.preview}”`; preview.classList.remove('hidden'); }

  const body = fragment.querySelector('.entry-body');
  const locked = fragment.querySelector('.locked-box');
  const replyRow = fragment.querySelector('.reply-row');
  const replies = fragment.querySelector('.replies');
  const entryActions = fragment.querySelector('.entry-actions');
  const editRow = fragment.querySelector('.edit-entry-row');

  if (entry.author_id === state.me.id) {
    entryActions.classList.remove('hidden');
    const editInput = editRow.querySelector('.edit-entry-input');
    const editStatus = editRow.querySelector('.edit-entry-status');
    entryActions.querySelector('.edit-entry-button').addEventListener('click', () => {
      editInput.value = entry.body || '';
      editStatus.textContent = '';
      body.classList.add('hidden');
      editRow.classList.remove('hidden');
      editInput.focus();
    });
    editRow.querySelector('.cancel-edit-button').addEventListener('click', () => {
      editRow.classList.add('hidden');
      body.classList.remove('hidden');
    });
    editRow.querySelector('.save-edit-button').addEventListener('click', async () => {
      if (!editInput.value.trim()) { editStatus.textContent = '这一页还没有字。'; return; }
      editStatus.textContent = '正在修改……';
      try {
        await api(`/entries/${entry.id}`, {
          method: 'PATCH',
          body: JSON.stringify({ body: editInput.value }),
        });
        await loadTimeline();
      } catch (error) { editStatus.textContent = error.message; }
    });
    entryActions.querySelector('.delete-entry-button').addEventListener('click', async () => {
      if (!window.confirm('确定删除这一篇日记吗？它下面的回应也会一起删除。')) return;
      try {
        await api(`/entries/${entry.id}`, { method: 'DELETE' });
        await loadTimeline();
      } catch (error) { window.alert(error.message); }
    });
  }

  if (entry.locked) {
    body.classList.add('hidden');
    locked.classList.remove('hidden');
    locked.querySelector('.lock-question').textContent = entry.challenge_question || '这一页上了锁。';
    locked.querySelector('.lock-hint').textContent = entry.challenge_hint ? `提示：${entry.challenge_hint}` : '';
    locked.querySelector('.unlock-button').addEventListener('click', async () => {
      const answer = locked.querySelector('.unlock-answer').value;
      const status = locked.querySelector('.unlock-status');
      try {
        const data = await api(`/entries/${entry.id}/unlock`, { method: 'POST', body: JSON.stringify({ answer }) });
        if (!data.unlocked) { status.textContent = '还差一点，再想想。'; return; }
        status.textContent = '咔哒——打开了。';
        await loadTimeline();
      } catch (error) { status.textContent = error.message; }
    });
  } else {
    body.textContent = entry.body;
    replyRow.classList.remove('hidden');
    loadReplies(entry, replies);
    replyRow.querySelector('.reply-button').addEventListener('click', async () => {
      const input = replyRow.querySelector('.reply-input');
      if (!input.value.trim()) return;
      try {
        await api(`/entries/${entry.id}/replies`, { method: 'POST', body: JSON.stringify({ body: input.value }) });
        input.value = '';
        await loadReplies(entry, replies);
      } catch (error) { input.placeholder = error.message; }
    });
    if (entry.unread) {
      setTimeout(() => {
        api(`/entries/${entry.id}/read`, { method: 'POST', body: '{}' }).catch(() => {});
      }, 1200);
    }
  }
  return fragment;
}

function renderTimeline() {
  const root = document.querySelector('#timeline');
  const empty = document.querySelector('#emptyState');
  root.innerHTML = '';
  const selectedName = state.authorId ? participantName(state.authorId) : null;
  empty.textContent = selectedName
    ? `${selectedName} 还没有留下你能看到的日记。`
    : '这里还是一页空白。第一行字在等你。';
  empty.classList.toggle('hidden', state.entries.length > 0);
  let currentDay = '';
  state.entries.forEach(entry => {
    const day = entry.local_day || entry.occurred_at.slice(0, 10);
    if (day !== currentDay) {
      currentDay = day;
      const divider = document.createElement('div');
      divider.className = 'day-divider';
      divider.textContent = formatDiaryDay(day);
      root.append(divider);
    }
    root.append(renderEntry(entry));
  });
}

async function loadTimeline() {
  const authorQuery = state.authorId ? `&author_id=${encodeURIComponent(state.authorId)}` : '';
  const data = await api(`/timeline?limit=80${authorQuery}`);
  state.entries = data.entries;
  renderTimeline();
}

function selectedAudience() {
  return [...document.querySelectorAll('#audienceList input:checked')].map(input => input.value);
}

async function saveEntry() {
  const status = document.querySelector('#composerStatus');
  const visibility = document.querySelector('#visibility').value;
  const body = document.querySelector('#entryBody').value;
  if (!body.trim()) { status.textContent = '这一页还没有字。'; return; }
  const payload = {
    body,
    occurred_at: new Date(document.querySelector('#occurredAt').value).toISOString(),
    visibility,
    audience: ['selected', 'challenge'].includes(visibility) ? selectedAudience() : [],
  };
  if (visibility === 'challenge') {
    payload.challenge_question = document.querySelector('#challengeQuestion').value;
    payload.challenge_answers = document.querySelector('#challengeAnswers').value.split('|').map(x => x.trim()).filter(Boolean);
    payload.challenge_hint = document.querySelector('#challengeHint').value;
    payload.preview = document.querySelector('#preview').value;
  }
  status.textContent = '墨迹还没干……';
  try {
    await api('/entries', { method: 'POST', body: JSON.stringify(payload) });
    document.querySelector('#entryBody').value = '';
    status.textContent = '已经写进这一页。';
    await loadTimeline();
  } catch (error) { status.textContent = error.message; }
}

function applyTheme(theme) {
  currentTheme = { ...theme };
  const root = document.documentElement;
  Object.entries(currentTheme).forEach(([name, value]) => root.style.setProperty(`--${name}`, value));
  document.querySelectorAll('[data-theme-key]').forEach(input => input.value = currentTheme[input.dataset.themeKey]);
  localStorage.setItem('shared-diary-theme', JSON.stringify(currentTheme));
}

function setupTheme() {
  const saved = JSON.parse(localStorage.getItem('shared-diary-theme') || 'null') || presets['暖白墨绿'];
  applyTheme(saved);
  const list = document.querySelector('#presetList');
  Object.entries(presets).forEach(([name, theme]) => {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'preset'; button.textContent = name;
    button.addEventListener('click', () => applyTheme(theme));
    list.append(button);
  });
  document.querySelectorAll('[data-theme-key]').forEach(input => input.addEventListener('input', () => {
    const theme = { ...currentTheme };
    document.querySelectorAll('[data-theme-key]').forEach(item => theme[item.dataset.themeKey] = item.value);
    applyTheme(theme);
  }));
  document.querySelector('#themeButton').addEventListener('click', () => document.querySelector('#themeDialog').showModal());
  document.querySelector('#resetTheme').addEventListener('click', () => applyTheme(presets['暖白墨绿']));
}

async function init() {
  setupTheme();
  document.querySelector('#occurredAt').value = localDateTimeValue();
  const identity = await api('/me');
  state.me = identity.me; state.participants = identity.participants;
  state.timezone = identity.timezone || state.timezone;
  const requestedAuthor = new URL(location.href).searchParams.get('author');
  state.authorId = state.participants.some(item => item.id === requestedAuthor)
    ? requestedAuthor
    : null;
  document.querySelector('#welcome').textContent = `${state.me.display_name}，今天想留下什么？`;
  renderAudience();
  renderAuthorFilters();

  document.querySelector('#visibility').addEventListener('change', event => {
    const value = event.target.value;
    document.querySelector('#audiencePanel').classList.toggle('hidden', !['selected', 'challenge'].includes(value));
    document.querySelector('#challengePanel').classList.toggle('hidden', value !== 'challenge');
  });
  document.querySelector('#saveEntry').addEventListener('click', saveEntry);
  document.querySelector('#exportButton').addEventListener('click', () => {
    const link = document.createElement('a');
    link.href = `${apiBase}/export`;
    link.download = '';
    document.body.append(link);
    link.click();
    link.remove();
  });
  document.querySelector('#refreshButton').addEventListener('click', loadTimeline);
  await loadTimeline();
}

init().catch(error => {
  document.querySelector('#welcome').textContent = `翻页失败：${error.message}`;
});
