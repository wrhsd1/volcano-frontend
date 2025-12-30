/**
 * 火山视频生成前端 - 主应用脚本
 */

// ======================== 配置 ========================

const API_BASE = '/api';

// 分辨率像素值 (Seedance 1.5 Pro)
const RESOLUTION_PIXELS = {
    '480p': {
        '16:9': [864, 496],
        '4:3': [752, 560],
        '1:1': [640, 640],
        '3:4': [560, 752],
        '9:16': [496, 864],
        '21:9': [992, 432]
    },
    '720p': {
        '16:9': [1280, 720],
        '4:3': [1112, 834],
        '1:1': [960, 960],
        '3:4': [834, 1112],
        '9:16': [720, 1280],
        '21:9': [1470, 630]
    }
};

// 价格 (元/千tokens)
const PRICE_WITH_AUDIO = 0.0160;
const PRICE_WITHOUT_AUDIO = 0.0080;

// ======================== 状态 ========================

let token = localStorage.getItem('auth_token');
let accounts = [];
let selectedAccountId = null;
let tasks = [];
let selectedTaskId = null;
let selectedTaskIds = new Set();  // 批量选择的任务ID
let firstFrameData = null;  // base64 or url
let lastFrameData = null;
let pollInterval = null;

// ======================== 初始化 ========================

document.addEventListener('DOMContentLoaded', () => {
    initApp();
});

function initApp() {
    // 绑定事件
    bindEvents();

    // 检查登录状态
    if (token) {
        showMainView();
    } else {
        showLoginView();
    }
}

function bindEvents() {
    // 登录表单
    document.getElementById('login-form').addEventListener('submit', handleLogin);

    // 退出按钮
    document.getElementById('logout-btn').addEventListener('click', handleLogout);

    // 导航按钮
    document.querySelectorAll('.nav-btn').forEach(btn => {
        btn.addEventListener('click', () => switchSection(btn.dataset.view));
    });

    // 文件上传 - 首帧
    document.getElementById('first-frame-file').addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (file) handleFileSelect(file, 'first-frame');
    });

    // 文件上传 - 尾帧
    document.getElementById('last-frame-file').addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (file) handleFileSelect(file, 'last-frame');
    });

    // URL 输入
    document.getElementById('first-frame-url').addEventListener('input', () => {
        const url = document.getElementById('first-frame-url').value.trim();
        if (url) {
            firstFrameData = { type: 'url', value: url };
            showUrlPreview('first-frame', url);
        } else if (!firstFrameData || firstFrameData.type === 'url') {
            firstFrameData = null;
            hidePreview('first-frame');
        }
        updateGenerationType();
        updateEstimate();
    });

    document.getElementById('last-frame-url').addEventListener('input', () => {
        const url = document.getElementById('last-frame-url').value.trim();
        if (url) {
            lastFrameData = { type: 'url', value: url };
            showUrlPreview('last-frame', url);
        } else if (!lastFrameData || lastFrameData.type === 'url') {
            lastFrameData = null;
            hidePreview('last-frame');
        }
        updateGenerationType();
        updateEstimate();
    });

    // 参数变化
    document.getElementById('ratio').addEventListener('change', updateEstimate);
    document.getElementById('resolution').addEventListener('change', updateEstimate);
    document.getElementById('duration').addEventListener('input', () => {
        document.getElementById('duration-value').textContent = `${document.getElementById('duration').value}秒`;
        updateEstimate();
    });
    document.getElementById('video-count').addEventListener('change', updateEstimate);
    document.getElementById('generate-audio').addEventListener('change', updateEstimate);
    document.getElementById('prompt-input').addEventListener('input', () => {
        updateGenerationType();
        updateEstimate();
    });

    // 生成按钮
    document.getElementById('generate-btn').addEventListener('click', handleGenerate);

    // 队列刷新
    document.getElementById('refresh-queue-btn').addEventListener('click', loadTasks);
    document.getElementById('queue-account-filter').addEventListener('change', loadTasks);
    document.getElementById('queue-status-filter').addEventListener('change', loadTasks);

    // 批量操作
    document.getElementById('select-all-tasks').addEventListener('change', handleSelectAll);
    document.getElementById('batch-delete-btn').addEventListener('click', handleBatchDelete);

    // 任务详情
    document.getElementById('close-detail').addEventListener('click', () => {
        document.getElementById('task-detail').style.display = 'none';
        selectedTaskId = null;
    });
    document.getElementById('sync-task-btn').addEventListener('click', syncSelectedTask);
    document.getElementById('delete-task-btn').addEventListener('click', deleteSelectedTask);

    // 添加账户
    document.getElementById('add-account-btn').addEventListener('click', showAddAccountModal);

    // 模态框关闭
    document.querySelector('.modal-close').addEventListener('click', closeModal);
    document.getElementById('modal-overlay').addEventListener('click', (e) => {
        if (e.target === e.currentTarget) closeModal();
    });
}

