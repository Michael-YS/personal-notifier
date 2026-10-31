const form = document.querySelector('#login-form');
const password = document.querySelector('#password');
const button = document.querySelector('#login-button');
const status = document.querySelector('#login-status');

if (new URLSearchParams(window.location.search).has('expired')) {
  status.textContent = '登录已过期，请重新登录。';
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  button.disabled = true;
  button.textContent = '正在登录…';
  status.textContent = '';
  try {
    const response = await fetch('/session', {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-Notify-Request': 'dashboard'},
      body: JSON.stringify({password: password.value}),
    });
    const errors = {401: '管理密码不正确，请重新输入。', 429: '登录请求太频繁，请一分钟后再试。', 503: '管理功能尚未配置，请检查服务器设置。', 403: '登录请求被拒绝，请刷新页面后重试。', 422: '请检查管理密码。'};
    if (!response.ok) throw new Error(errors[response.status] || '登录失败，请稍后再试。');
    window.location.replace('/');
  } catch (error) {
    status.textContent = error instanceof TypeError ? '无法连接服务，请检查网络后重试。' : error.message;
    status.dataset.state = 'error';
    password.focus();
  } finally {
    password.value = '';
    button.disabled = false;
    button.textContent = '登录';
  }
});
