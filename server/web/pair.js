const form = document.querySelector('#pair');
const result = document.querySelector('#result');
const empty = document.querySelector('#empty');
const status = document.querySelector('#status');
const scanState = document.querySelector('#scan-state');
const button = document.querySelector('#generate');
const device = document.querySelector('#device');
const qr = document.querySelector('#qr');

device.value = 'phone-' + crypto.randomUUID().slice(0, 8);
let imageUrl, timer;

function clearQr() {
  clearInterval(timer);
  result.hidden = true;
  empty.hidden = false;
  qr.removeAttribute('src');
  if (imageUrl) URL.revokeObjectURL(imageUrl);
  imageUrl = undefined;
}

function showStatus(message, state) {
  status.textContent = message;
  status.dataset.state = state;
  scanState.dataset.state = state;
  scanState.textContent = {loading: '生成中', ready: '等待扫码', error: '等待生成', expired: '已过期'}[state];
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  clearQr();
  button.disabled = true;
  button.querySelector('span').textContent = '正在生成…';
  showStatus('正在为这台设备生成二维码…', 'loading');
  try {
    const response = await adminRequest('/pairings', {device: device.value});
    const errors = {409: '设备名已存在，请换一个新名称。', 429: '请求太频繁，请一分钟后再试。', 503: '配对功能尚未配置，请检查服务器设置。', 422: '请检查设备名称，只能使用字母、数字、短横线和下划线。'};
    if (!response.ok) throw new Error(errors[response.status] || '服务暂不可用，请稍后再试。');
    const data = await response.json();
    imageUrl = URL.createObjectURL(new Blob([data.qr_svg], {type: 'image/svg+xml'}));
    qr.src = imageUrl;
    result.hidden = false;
    empty.hidden = true;
    document.querySelector('#paired-device').textContent = data.device;
    showStatus('二维码已生成，请在有效期内用 App 扫描。', 'ready');
    const deadline = Date.now() + data.expires_in * 1000;
    const tick = () => {
      const seconds = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      const minutes = Math.floor(seconds / 60);
      document.querySelector('#expiry').textContent = `${minutes}:${String(seconds % 60).padStart(2, '0')} 后过期 · 仅可使用一次`;
      if (!seconds) {
        clearQr();
        showStatus('二维码已过期，请重新生成。', 'expired');
      }
    };
    tick();
    if (Date.now() < deadline) timer = setInterval(tick, 1000);
  } catch (error) {
    showStatus(error instanceof TypeError ? '无法连接服务，请检查网络后重试。' : error.message || '生成失败，请重试。', 'error');
  } finally {
    button.disabled = false;
    button.querySelector('span').textContent = '生成配对二维码';
  }
});

const pushForm = document.querySelector('#push-key-form');
const pushButton = document.querySelector('#create-push-key');
const pushResult = document.querySelector('#push-result');
const pushKey = document.querySelector('#push-key');
const pushStatus = document.querySelector('#push-status');

function showPushStatus(message, state) {
  pushStatus.textContent = message;
  pushStatus.dataset.state = state;
}

pushForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  pushButton.disabled = true;
  pushButton.textContent = '正在创建…';
  showPushStatus('正在创建发送密钥…', 'loading');
  try {
    const response = await adminRequest('/push-keys', {});
    const errors = {429: '请求太频繁，请一分钟后再试。', 503: '管理功能尚未配置，请检查服务器设置。'};
    if (!response.ok) throw new Error(errors[response.status] || '创建失败，请稍后再试。');
    const data = await response.json();
    pushKey.value = data.push_key;
    pushResult.hidden = false;
    showPushStatus('密钥已创建，请立即复制保存。', 'ready');
  } catch (error) {
    showPushStatus(error instanceof TypeError ? '连接中断，无法确认创建结果；如已显示旧密钥，它仍可使用。' : error.message || '创建失败，请重试。', 'error');
  } finally {
    pushButton.disabled = false;
    pushButton.textContent = 'Create push_key';
  }
});

function clearSensitiveContent() {
  clearQr();
  pushKey.value = '';
  pushResult.hidden = true;
}

function expireSession() {
  clearSensitiveContent();
  window.location.replace('/login?expired=1');
}

async function adminRequest(path, body, method = 'POST') {
  const response = await fetch(path, {
    method, credentials: 'same-origin',
    headers: {'Content-Type': 'application/json', 'X-Notify-Request': 'dashboard'},
    ...(body === undefined ? {} : {body: JSON.stringify(body)}),
  });
  if (response.status === 401) {
    expireSession();
    throw new Error('登录已过期，请重新登录。');
  }
  return response;
}

document.querySelector('#logout').addEventListener('click', async () => {
  const logout = document.querySelector('#logout');
  logout.disabled = true;
  clearSensitiveContent();
  try {
    const response = await adminRequest('/session', undefined, 'DELETE');
    if (!response.ok) throw new Error('退出失败，请重试。');
    window.location.replace('/login');
  } catch {
    document.querySelector('#account-status').textContent = '未能确认退出，请检查网络后重试。';
    logout.disabled = false;
  }
});

// Reload pages restored from browser history so the server checks the session again.
window.addEventListener('pageshow', (event) => {
  if (event.persisted) {
    clearSensitiveContent();
    window.location.reload();
  }
});

adminRequest('/session', undefined, 'GET').then(async (response) => {
  if (!response.ok) throw new Error('Session check failed');
  const data = await response.json();
  setTimeout(expireSession, data.expires_in * 1000);
}).catch(() => {
  document.querySelector('#account-status').textContent = '无法确认登录状态，请刷新页面重试。';
});

document.querySelector('#copy-push-key').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(pushKey.value);
    showPushStatus('密钥已复制。', 'ready');
  } catch {
    pushKey.focus();
    pushKey.select();
    showPushStatus('无法自动复制，已选中密钥，请手动复制。', 'error');
  }
});
