document.addEventListener('DOMContentLoaded', function () {
  // ==================== DOM 元素 ====================
  const activateView = document.getElementById('activate-view');
  const loginView = document.getElementById('login-view');

  // 激活页面元素
  const activateBtn = document.getElementById('activate-btn');
  const activateCode = document.getElementById('activate-code');
  const activateError = document.getElementById('activate-error');

  // 登录页面元素
  const fetchBtn = document.getElementById('fetch-cookie');
  const cardInput = document.getElementById('card-number');
  const errorMessage = document.getElementById('error-message');
  const changeCard = document.getElementById('change-card');

  // 更新弹窗元素
  const updateModal = document.getElementById('update-modal');
  const modalTitle = document.getElementById('modal-title');
  const modalMessage = document.getElementById('modal-message');
  const modalDownload = document.getElementById('modal-download');
  const modalClose = document.getElementById('modal-close');

  let currentDownloadUrl = '';

  // ==================== 视图切换 ====================
  function showActivateView() {
    activateView.style.display = 'flex';
    loginView.style.display = 'none';
    activateCode.value = '';
    activateError.textContent = '';
  }

  function showLoginView(cardNumber) {
    activateView.style.display = 'none';
    loginView.style.display = 'flex';
    cardInput.value = cardNumber || '';
    errorMessage.textContent = '';
  }

  // ==================== 初始化：判断显示哪个视图 ====================
  chrome.storage.local.get(['isActivated', 'cardNumber'], function (result) {
    if (result.isActivated && result.cardNumber) {
      showLoginView(result.cardNumber);
    } else {
      showActivateView();
    }
  });

  // ==================== 激活逻辑 ====================
  activateBtn.addEventListener('click', function () {
    const code = activateCode.value.trim();
    if (!code) {
      showActivateError('请输入激活码');
      return;
    }

    activateBtn.textContent = '激活中...';
    activateBtn.disabled = true;
    showActivateError('正在激活...');

    // 发送激活消息给 background，只验证卡密有效性，不设置代理和Cookie
    chrome.runtime.sendMessage({
      action: 'activate-card',
      cardNumber: code
    }, function (response) {
      activateBtn.textContent = '开始激活使用';
      activateBtn.disabled = false;

      if (response && response.success) {
        // 激活成功，存储标记并切换到登录页面
        chrome.storage.local.set({ isActivated: true, cardNumber: code }, function () {
          showLoginView(code);
        });
      } else {
        showActivateError(response ? response.error : '激活失败，请检查激活码');
      }
    });
  });

  function showActivateError(message) {
    activateError.textContent = message;
  }

  // ==================== 登录逻辑 ====================
  fetchBtn.addEventListener('click', function () {
    const cardNumber = cardInput.value.trim();
    if (!cardNumber) {
      chrome.storage.local.set({ cardNumber: '' });
      showLoginError('请输入卡密');
      return;
    }

    fetchBtn.textContent = '验证中...';
    fetchBtn.disabled = true;
    showLoginError('正在验证中...');

    // 同步存储卡密
    chrome.storage.local.set({ cardNumber: cardNumber });

    chrome.runtime.sendMessage({
      action: 'fetch-proxy-and-cookie',
      cardNumber: cardNumber
    }, function (response) {
      fetchBtn.textContent = '验证登录';
      fetchBtn.disabled = false;

      if (response && response.success) {
        if (response.hasUpdate) {
          showUpdateModal(response.newVersion, response.updateMessage, response.downloadUrl);
        } else {
          showLoginError('登录成功', 'green');
        }
      } else {
        const errMsg = response ? response.error : '验证失败';
        showLoginError(errMsg);
        // 登录失败时不再清除激活标记，激活成功一次就够了
        // 用户可通过"更换卡密"按钮主动切换
      }
    });
  });

  function showLoginError(message, color) {
    errorMessage.textContent = message;
    errorMessage.style.color = color || '#ff4757';
  }

  // ==================== 更换卡密 ====================
  changeCard.addEventListener('click', function () {
    chrome.storage.local.remove('isActivated', function () {
      showActivateView();
    });
  });

  // ==================== 更新弹窗 ====================
  function showUpdateModal(version, message, downloadUrl) {
    currentDownloadUrl = downloadUrl;
    modalTitle.textContent = '发现新版本 v' + version;
    modalMessage.textContent = message;
    updateModal.classList.add('show');
  }

  modalDownload.addEventListener('click', function () {
    if (currentDownloadUrl) {
      window.open(currentDownloadUrl, '_blank');
    }
    window.close();
  });

  modalClose.addEventListener('click', function () {
    window.close();
  });

  updateModal.addEventListener('click', function (e) {
    if (e.target === updateModal) {
      window.close();
    }
  });
});