// ======================== 视图切换 ========================

function showLoginView() {
    document.getElementById('login-view').classList.add('active');
    document.getElementById('main-view').classList.remove('active');
}

function showMainView() {
    document.getElementById('login-view').classList.remove('active');
    document.getElementById('main-view').classList.add('active');
    loadAccounts();
    loadTasks();
    startPolling();
}

function switchSection(sectionName) {
    // 更新导航按钮
    document.querySelectorAll('.nav-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.view === sectionName);
    });

    // 切换内容区
    document.querySelectorAll('.section').forEach(section => {
        section.classList.remove('active');
    });
    document.getElementById(`${sectionName}-section`).classList.add('active');

    // 根据页面加载数据
    if (sectionName === 'queue') {
        loadTasks();
    } else if (sectionName === 'settings') {
        loadAccountsConfig();
    }
}

// ======================== 认证 ========================

async function handleLogin(e) {
    e.preventDefault();
    const password = document.getElementById('password-input').value;
    const errorEl = document.getElementById('login-error');

    try {
        const resp = await fetch(`${API_BASE}/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password })
        });

        const data = await resp.json();

        if (resp.ok && data.ok) {
            token = data.token;
            localStorage.setItem('auth_token', token);
            errorEl.textContent = '';
            showMainView();
        } else {
            errorEl.textContent = data.detail || '登录失败';
        }
    } catch (err) {
        errorEl.textContent = '网络错误';
    }
}

function handleLogout() {
    token = null;
    localStorage.removeItem('auth_token');
    stopPolling();
    showLoginView();
}

function authHeaders() {
    return {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
    };
}

// ======================== 文件上传 ========================

function handleFileSelect(file, prefix) {
    if (!file.type.startsWith('image/')) {
        showToast('请选择图片文件', 'error');
        return;
    }

    const reader = new FileReader();
    reader.onload = (e) => {
        const base64 = e.target.result;

        if (prefix === 'first-frame') {
            firstFrameData = { type: 'base64', value: base64 };
            document.getElementById('first-frame-url').value = '';
        } else {
            lastFrameData = { type: 'base64', value: base64 };
            document.getElementById('last-frame-url').value = '';
        }

        showPreview(prefix, base64);
        updateGenerationType();
        updateEstimate();
    };
    reader.readAsDataURL(file);
}

function showPreview(prefix, src) {
    const placeholder = document.getElementById(`${prefix}-placeholder`);
    const previewContainer = document.getElementById(`${prefix}-preview-container`);
    const img = document.getElementById(`${prefix}-img`);

    placeholder.style.display = 'none';
    previewContainer.style.display = 'block';
    img.src = src;
}

function showUrlPreview(prefix, url) {
    // 对于 URL，也显示预览
    showPreview(prefix, url);
}

function hidePreview(prefix) {
    const placeholder = document.getElementById(`${prefix}-placeholder`);
    const previewContainer = document.getElementById(`${prefix}-preview-container`);

    placeholder.style.display = 'flex';
    previewContainer.style.display = 'none';
}

function clearImage(prefix) {
    if (prefix === 'first-frame') {
        firstFrameData = null;
        document.getElementById('first-frame-file').value = '';
        document.getElementById('first-frame-url').value = '';
    } else {
        lastFrameData = null;
        document.getElementById('last-frame-file').value = '';
        document.getElementById('last-frame-url').value = '';
    }

    hidePreview(prefix);
    updateGenerationType();
    updateEstimate();
}

function previewImage(prefix) {
    const img = document.getElementById(`${prefix}-img`);
    if (!img.src) return;

    // 创建预览模态框
    const modal = document.createElement('div');
    modal.className = 'image-preview-modal';
    modal.innerHTML = `<img src="${img.src}" alt="预览">`;
    modal.onclick = () => modal.remove();

    document.body.appendChild(modal);
}

// 暴露到全局
window.clearImage = clearImage;
window.previewImage = previewImage;

// ======================== 生成类型检测 ========================

function updateGenerationType() {
    const prompt = document.getElementById('prompt-input').value.trim();
    const hasFirstFrame = !!firstFrameData;
    const hasLastFrame = !!lastFrameData;

    let type = '待检测';

    if (hasLastFrame && !hasFirstFrame) {
        type = '❌ 缺失首帧';
    } else if (hasFirstFrame && hasLastFrame) {
        type = '🖼️ 首尾帧生成';
    } else if (hasFirstFrame) {
        type = '🖼️ 首帧生成';
    } else if (prompt) {
        type = '📝 文生视频';
    }

    document.getElementById('generation-type').textContent = type;

    // 更新生成按钮状态
    updateGenerateButton();
}

function updateGenerateButton() {
    const btn = document.getElementById('generate-btn');
    const prompt = document.getElementById('prompt-input').value.trim();
    const hasFirstFrame = !!firstFrameData;
    const hasLastFrame = !!lastFrameData;

    let canGenerate = selectedAccountId !== null;

    // 检查输入完整性
    if (hasLastFrame && !hasFirstFrame) {
        canGenerate = false; // 缺失首帧
    } else if (!hasFirstFrame && !prompt) {
        canGenerate = false; // 文生视频需要提示词
    }

    btn.disabled = !canGenerate;
}

// ======================== Token 预估 ========================

function calculateTokens(resolution, ratio, duration, fps = 24) {
    if (!RESOLUTION_PIXELS[resolution] || !RESOLUTION_PIXELS[resolution][ratio]) {
        resolution = '720p';
        ratio = '16:9';
    }

    const [width, height] = RESOLUTION_PIXELS[resolution][ratio];
    // 正确公式: width * height * fps * duration / 1024
    const tokens = Math.floor(width * height * fps * duration / 1024);

    return tokens;
}

function updateEstimate() {
    const resolution = document.getElementById('resolution').value;
    const ratio = document.getElementById('ratio').value;
    const duration = parseInt(document.getElementById('duration').value);
    const videoCount = parseInt(document.getElementById('video-count').value);
    const hasAudio = document.getElementById('generate-audio').checked;

    const tokensPerVideo = calculateTokens(resolution, ratio, duration);
    const totalTokens = tokensPerVideo * videoCount;

    // 根据是否有声音选择价格
    const priceRate = hasAudio ? PRICE_WITH_AUDIO : PRICE_WITHOUT_AUDIO;
    const price = (totalTokens / 1000 * priceRate).toFixed(4);

    document.getElementById('estimated-tokens').textContent = totalTokens.toLocaleString();
    document.getElementById('price-type').textContent = hasAudio ? '有声' : '无声';
    document.getElementById('estimated-price').textContent = `¥${price}`;
}

// ======================== 账户管理 ========================

async function loadAccounts() {
    try {
        const resp = await fetch(`${API_BASE}/accounts`, {
            headers: authHeaders()
        });

        if (resp.status === 401) {
            handleLogout();
            return;
        }

        accounts = await resp.json();
        renderAccountList();
        updateAccountFilters();
    } catch (err) {
        console.error('加载账户失败:', err);
    }
}

function renderAccountList() {
    const container = document.getElementById('account-list');

    if (accounts.length === 0) {
        container.innerHTML = '<div class="loading">暂无账户，请先在设置中添加</div>';
        return;
    }

    container.innerHTML = accounts.map(account => {
        const percentage = account.remaining_tokens / account.daily_limit * 100;
        let quotaClass = 'remaining';
        if (percentage < 20) quotaClass = 'empty';
        else if (percentage < 50) quotaClass = 'low';

        return `
            <div class="account-item ${selectedAccountId === account.id ? 'selected' : ''}" 
                 onclick="selectAccount(${account.id})">
                <div class="account-info">
                    <div class="account-name">${account.name}</div>
                    <div class="account-quota">
                        今日剩余: <span class="${quotaClass}">${(account.remaining_tokens / 10000).toFixed(1)}万</span> / ${(account.daily_limit / 10000).toFixed(0)}万
                    </div>
                </div>
            </div>
        `;
    }).join('');

    // 如果未选择账户，默认选择第一个
    if (selectedAccountId === null && accounts.length > 0) {
        selectAccount(accounts[0].id);
    }
}

function selectAccount(accountId) {
    selectedAccountId = accountId;
    renderAccountList();
    updateGenerateButton();
}

window.selectAccount = selectAccount;

function updateAccountFilters() {
    const filter = document.getElementById('queue-account-filter');
    filter.innerHTML = '<option value="">全部账户</option>' +
        accounts.map(a => `<option value="${a.id}">${a.name}</option>`).join('');
}

async function loadAccountsConfig() {
    await loadAccounts();

    const container = document.getElementById('accounts-config-list');

    if (accounts.length === 0) {
        container.innerHTML = '<div class="loading">暂无账户</div>';
        return;
    }

    container.innerHTML = accounts.map(account => {
        const percentage = (account.remaining_tokens / account.daily_limit) * 100;
        let barClass = '';
        if (percentage < 20) barClass = 'danger';
        else if (percentage < 50) barClass = 'warning';

        return `
            <div class="account-config-card glass">
                <div class="account-config-header">
                    <h4>${account.name}</h4>
                    <div>
                        <button class="btn btn-ghost btn-sm" onclick="editAccount(${account.id})">编辑</button>
                        <button class="btn btn-danger btn-sm" onclick="deleteAccount(${account.id})">删除</button>
                    </div>
                </div>
                <div class="account-config-info">
                    <div class="account-config-row">
                        <span class="label">Model ID</span>
                        <span class="value">${account.model_id}</span>
                    </div>
                    <div class="account-config-row">
                        <span class="label">API Key</span>
                        <span class="value masked">********</span>
                    </div>
                    <div class="account-quota-bar">
                        <div class="quota-bar">
                            <div class="fill ${barClass}" style="width: ${percentage}%"></div>
                        </div>
                        <div class="quota-text">
                            今日剩余: ${account.remaining_tokens.toLocaleString()} / ${account.daily_limit.toLocaleString()} tokens
                        </div>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

function showAddAccountModal() {
    showModal('添加账户', `
        <div class="form-group">
            <label>账户名称</label>
            <input type="text" id="modal-account-name" placeholder="如：账户1">
        </div>
        <div class="form-group">
            <label>Model ID</label>
            <input type="text" id="modal-model-id" placeholder="如：ep-20251229122405-zxz8f">
        </div>
        <div class="form-group">
            <label>API Key</label>
            <input type="password" id="modal-api-key" placeholder="火山方舟 API Key">
        </div>
    `, [
        { text: '取消', class: 'btn-ghost', action: closeModal },
        { text: '添加', class: 'btn-primary', action: createAccount }
    ]);
}

async function createAccount() {
    const name = document.getElementById('modal-account-name').value.trim();
    const model_id = document.getElementById('modal-model-id').value.trim();
    const api_key = document.getElementById('modal-api-key').value.trim();

    if (!name || !model_id || !api_key) {
        showToast('请填写所有字段', 'error');
        return;
    }

    try {
        const resp = await fetch(`${API_BASE}/accounts`, {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify({ name, model_id, api_key })
        });

        if (resp.ok) {
            closeModal();
            showToast('账户添加成功', 'success');
            loadAccounts();
            loadAccountsConfig();
        } else {
            const data = await resp.json();
            showToast(data.detail || '添加失败', 'error');
        }
    } catch (err) {
        showToast('网络错误', 'error');
    }
}

async function deleteAccount(accountId) {
    if (!confirm('确定删除此账户？相关任务记录也会被删除。')) return;

    try {
        const resp = await fetch(`${API_BASE}/accounts/${accountId}`, {
            method: 'DELETE',
            headers: authHeaders()
        });

        if (resp.ok) {
            showToast('账户已删除', 'success');
            if (selectedAccountId === accountId) {
                selectedAccountId = null;
            }
            loadAccounts();
            loadAccountsConfig();
        } else {
            const data = await resp.json();
            showToast(data.detail || '删除失败', 'error');
        }
    } catch (err) {
        showToast('网络错误', 'error');
    }
}

window.deleteAccount = deleteAccount;

function editAccount(accountId) {
    const account = accounts.find(a => a.id === accountId);
    if (!account) return;

    showModal('编辑账户', `
        <div class="form-group">
            <label>账户名称</label>
            <input type="text" id="modal-account-name" value="${account.name}">
        </div>
        <div class="form-group">
            <label>Model ID</label>
            <input type="text" id="modal-model-id" value="${account.model_id}">
        </div>
        <div class="form-group">
            <label>API Key (留空保持不变)</label>
            <input type="password" id="modal-api-key" placeholder="新的 API Key">
        </div>
    `, [
        { text: '取消', class: 'btn-ghost', action: closeModal },
        { text: '保存', class: 'btn-primary', action: () => updateAccount(accountId) }
    ]);
}

window.editAccount = editAccount;

async function updateAccount(accountId) {
    const name = document.getElementById('modal-account-name').value.trim();
    const model_id = document.getElementById('modal-model-id').value.trim();
    const api_key = document.getElementById('modal-api-key').value.trim();

    const body = { name, model_id };
    if (api_key) body.api_key = api_key;

    try {
        const resp = await fetch(`${API_BASE}/accounts/${accountId}`, {
            method: 'PUT',
            headers: authHeaders(),
            body: JSON.stringify(body)
        });

        if (resp.ok) {
            closeModal();
            showToast('账户已更新', 'success');
            loadAccounts();
            loadAccountsConfig();
        } else {
            const data = await resp.json();
            showToast(data.detail || '更新失败', 'error');
        }
    } catch (err) {
        showToast('网络错误', 'error');
    }
}

// ======================== 任务管理 ========================

async function handleGenerate() {
    const prompt = document.getElementById('prompt-input').value.trim();
    const hasFirstFrame = !!firstFrameData;
    const hasLastFrame = !!lastFrameData;

    // 验证
    if (hasLastFrame && !hasFirstFrame) {
        showToast('缺失首帧图片：仅提供尾帧时必须同时提供首帧', 'error');
        return;
    }

    if (!hasFirstFrame && !prompt) {
        showToast('文生视频模式需要提供提示词', 'error');
        return;
    }

    if (!selectedAccountId) {
        showToast('请选择账户', 'error');
        return;
    }

    const btn = document.getElementById('generate-btn');
    btn.disabled = true;
    btn.innerHTML = '<span class="btn-icon">⏳</span><span>提交中...</span>';

    try {
        const body = {
            account_id: selectedAccountId,
            prompt: prompt || null,
            ratio: document.getElementById('ratio').value,
            resolution: document.getElementById('resolution').value,
            duration: parseInt(document.getElementById('duration').value),
            video_count: parseInt(document.getElementById('video-count').value),
            generate_audio: document.getElementById('generate-audio').checked,
            seed: parseInt(document.getElementById('seed').value) || -1,
            watermark: document.getElementById('watermark').checked,
            camera_fixed: document.getElementById('camera-fixed').checked
        };

        // 添加图片
        if (firstFrameData) {
            if (firstFrameData.type === 'base64') {
                body.first_frame_base64 = firstFrameData.value;
            } else {
                body.first_frame_url = firstFrameData.value;
            }
        }

        if (lastFrameData) {
            if (lastFrameData.type === 'base64') {
                body.last_frame_base64 = lastFrameData.value;
            } else {
                body.last_frame_url = lastFrameData.value;
            }
        }

        const resp = await fetch(`${API_BASE}/tasks`, {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify(body)
        });

        if (resp.ok) {
            const tasks = await resp.json();
            showToast(`成功创建 ${tasks.length} 个任务`, 'success');

            // 刷新账户额度和任务列表
            loadAccounts();

            // 切换到队列页面
            switchSection('queue');
        } else {
            const data = await resp.json();
            showToast(data.detail || '创建任务失败', 'error');
        }
    } catch (err) {
        console.error(err);
        showToast('网络错误', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<span class="btn-icon">🚀</span><span>生成视频</span>';
        updateGenerateButton();
    }
}

async function loadTasks() {
    const accountFilter = document.getElementById('queue-account-filter').value;
    const statusFilter = document.getElementById('queue-status-filter').value;

    let url = `${API_BASE}/tasks?limit=50`;
    if (accountFilter) url += `&account_id=${accountFilter}`;
    if (statusFilter) url += `&status=${statusFilter}`;

    try {
        const resp = await fetch(url, {
            headers: authHeaders()
        });

        if (resp.status === 401) {
            handleLogout();
            return;
        }

        const data = await resp.json();
        tasks = data.tasks || [];
        renderTaskList();
    } catch (err) {
        console.error('加载任务失败:', err);
    }
}

function renderTaskList() {
    const container = document.getElementById('task-list');

    if (tasks.length === 0) {
        container.innerHTML = '<div class="loading">暂无任务</div>';
        updateBatchUI();
        return;
    }

    container.innerHTML = tasks.map(task => {
        const statusMap = {
            'queued': '排队中',
            'running': '进行中',
            'succeeded': '已完成',
            'failed': '失败',
            'cancelled': '已取消',
            'expired': '已过期'
        };

        const typeMap = {
            'text_to_video': '文生视频',
            'first_frame': '首帧生成',
            'first_last_frame': '首尾帧生成'
        };

        const isSelected = selectedTaskIds.has(task.task_id);

        return `
            <div class="task-item ${selectedTaskId === task.task_id ? 'selected' : ''} ${isSelected ? 'batch-selected' : ''}">
                <div class="task-item-content">
                    <input type="checkbox" class="task-checkbox" 
                           data-task-id="${task.task_id}"
                           ${isSelected ? 'checked' : ''}
                           onclick="toggleTaskSelection(event, '${task.task_id}')">
                    <div class="task-info" onclick="selectTask('${task.task_id}')">
                        <div class="task-id">${task.task_id}</div>
                        <div class="task-meta">
                            <span>${task.account_name || '未知账户'}</span>
                            <span>${typeMap[task.generation_type] || task.generation_type || '-'}</span>
                            <span>${formatTime(task.created_at)}</span>
                        </div>
                    </div>
                    <span class="task-status ${task.status}">${statusMap[task.status] || task.status}</span>
                </div>
            </div>
        `;
    }).join('');

    updateBatchUI();
}

function selectTask(taskId) {
    selectedTaskId = taskId;
    renderTaskList();
    showTaskDetail(taskId);
}

window.selectTask = selectTask;

async function showTaskDetail(taskId) {
    const task = tasks.find(t => t.task_id === taskId);
    if (!task) return;

    const detailPanel = document.getElementById('task-detail');
    detailPanel.style.display = 'block';

    const typeMap = {
        'text_to_video': '文生视频',
        'first_frame': '首帧生成',
        'first_last_frame': '首尾帧生成'
    };

    const statusMap = {
        'queued': '排队中',
        'running': '进行中',
        'succeeded': '已完成',
        'failed': '失败',
        'cancelled': '已取消',
        'expired': '已过期'
    };

    document.getElementById('detail-task-id').textContent = task.task_id;
    document.getElementById('detail-account').textContent = task.account_name || '未知';
    document.getElementById('detail-type').textContent = typeMap[task.generation_type] || task.generation_type || '-';
    document.getElementById('detail-status').textContent = statusMap[task.status] || task.status;
    document.getElementById('detail-tokens').textContent = task.token_usage ? task.token_usage.toLocaleString() : '-';
    document.getElementById('detail-created').textContent = formatTime(task.created_at);

    // 错误信息
    const errorRow = document.getElementById('detail-error-row');
    if (task.error_message) {
        errorRow.style.display = 'flex';
        document.getElementById('detail-error').textContent = task.error_message;
    } else {
        errorRow.style.display = 'none';
    }

    // 视频预览
    const videoContainer = document.getElementById('detail-video-container');
    const downloadBtn = document.getElementById('download-video-btn');

    if (task.result_url) {
        videoContainer.style.display = 'block';
        document.getElementById('detail-video').src = task.result_url;
        downloadBtn.href = task.result_url;
        downloadBtn.style.display = 'inline-flex';
    } else {
        videoContainer.style.display = 'none';
        downloadBtn.style.display = 'none';
    }
}

async function syncSelectedTask() {
    if (!selectedTaskId) return;

    try {
        const resp = await fetch(`${API_BASE}/tasks/${selectedTaskId}/sync`, {
            method: 'POST',
            headers: authHeaders()
        });

        if (resp.ok) {
            const task = await resp.json();
            // 更新列表中的任务
            const idx = tasks.findIndex(t => t.task_id === selectedTaskId);
            if (idx !== -1) {
                tasks[idx] = task;
                renderTaskList();
                showTaskDetail(selectedTaskId);
            }
            showToast('状态已刷新', 'success');
        }
    } catch (err) {
        showToast('刷新失败', 'error');
    }
}

async function deleteSelectedTask() {
    if (!selectedTaskId) return;
    if (!confirm('确定删除此任务？')) return;

    try {
        const resp = await fetch(`${API_BASE}/tasks/${selectedTaskId}`, {
            method: 'DELETE',
            headers: authHeaders()
        });

        if (resp.ok) {
            showToast('任务已删除', 'success');
            document.getElementById('task-detail').style.display = 'none';
            selectedTaskId = null;
            loadTasks();
        } else {
            const data = await resp.json();
            showToast(data.detail || '删除失败', 'error');
        }
    } catch (err) {
        showToast('网络错误', 'error');
    }
}

// ======================== 批量操作 ========================

function toggleTaskSelection(event, taskId) {
    event.stopPropagation();  // 防止触发任务选择

    if (selectedTaskIds.has(taskId)) {
        selectedTaskIds.delete(taskId);
    } else {
        selectedTaskIds.add(taskId);
    }

    renderTaskList();
}

function handleSelectAll(event) {
    if (event.target.checked) {
        // 全选
        tasks.forEach(task => selectedTaskIds.add(task.task_id));
    } else {
        // 取消全选
        selectedTaskIds.clear();
    }

    renderTaskList();
}

function updateBatchUI() {
    const count = selectedTaskIds.size;
    document.getElementById('selected-count').textContent = `已选择 ${count} 项`;
    document.getElementById('batch-delete-btn').disabled = count === 0;

    // 更新全选复选框状态
    const selectAllCheckbox = document.getElementById('select-all-tasks');
    if (tasks.length > 0 && count === tasks.length) {
        selectAllCheckbox.checked = true;
        selectAllCheckbox.indeterminate = false;
    } else if (count > 0) {
        selectAllCheckbox.checked = false;
        selectAllCheckbox.indeterminate = true;
    } else {
        selectAllCheckbox.checked = false;
        selectAllCheckbox.indeterminate = false;
    }
}

async function handleBatchDelete() {
    const count = selectedTaskIds.size;
    if (count === 0) return;

    if (!confirm(`确定删除选中的 ${count} 个任务？`)) return;

    let successCount = 0;
    let failCount = 0;

    for (const taskId of selectedTaskIds) {
        try {
            const resp = await fetch(`${API_BASE}/tasks/${taskId}`, {
                method: 'DELETE',
                headers: authHeaders()
            });

            if (resp.ok) {
                successCount++;
            } else {
                failCount++;
            }
        } catch (err) {
            failCount++;
        }
    }

    // 清空选择
    selectedTaskIds.clear();
    document.getElementById('task-detail').style.display = 'none';
    selectedTaskId = null;

    // 刷新列表
    await loadTasks();

    if (failCount === 0) {
        showToast(`成功删除 ${successCount} 个任务`, 'success');
    } else {
        showToast(`删除完成：成功 ${successCount} 个，失败 ${failCount} 个`, 'warning');
    }
}

// 暴露到全局
window.toggleTaskSelection = toggleTaskSelection;

// ======================== 轮询 ========================

function startPolling() {
    if (pollInterval) return;

    // 记录任务状态，用于检测变化
    let previousStatuses = {};

    pollInterval = setInterval(async () => {
        // 检查是否有进行中的任务
        const runningTasks = tasks.filter(t => t.status === 'queued' || t.status === 'running');

        if (runningTasks.length > 0) {
            console.log(`[轮询] 同步 ${runningTasks.length} 个进行中的任务...`);

            // 记录当前状态
            tasks.forEach(t => {
                previousStatuses[t.task_id] = t.status;
            });

            // 同步所有进行中的任务
            for (const task of runningTasks) {
                try {
                    await fetch(`${API_BASE}/tasks/${task.task_id}/sync`, {
                        method: 'POST',
                        headers: authHeaders()
                    });
                } catch (err) {
                    console.error(`同步任务 ${task.task_id} 失败:`, err);
                }
            }

            // 重新加载任务列表
            await loadTasks();

            // 检查状态变化，显示通知
            tasks.forEach(t => {
                const prevStatus = previousStatuses[t.task_id];
                if (prevStatus && prevStatus !== t.status) {
                    if (t.status === 'succeeded') {
                        showToast(`任务 ${t.task_id.slice(-8)} 已完成！`, 'success');
                    } else if (t.status === 'failed') {
                        showToast(`任务 ${t.task_id.slice(-8)} 失败`, 'error');
                    }
                }
            });

            // 如果当前在队列页面且有选中任务，更新详情
            if (document.getElementById('queue-section').classList.contains('active') && selectedTaskId) {
                showTaskDetail(selectedTaskId);
            }
        }
    }, 5000);
}

function stopPolling() {
    if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
    }
}

// ======================== 模态框 ========================

function showModal(title, content, buttons) {
    document.getElementById('modal-title').textContent = title;
    document.getElementById('modal-content').innerHTML = content;

    const footer = document.getElementById('modal-footer');
    footer.innerHTML = buttons.map(btn =>
        `<button class="btn ${btn.class}" data-action="${btn.text}">${btn.text}</button>`
    ).join('');

    footer.querySelectorAll('button').forEach(el => {
        const btn = buttons.find(b => b.text === el.dataset.action);
        if (btn && btn.action) {
            el.addEventListener('click', btn.action);
        }
    });

    document.getElementById('modal-overlay').classList.add('active');
}

function closeModal() {
    document.getElementById('modal-overlay').classList.remove('active');
}

// ======================== Toast 通知 ========================

function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;

    container.appendChild(toast);

    setTimeout(() => {
        toast.remove();
    }, 3000);
}

// ======================== 工具函数 ========================

function formatTime(isoString) {
    if (!isoString) return '-';
    const date = new Date(isoString);
    return date.toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
    });
}
