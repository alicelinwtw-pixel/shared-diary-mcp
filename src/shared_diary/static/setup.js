const adminKey = location.pathname.split('/').filter(Boolean)[1];
const apiRoot = `/admin-api/${encodeURIComponent(adminKey)}`;
const list = document.querySelector('#participant-list');
const keyCard = document.querySelector('#new-key');
const statusLine = document.querySelector('#form-status');

async function request(path, options = {}) {
  const response = await fetch(`${apiRoot}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok || !payload.ok) throw new Error(payload.error || '请求失败');
  return payload;
}

function showKey(payload) {
  document.querySelector('#diary-url').value = payload.diary_url;
  document.querySelector('#mcp-url').value = payload.mcp_url;
  keyCard.hidden = false;
  keyCard.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

async function refresh() {
  const payload = await request('/participants');
  list.replaceChildren();
  if (!payload.participants.length) {
    list.textContent = '还没有参与者。先邀请第一位住进来吧。';
    return;
  }
  for (const person of payload.participants) {
    const row = document.createElement('div');
    row.className = 'participant-row';
    const identity = document.createElement('div');
    const name = document.createElement('strong');
    name.textContent = person.display_name;
    const kind = document.createElement('span');
    kind.className = 'muted';
    kind.textContent = person.kind === 'ai' ? 'AI' : '人类';
    identity.append(name, kind);

    const actions = document.createElement('div');
    actions.className = 'participant-actions';
    const rename = document.createElement('button');
    rename.className = 'secondary';
    rename.textContent = '改名字';
    const rotate = document.createElement('button');
    rotate.className = 'secondary';
    rotate.textContent = '换钥匙';
    rotate.addEventListener('click', async () => {
      if (!confirm(`为 ${person.display_name} 更换钥匙？旧链接会立刻失效。`)) return;
      showKey(await request(`/participants/${encodeURIComponent(person.id)}/rotate-key`, { method: 'POST' }));
    });
    rename.addEventListener('click', () => {
      const editor = document.createElement('form');
      editor.className = 'participant-name-editor';
      const input = document.createElement('input');
      input.value = person.display_name;
      input.required = true;
      input.maxLength = 60;
      input.setAttribute('aria-label', `${person.display_name} 的新名字`);
      const save = document.createElement('button');
      save.type = 'submit';
      save.textContent = '保存';
      const cancel = document.createElement('button');
      cancel.type = 'button';
      cancel.className = 'secondary';
      cancel.textContent = '取消';
      cancel.addEventListener('click', () => {
        editor.replaceWith(identity);
        actions.hidden = false;
      });
      editor.addEventListener('submit', async (event) => {
        event.preventDefault();
        statusLine.textContent = '正在改名字…';
        try {
          const payload = await request(`/participants/${encodeURIComponent(person.id)}`, {
            method: 'PATCH',
            body: JSON.stringify({ display_name: input.value }),
          });
          statusLine.textContent = `${person.display_name} 已改名为 ${payload.participant.display_name}，原来的钥匙仍然有效。`;
          await refresh();
        } catch (error) {
          statusLine.textContent = error.message;
        }
      });
      editor.append(input, save, cancel);
      identity.replaceWith(editor);
      actions.hidden = true;
      input.focus();
      input.select();
    });
    actions.append(rename, rotate);
    row.append(identity, actions);
    list.append(row);
  }
}

document.querySelector('#participant-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  statusLine.textContent = '正在制作钥匙…';
  try {
    const payload = await request('/participants', {
      method: 'POST',
      body: JSON.stringify({
        display_name: document.querySelector('#display-name').value,
        kind: document.querySelector('#kind').value,
      }),
    });
    showKey(payload);
    event.target.reset();
    statusLine.textContent = `${payload.participant.display_name} 已经入住。`;
    await refresh();
  } catch (error) {
    statusLine.textContent = error.message;
  }
});

document.querySelectorAll('[data-copy]').forEach((button) => {
  button.addEventListener('click', async () => {
    await navigator.clipboard.writeText(document.querySelector(`#${button.dataset.copy}`).value);
    const old = button.textContent;
    button.textContent = '已复制';
    setTimeout(() => { button.textContent = old; }, 1200);
  });
});

refresh().catch((error) => { list.textContent = error.message; });
