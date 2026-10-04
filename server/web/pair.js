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
  const password = document.querySelector('#password');
  try {
    const response = await fetch('/pairings', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({password: password.value, device: device.value}),
    });
    password.value = '';
    const errors = {401: '管理密码不正确，请重新输入。', 409: '设备名已存在，请换一个新名称。', 429: '请求太频繁，请一分钟后再试。', 503: '配对功能尚未配置，请检查服务器设置。', 422: '请检查设备名称，只能使用字母、数字、短横线和下划线。'};
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
        showStatus('二维码已过期。重新输入管理密码，再生成一个。', 'expired');
      }
    };
    tick();
    if (Date.now() < deadline) timer = setInterval(tick, 1000);
  } catch (error) {
    password.value = '';
    showStatus(error instanceof TypeError ? '无法连接服务，请检查网络后重试。' : error.message || '生成失败，请重试。', 'error');
  } finally {
    button.disabled = false;
    button.querySelector('span').textContent = '生成配对二维码';
  }
});
