const form = document.querySelector('#pair');
const result = document.querySelector('#result');
const status = document.querySelector('#status');
const button = document.querySelector('#generate');
document.querySelector('#device').value = 'phone-' + crypto.randomUUID().slice(0, 8);
let imageUrl, timer;
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  clearInterval(timer);
  result.hidden = true;
  if (imageUrl) URL.revokeObjectURL(imageUrl);
  button.disabled = true;
  status.textContent = '正在生成…';
  const password = document.querySelector('#password');
  try {
    const response = await fetch('/pairings', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({password: password.value, device: document.querySelector('#device').value}),
    });
    password.value = '';
    const errors = {401: '管理密码不正确。', 409: '设备名已存在，请换一个新名称。', 429: '请求太频繁，请一分钟后再试。', 503: '配对功能尚未配置。', 422: '请检查设备名称。'};
    if (!response.ok) throw new Error(errors[response.status] || '服务暂不可用，请稍后再试。');
    const data = await response.json();
    imageUrl = URL.createObjectURL(new Blob([data.qr_svg], {type: 'image/svg+xml'}));
    document.querySelector('#qr').src = imageUrl;
    result.hidden = false;
    status.textContent = '用 App 扫描下方二维码，配对设备：' + data.device;
    const deadline = Date.now() + data.expires_in * 1000;
    const tick = () => {
      const seconds = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      document.querySelector('#expiry').textContent = '有效期剩余 ' + seconds + ' 秒 · 仅可使用一次';
      if (!seconds) { clearInterval(timer); result.hidden = true; status.textContent = '二维码已过期，请重新生成。'; }
    };
    tick(); timer = setInterval(tick, 1000);
  } catch (error) { password.value = ''; status.textContent = error.message || '网络异常，请重试。'; }
  finally { button.disabled = false; }
});
