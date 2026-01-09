/**
 * 火山内容生成前端 - 主应用脚本
 * 支持视频和图片生成
 */

// ======================== 配置 ========================

const API_BASE = '/api';

// 分辨率像素值 (Seedance 1.5 Pro 视频)
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

// 视频价格 (元/千tokens)
const PRICE_WITH_AUDIO = 0.0160;
const PRICE_WITHOUT_AUDIO = 0.0080;

// 图片价格 (元/张)
const IMAGE_PRICE = 0.25;

// 图片尺寸映射 (分辨率 + 比例 -> 像素值)
const IMAGE_SIZE_MAP = {
    '2K': {
        '1:1': '2048x2048',
        '4:3': '2304x1728',
        '3:4': '1728x2304',
        '16:9': '2560x1440',
        '9:16': '1440x2560',
        '3:2': '2496x1664',
        '2:3': '1664x2496',
        '21:9': '3024x1296'
    },
    '4K': {
        '1:1': '4096x4096',
        '4:3': '4096x3072',
        '3:4': '3072x4096',
        '16:9': '4096x2304',
        '9:16': '2304x4096',
        '3:2': '4096x2730',
        '2:3': '2730x4096',
        '21:9': '4096x1755'
    }
};

// ======================== 状态 ========================

let token = localStorage.getItem('auth_token');
let userRole = localStorage.getItem('user_role') || 'admin';  // 'admin' | 'guest'
let guestId = localStorage.getItem('guest_id') || '';  // '' for admin, '1'/'2' for guests
let accounts = [];
let selectedAccountId = null;
let selectedImageAccountId = null;  // 图片模式选中的账户
let selectedBananaAccountId = null;  // Banana模式选中的账户
let tasks = [];
let selectedTaskId = null;
let selectedTaskIds = new Set();  // 批量选择的任务ID
// 图片数据结构: { type: 'uploading'|'uploaded'|'url', fileId?, localPreview?, progress?, value? }
let firstFrameData = null;
let lastFrameData = null;
let referenceImages = [];  // 图片生成参考图列表: { name, localPreview, type, fileId?, progress? }
let bananaReferenceImages = [];  // Banana参考图列表
let currentMode = 'video';  // 'video' | 'image' | 'banana'
let pollInterval = null;

// ======================== 文件上传 ========================

/**
 * 计算文件的 SHA-256 hash
 * @param {File} file - 文件对象
 * @returns {Promise<string>} - 十六进制 hash 字符串
 */
async function calculateFileHash(file) {
    const buffer = await file.arrayBuffer();
    const hashBuffer = await crypto.subtle.digest('SHA-256', buffer);
    return Array.from(new Uint8Array(hashBuffer))
        .map(b => b.toString(16).padStart(2, '0')).join('');
}

/**
 * 上传文件到服务器 (支持秒传)
 * @param {File} file - 要上传的文件
 * @param {Function} onProgress - 进度回调 (0-100)
 * @returns {Promise<{ok: boolean, file_id: string, filename: string, size: number}>}
 */
async function uploadFile(file, onProgress) {
    // 先计算 hash 尝试秒传
    try {
        const fileHash = await calculateFileHash(file);

        // 检查服务器是否已有此文件
        const checkResp = await fetch(`${API_BASE}/upload/check`, {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify({ hash: fileHash, filename: file.name })
        });

        if (checkResp.ok) {
            const checkData = await checkResp.json();
            if (checkData.exists && checkData.file_id) {
                // 秒传成功
                console.log(`[秒传] 文件已存在: ${checkData.file_id}`);
                if (onProgress) onProgress(100);
                return {
                    ok: true,
                    file_id: checkData.file_id,
                    filename: checkData.filename || file.name,
                    size: file.size
                };
            }
        }
    } catch (e) {
        console.warn('Hash检查失败，继续正常上传:', e);
    }

    // 正常上传
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        const formData = new FormData();
        formData.append('file', file);

        xhr.upload.onprogress = (e) => {
            if (e.lengthComputable) {
                const percent = Math.round(e.loaded / e.total * 100);
                if (onProgress) onProgress(percent);
            }
        };

        xhr.onload = () => {
            if (xhr.status === 200) {
                try {
                    const data = JSON.parse(xhr.responseText);
                    resolve(data);
                } catch (e) {
                    reject(new Error('解析响应失败'));
                }
            } else {
                let errorMsg = '上传失败';
                try {
                    const data = JSON.parse(xhr.responseText);
                    errorMsg = data.detail || errorMsg;
                } catch (e) { }
                reject(new Error(errorMsg));
            }
        };

        xhr.onerror = () => {
            reject(new Error('网络错误'));
        };

        xhr.open('POST', `${API_BASE}/upload`);
        xhr.setRequestHeader('Authorization', `Bearer ${token}`);
        xhr.send(formData);
    });
}


/**
 * 删除已上传的文件
 * @param {string} fileId - 文件ID
 */
async function deleteUploadedFile(fileId) {
    if (!fileId) return;
    try {
        await fetch(`${API_BASE}/upload/${fileId}`, {
            method: 'DELETE',
            headers: authHeaders()
        });
    } catch (e) {
        console.warn('删除上传文件失败:', e);
    }
}

/**
 * 检查是否有图片正在上传
 */
function hasUploadingImages() {
    if (firstFrameData?.type === 'uploading') return true;
    if (lastFrameData?.type === 'uploading') return true;
    if (referenceImages.some(img => img.type === 'uploading')) return true;
    if (bananaReferenceImages.some(img => img.type === 'uploading')) return true;
    return false;
}

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

    // 模式切换选项卡
    document.querySelectorAll('.mode-tab').forEach(tab => {
        tab.addEventListener('click', () => switchMode(tab.dataset.mode));
    });

    // ======== 视频生成事件 ========

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

    // ======== 图片生成事件 ========

    // 参考图片上传
    document.getElementById('ref-images-file').addEventListener('change', handleRefImagesSelect);

    // 组图模式切换
    document.getElementById('sequential-mode').addEventListener('change', (e) => {
        const maxImagesGroup = document.getElementById('max-images-group');
        const imageCountGroup = document.getElementById('image-count-group');
        if (e.target.checked) {
            maxImagesGroup.style.display = 'block';
            imageCountGroup.style.display = 'none';
        } else {
            maxImagesGroup.style.display = 'none';
            imageCountGroup.style.display = 'block';
        }
        updateImageEstimate();
    });

    // 组图数量滑块
    document.getElementById('max-images').addEventListener('input', () => {
        document.getElementById('max-images-value').textContent = `${document.getElementById('max-images').value}张`;
        updateImageEstimate();
    });

    // 生成张数
    document.getElementById('image-count').addEventListener('change', updateImageEstimate);

    // 分辨率和比例
    document.getElementById('image-resolution').addEventListener('change', updateImageResolutionDisplay);
    document.getElementById('image-ratio').addEventListener('change', updateImageResolutionDisplay);

    // 提示词输入
    document.getElementById('image-prompt-input').addEventListener('input', () => {
        updateImageGenerationType();
        updateImageGenerateButton();
    });

    // 图片生成按钮
    document.getElementById('image-generate-btn').addEventListener('click', handleImageGenerate);

    // ======== Banana生图事件 ========

    // Banana参考图片上传
    document.getElementById('banana-ref-images-file').addEventListener('change', handleBananaRefImagesSelect);

    // Banana提示词输入
    document.getElementById('banana-prompt-input').addEventListener('input', () => {
        updateBananaGenerationType();
        updateBananaGenerateButton();
    });

    // Banana生成按钮
    document.getElementById('banana-generate-btn').addEventListener('click', handleBananaGenerate);

    // ======== 队列事件 ========

    // 队列刷新
    document.getElementById('refresh-queue-btn').addEventListener('click', loadTasks);
    document.getElementById('queue-type-filter').addEventListener('change', loadTasks);
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

    // 根据角色更新UI
    updateUIForRole();

    loadAccounts();
    loadTasks();
    startPolling();
}

function updateUIForRole() {
    // 访客隐藏设置按钮
    const settingsBtn = document.querySelector('.nav-btn[data-view="settings"]');
    if (settingsBtn) {
        settingsBtn.style.display = userRole === 'admin' ? '' : 'none';
    }
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

function switchMode(mode) {
    currentMode = mode;

    // 更新选项卡样式
    document.querySelectorAll('.mode-tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.mode === mode);
    });

    // 切换面板
    document.querySelectorAll('.mode-panel').forEach(panel => {
        panel.classList.remove('active');
    });
    document.getElementById(`${mode}-panel`).classList.add('active');

    // 重新渲染账户列表
    renderAccountList();
    renderImageAccountList();
    renderBananaAccountList();

    // 模式特殊初始化
    if (mode === 'banana') {
        loadBananaStorage();
    } else if (mode === 'image') {
        loadVolcanoStorage();
    } else if (mode === 'video') {
        loadVideoStorage();
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
            userRole = data.role || 'admin';
            guestId = data.guest_id || '';
            localStorage.setItem('auth_token', token);
            localStorage.setItem('user_role', userRole);
            localStorage.setItem('guest_id', guestId);
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
    userRole = 'admin';
    guestId = '';
    localStorage.removeItem('auth_token');
    localStorage.removeItem('user_role');
    localStorage.removeItem('guest_id');
    stopPolling();
    showLoginView();
}

function authHeaders() {
    return {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
    };
}

// ======================== 文件上传 (视频) ========================

async function handleFileSelect(file, prefix) {
    if (!file.type.startsWith('image/')) {
        showToast('请选择图片文件', 'error');
        return;
    }

    // 先读取本地预览
    const reader = new FileReader();
    reader.onload = async (e) => {
        const localPreview = e.target.result;

        // 设置为上传中状态
        if (prefix === 'first-frame') {
            firstFrameData = { type: 'uploading', localPreview, progress: 0 };
            document.getElementById('first-frame-url').value = '';
        } else {
            lastFrameData = { type: 'uploading', localPreview, progress: 0 };
            document.getElementById('last-frame-url').value = '';
        }

        showPreviewWithProgress(prefix, localPreview, 0);
        updateGenerationType();
        updateGenerateButton();

        // 开始上传
        try {
            const result = await uploadFile(file, (progress) => {
                // 更新进度
                if (prefix === 'first-frame' && firstFrameData?.type === 'uploading') {
                    firstFrameData.progress = progress;
                } else if (prefix === 'last-frame' && lastFrameData?.type === 'uploading') {
                    lastFrameData.progress = progress;
                }
                updateProgressBar(prefix, progress);
            });

            // 上传成功
            if (prefix === 'first-frame') {
                firstFrameData = { type: 'uploaded', fileId: result.file_id, localPreview };
            } else {
                lastFrameData = { type: 'uploaded', fileId: result.file_id, localPreview };
            }

            hideProgressBar(prefix);
            updateGenerateButton();
            showToast(`${prefix === 'first-frame' ? '首帧' : '尾帧'}上传完成`, 'success');

        } catch (err) {
            showToast(`上传失败: ${err.message}`, 'error');
            // 清除状态
            if (prefix === 'first-frame') {
                firstFrameData = null;
            } else {
                lastFrameData = null;
            }
            hidePreview(prefix);
            updateGenerationType();
            updateGenerateButton();
        }
    };
    reader.readAsDataURL(file);
}

function showPreviewWithProgress(prefix, src, progress) {
    const placeholder = document.getElementById(`${prefix}-placeholder`);
    const previewContainer = document.getElementById(`${prefix}-preview-container`);
    const img = document.getElementById(`${prefix}-img`);

    placeholder.style.display = 'none';
    previewContainer.style.display = 'block';
    previewContainer.classList.add('uploading');
    img.src = src;

    // 添加进度条
    let progressBar = previewContainer.querySelector('.upload-progress');
    if (!progressBar) {
        progressBar = document.createElement('div');
        progressBar.className = 'upload-progress';
        progressBar.innerHTML = '<div class="upload-progress-bar" style="width: 0%"></div>';
        previewContainer.appendChild(progressBar);
    }
    progressBar.querySelector('.upload-progress-bar').style.width = `${progress}%`;
}

function updateProgressBar(prefix, progress) {
    const previewContainer = document.getElementById(`${prefix}-preview-container`);
    const progressBar = previewContainer?.querySelector('.upload-progress-bar');
    if (progressBar) {
        progressBar.style.width = `${progress}%`;
    }
}

function hideProgressBar(prefix) {
    const previewContainer = document.getElementById(`${prefix}-preview-container`);
    if (previewContainer) {
        previewContainer.classList.remove('uploading');
        const progressBar = previewContainer.querySelector('.upload-progress');
        if (progressBar) {
            progressBar.remove();
        }
    }
}

function showPreview(prefix, src) {
    const placeholder = document.getElementById(`${prefix}-placeholder`);
    const previewContainer = document.getElementById(`${prefix}-preview-container`);
    const img = document.getElementById(`${prefix}-img`);

    placeholder.style.display = 'none';
    previewContainer.style.display = 'block';
    previewContainer.classList.remove('uploading');
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
    previewContainer.classList.remove('uploading');
}

async function clearImage(prefix) {
    let fileIdToDelete = null;

    if (prefix === 'first-frame') {
        fileIdToDelete = firstFrameData?.fileId;
        firstFrameData = null;
        document.getElementById('first-frame-file').value = '';
        document.getElementById('first-frame-url').value = '';
    } else {
        fileIdToDelete = lastFrameData?.fileId;
        lastFrameData = null;
        document.getElementById('last-frame-file').value = '';
        document.getElementById('last-frame-url').value = '';
    }

    // 删除服务端文件
    if (fileIdToDelete) {
        deleteUploadedFile(fileIdToDelete);
    }

    hidePreview(prefix);
    hideProgressBar(prefix);
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

// ======================== 参考图片上传 (图片生成) ========================

function handleRefImagesSelect(e) {
    const files = Array.from(e.target.files);

    if (referenceImages.length + files.length > 14) {
        showToast('参考图片最多14张', 'error');
        return;
    }

    files.forEach(file => {
        if (!file.type.startsWith('image/')) {
            showToast(`${file.name} 不是图片文件`, 'error');
            return;
        }

        const index = referenceImages.length;

        // 先读取本地预览
        const reader = new FileReader();
        reader.onload = async (ev) => {
            const localPreview = ev.target.result;

            // 添加为上传中状态
            referenceImages.push({
                name: file.name,
                localPreview: localPreview,
                type: 'uploading',
                progress: 0
            });

            renderRefImages();
            updateImageGenerationType();
            updateImageGenerateButton();

            // 开始上传
            const currentIndex = referenceImages.findIndex(
                img => img.localPreview === localPreview && img.type === 'uploading'
            );

            try {
                const result = await uploadFile(file, (progress) => {
                    if (currentIndex >= 0 && referenceImages[currentIndex]) {
                        referenceImages[currentIndex].progress = progress;
                        renderRefImages();
                    }
                });

                // 上传成功
                if (currentIndex >= 0 && referenceImages[currentIndex]) {
                    referenceImages[currentIndex].type = 'uploaded';
                    referenceImages[currentIndex].fileId = result.file_id;
                    delete referenceImages[currentIndex].progress;
                }

                renderRefImages();
                updateImageGenerateButton();

            } catch (err) {
                showToast(`上传失败: ${err.message}`, 'error');
                // 移除失败的图片
                const failIndex = referenceImages.findIndex(
                    img => img.localPreview === localPreview && img.type === 'uploading'
                );
                if (failIndex >= 0) {
                    referenceImages.splice(failIndex, 1);
                }
                renderRefImages();
                updateImageGenerateButton();
            }
        };
        reader.readAsDataURL(file);
    });

    // 清空input以便重复选择相同文件
    e.target.value = '';
}

function renderRefImages() {
    const container = document.getElementById('ref-images-container');

    // 清空现有预览
    container.innerHTML = '';

    // 添加已有图片
    referenceImages.forEach((img, index) => {
        const item = document.createElement('div');
        item.className = 'ref-image-item' + (img.type === 'uploading' ? ' uploading' : '');

        let progressHtml = '';
        if (img.type === 'uploading') {
            progressHtml = `<div class="upload-progress"><div class="upload-progress-bar" style="width: ${img.progress || 0}%"></div></div>`;
        }

        const src = img.localPreview || img.data;
        item.innerHTML = `
            <img src="${src}" alt="${img.name}">
            <button type="button" class="ref-image-remove" onclick="removeRefImage(${index})">✕</button>
            ${progressHtml}
        `;
        container.appendChild(item);
    });

    // 添加"添加"按钮
    if (referenceImages.length < 14) {
        const addDiv = document.createElement('div');
        addDiv.className = 'ref-image-add';
        addDiv.id = 'ref-image-add';
        addDiv.onclick = () => document.getElementById('ref-images-file').click();
        addDiv.innerHTML = `
            <span class="add-icon">+</span>
            <span class="add-text">添加</span>
        `;
        container.appendChild(addDiv);
    }
}

async function removeRefImage(index) {
    const img = referenceImages[index];
    if (img?.fileId) {
        deleteUploadedFile(img.fileId);
    }
    referenceImages.splice(index, 1);
    renderRefImages();
    updateImageGenerationType();
    updateImageEstimate();
}

window.removeRefImage = removeRefImage;

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

    // 检查是否有图片正在上传
    const isUploading = hasUploadingImages();
    if (isUploading) {
        canGenerate = false;
        btn.classList.add('uploading-blocked');
    } else {
        btn.classList.remove('uploading-blocked');
    }

    // 检查账户是否有视频model_id
    if (canGenerate) {
        const account = accounts.find(a => a.id === selectedAccountId);
        if (!account || !account.video_model_id) {
            canGenerate = false;
        }
    }

    // 检查输入完整性
    if (hasLastFrame && !hasFirstFrame) {
        canGenerate = false; // 缺失首帧
    } else if (!hasFirstFrame && !prompt) {
        canGenerate = false; // 文生视频需要提示词
    }

    btn.disabled = !canGenerate;
}

function updateImageGenerationType() {
    const hasImages = referenceImages.length > 0;
    let type = '纯文生图';

    if (hasImages) {
        if (referenceImages.length > 1) {
            type = `多图融合 (${referenceImages.length}张)`;
        } else {
            type = '单图参考';
        }
    }

    document.getElementById('image-generation-type').textContent = type;
}

function updateImageGenerateButton() {
    const btn = document.getElementById('image-generate-btn');
    const prompt = document.getElementById('image-prompt-input').value.trim();

    let canGenerate = selectedImageAccountId !== null && prompt.length > 0;

    // 检查是否有图片正在上传
    const isUploading = referenceImages.some(img => img.type === 'uploading');
    if (isUploading) {
        canGenerate = false;
        btn.classList.add('uploading-blocked');
    } else {
        btn.classList.remove('uploading-blocked');
    }

    // 检查账户是否有图片model_id
    if (canGenerate) {
        const account = accounts.find(a => a.id === selectedImageAccountId);
        if (!account || !account.image_model_id) {
            canGenerate = false;
        }
    }

    btn.disabled = !canGenerate;
}

function updateImageResolutionDisplay() {
    const resolution = document.getElementById('image-resolution').value;
    const ratio = document.getElementById('image-ratio').value;

    const sizeValue = getImageSizeValue(resolution, ratio);
    const displayValue = sizeValue.replace('x', '×');

    document.getElementById('image-resolution-display').textContent = displayValue;
}

function getImageSizeValue(resolution, ratio) {
    // 根据分辨率和比例返回实际像素值
    if (IMAGE_SIZE_MAP[resolution] && IMAGE_SIZE_MAP[resolution][ratio]) {
        return IMAGE_SIZE_MAP[resolution][ratio];
    }
    // 默认返回2K 1:1
    return '2048x2048';
}

// ======================== Token/价格 预估 ========================

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

function updateImageEstimate() {
    const isSequential = document.getElementById('sequential-mode').checked;
    let count;

    if (isSequential) {
        count = parseInt(document.getElementById('max-images').value);
    } else {
        count = parseInt(document.getElementById('image-count').value);
    }

    const price = (count * IMAGE_PRICE).toFixed(2);

    document.getElementById('estimated-images').textContent = count;
    document.getElementById('image-estimated-price').textContent = `¥${price}`;
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
        renderImageAccountList();
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

    // 过滤有视频model_id的账户
    const videoAccounts = accounts.filter(a => a.video_model_id);

    if (videoAccounts.length === 0) {
        container.innerHTML = '<div class="loading">暂无配置视频端点的账户</div>';
        return;
    }

    container.innerHTML = videoAccounts.map(account => {
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
                        今日剩余: <span class="${quotaClass}">${(account.remaining_tokens / 10000).toFixed(1)}万</span> / ${(account.daily_limit / 10000).toFixed(0)}万 tokens
                    </div>
                </div>
            </div>
        `;
    }).join('');

    // 如果未选择账户，默认选择第一个有视频能力的
    if (selectedAccountId === null && videoAccounts.length > 0) {
        selectAccount(videoAccounts[0].id);
    }
}

function renderImageAccountList() {
    const container = document.getElementById('image-account-list');

    if (accounts.length === 0) {
        container.innerHTML = '<div class="loading">暂无账户，请先在设置中添加</div>';
        return;
    }

    // 过滤有图片model_id的账户
    const imageAccounts = accounts.filter(a => a.image_model_id);

    if (imageAccounts.length === 0) {
        container.innerHTML = '<div class="loading">暂无配置图片端点的账户</div>';
        return;
    }

    container.innerHTML = imageAccounts.map(account => {
        const percentage = account.remaining_images / account.daily_image_limit * 100;
        let quotaClass = 'remaining';
        if (percentage < 20) quotaClass = 'empty';
        else if (percentage < 50) quotaClass = 'low';

        return `
            <div class="account-item ${selectedImageAccountId === account.id ? 'selected' : ''}" 
                 onclick="selectImageAccount(${account.id})">
                <div class="account-info">
                    <div class="account-name">${account.name}</div>
                    <div class="account-quota">
                        今日剩余: <span class="${quotaClass}">${account.remaining_images}</span> / ${account.daily_image_limit} 张
                    </div>
                </div>
            </div>
        `;
    }).join('');

    // 如果未选择账户，默认选择第一个有图片能力的
    if (selectedImageAccountId === null && imageAccounts.length > 0) {
        selectImageAccount(imageAccounts[0].id);
    }
}

function selectAccount(accountId) {
    selectedAccountId = accountId;
    renderAccountList();
    updateGenerateButton();
}

function selectImageAccount(accountId) {
    selectedImageAccountId = accountId;
    renderImageAccountList();
    updateImageGenerateButton();
}

window.selectAccount = selectAccount;
window.selectImageAccount = selectImageAccount;

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
        const tokenPercentage = (account.remaining_tokens / account.daily_limit) * 100;
        const imagePercentage = (account.remaining_images / account.daily_image_limit) * 100;

        let tokenBarClass = '';
        if (tokenPercentage < 20) tokenBarClass = 'danger';
        else if (tokenPercentage < 50) tokenBarClass = 'warning';

        let imageBarClass = '';
        if (imagePercentage < 20) imageBarClass = 'danger';
        else if (imagePercentage < 50) imageBarClass = 'warning';

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
                        <span class="label">视频端点ID</span>
                        <span class="value">${account.video_model_id || '<span class="text-muted">未配置</span>'}</span>
                    </div>
                    <div class="account-config-row">
                        <span class="label">图片端点ID</span>
                        <span class="value">${account.image_model_id || '<span class="text-muted">未配置</span>'}</span>
                    </div>
                    <div class="account-config-row">
                        <span class="label">API Key</span>
                        <span class="value masked">********</span>
                    </div>
                    <div class="account-quota-bar">
                        <div class="quota-label">视频配额</div>
                        <div class="quota-bar">
                            <div class="fill ${tokenBarClass}" style="width: ${tokenPercentage}%"></div>
                        </div>
                        <div class="quota-text">
                            ${account.remaining_tokens.toLocaleString()} / ${account.daily_limit.toLocaleString()} tokens
                        </div>
                    </div>
                    <div class="account-quota-bar">
                        <div class="quota-label">图片配额</div>
                        <div class="quota-bar">
                            <div class="fill ${imageBarClass}" style="width: ${imagePercentage}%"></div>
                        </div>
                        <div class="quota-text">
                            ${account.remaining_images} / ${account.daily_image_limit} 张
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
            <label>视频端点ID <span class="optional">(Seedance 1.5 Pro)</span></label>
            <input type="text" id="modal-video-model-id" placeholder="如：ep-20251229122405-zxz8f">
        </div>
        <div class="form-group">
            <label>图片端点ID <span class="optional">(Seedream 4.5)</span></label>
            <input type="text" id="modal-image-model-id" placeholder="如：ep-20251229122405-abc12">
        </div>
        <div class="form-group">
            <label>火山 API Key</label>
            <input type="password" id="modal-api-key" placeholder="火山方舟 API Key">
        </div>
        <hr style="border: none; border-top: 1px solid rgba(255,255,255,0.1); margin: 16px 0;">
        <p class="hint" style="margin-bottom: 12px;">🍌 Banana (Gemini) 配置 (可选)</p>
        <div class="form-group">
            <label>Banana Base URL</label>
            <input type="text" id="modal-banana-base-url" placeholder="如：https://generativelanguage.googleapis.com">
        </div>
        <div class="form-group">
            <label>Banana API Key</label>
            <input type="password" id="modal-banana-api-key" placeholder="Gemini API Key">
        </div>
        <div class="form-group">
            <label>Banana 模型名</label>
            <input type="text" id="modal-banana-model-name" placeholder="默认：gemini-3-pro-image-preview">
        </div>
        <p class="hint">至少需要填写一个端点ID（视频或图片）或 Banana 配置</p>
    `, [
        { text: '取消', class: 'btn-ghost', action: closeModal },
        { text: '添加', class: 'btn-primary', action: createAccount }
    ]);
}

async function createAccount() {
    const name = document.getElementById('modal-account-name').value.trim();
    const video_model_id = document.getElementById('modal-video-model-id').value.trim() || null;
    const image_model_id = document.getElementById('modal-image-model-id').value.trim() || null;
    const api_key = document.getElementById('modal-api-key').value.trim();
    const banana_base_url = document.getElementById('modal-banana-base-url').value.trim() || null;
    const banana_api_key = document.getElementById('modal-banana-api-key').value.trim() || null;
    const banana_model_name = document.getElementById('modal-banana-model-name').value.trim() || null;

    if (!name || !api_key) {
        showToast('请填写账户名称和API Key', 'error');
        return;
    }

    if (!video_model_id && !image_model_id && !banana_base_url) {
        showToast('至少需要填写一个端点ID或Banana配置', 'error');
        return;
    }

    try {
        const resp = await fetch(`${API_BASE}/accounts`, {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify({
                name, video_model_id, image_model_id, api_key,
                banana_base_url, banana_api_key, banana_model_name
            })
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
            if (selectedImageAccountId === accountId) {
                selectedImageAccountId = null;
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
            <label>视频端点ID <span class="optional">(Seedance 1.5 Pro)</span></label>
            <input type="text" id="modal-video-model-id" value="${account.video_model_id || ''}" placeholder="如：ep-20251229122405-zxz8f">
        </div>
        <div class="form-group">
            <label>图片端点ID <span class="optional">(Seedream 4.5)</span></label>
            <input type="text" id="modal-image-model-id" value="${account.image_model_id || ''}" placeholder="如：ep-20251229122405-abc12">
        </div>
        <div class="form-group">
            <label>火山 API Key (留空保持不变)</label>
            <input type="password" id="modal-api-key" placeholder="新的 API Key">
        </div>
        <hr style="border: none; border-top: 1px solid rgba(255,255,255,0.1); margin: 16px 0;">
        <p class="hint" style="margin-bottom: 12px;">🍌 Banana (Gemini) 配置</p>
        <div class="form-group">
            <label>Banana Base URL</label>
            <input type="text" id="modal-banana-base-url" value="${account.banana_base_url || ''}" placeholder="如：https://generativelanguage.googleapis.com">
        </div>
        <div class="form-group">
            <label>Banana API Key (留空保持不变)</label>
            <input type="password" id="modal-banana-api-key" placeholder="新的 Gemini API Key">
        </div>
        <div class="form-group">
            <label>Banana 模型名</label>
            <input type="text" id="modal-banana-model-name" value="${account.banana_model_name || ''}" placeholder="默认：gemini-3-pro-image-preview">
        </div>
    `, [
        { text: '取消', class: 'btn-ghost', action: closeModal },
        { text: '保存', class: 'btn-primary', action: () => updateAccount(accountId) }
    ]);
}

window.editAccount = editAccount;

async function updateAccount(accountId) {
    const name = document.getElementById('modal-account-name').value.trim();
    const video_model_id = document.getElementById('modal-video-model-id').value.trim() || null;
    const image_model_id = document.getElementById('modal-image-model-id').value.trim() || null;
    const api_key = document.getElementById('modal-api-key').value.trim();
    const banana_base_url = document.getElementById('modal-banana-base-url').value.trim() || null;
    const banana_api_key = document.getElementById('modal-banana-api-key').value.trim();
    const banana_model_name = document.getElementById('modal-banana-model-name').value.trim() || null;

    const body = { name, video_model_id, image_model_id, banana_base_url, banana_model_name };
    if (api_key) body.api_key = api_key;
    if (banana_api_key) body.banana_api_key = banana_api_key;

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

// ======================== 视频任务生成 ========================

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

        // 添加图片 (使用 file_id 或 URL)
        if (firstFrameData) {
            if (firstFrameData.type === 'uploaded' && firstFrameData.fileId) {
                body.first_frame_file_id = firstFrameData.fileId;
            } else if (firstFrameData.type === 'url') {
                body.first_frame_url = firstFrameData.value;
            }
        }

        if (lastFrameData) {
            if (lastFrameData.type === 'uploaded' && lastFrameData.fileId) {
                body.last_frame_file_id = lastFrameData.fileId;
            } else if (lastFrameData.type === 'url') {
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
            showToast(`成功创建 ${tasks.length} 个视频任务`, 'success');

            // 刷新账户额度和任务列表
            loadAccounts();

            // 不再跳转，留在当前页面方便继续提交
            // switchSection('queue');
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

// ======================== 图片任务生成 ========================

async function handleImageGenerate() {
    const prompt = document.getElementById('image-prompt-input').value.trim();

    if (!prompt) {
        showToast('请输入图片描述', 'error');
        return;
    }

    if (!selectedImageAccountId) {
        showToast('请选择账户', 'error');
        return;
    }

    const btn = document.getElementById('image-generate-btn');
    btn.disabled = true;
    btn.innerHTML = '<span class="btn-icon">⏳</span><span>生成中...</span>';

    try {
        const isSequential = document.getElementById('sequential-mode').checked;
        const resolution = document.getElementById('image-resolution').value;
        const ratio = document.getElementById('image-ratio').value;
        const optimizePrompt = document.getElementById('optimize-prompt').checked;

        const body = {
            account_id: selectedImageAccountId,
            prompt: prompt,
            size: getImageSizeValue(resolution, ratio),
            watermark: document.getElementById('image-watermark').checked,
            sequential_image_generation: isSequential ? 'auto' : 'disabled',
            optimize_prompt: optimizePrompt,
        };

        if (isSequential) {
            body.max_images = parseInt(document.getElementById('max-images').value);
        } else {
            body.count = parseInt(document.getElementById('image-count').value);
        }

        // 添加参考图片 (使用 file_id)
        if (referenceImages.length > 0) {
            const uploadedFileIds = referenceImages
                .filter(img => img.type === 'uploaded' && img.fileId)
                .map(img => img.fileId);
            if (uploadedFileIds.length > 0) {
                body.file_ids = uploadedFileIds;
            }
        }

        const resp = await fetch(`${API_BASE}/images`, {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify(body)
        });

        if (resp.ok) {
            const createdTasks = await resp.json();
            showToast(`已提交 ${createdTasks.length} 个图片任务，正在生成中...`, 'success');

            // 不再跳转，留在当前页面方便继续提交
            // switchSection('queue');
        } else {
            const data = await resp.json();
            showToast(data.detail || '生成失败', 'error');
        }
    } catch (err) {
        console.error(err);
        showToast('网络错误', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<span class="btn-icon">🎨</span><span>生成图片</span>';
        updateImageGenerateButton();
    }
}

// ======================== 任务管理 ========================

async function loadTasks() {
    const typeFilter = document.getElementById('queue-type-filter').value;
    const accountFilter = document.getElementById('queue-account-filter').value;
    const statusFilter = document.getElementById('queue-status-filter').value;

    let url = `${API_BASE}/tasks?limit=50`;
    if (typeFilter) url += `&task_type=${typeFilter}`;
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
            'first_last_frame': '首尾帧生成',
            'text_to_image': '文生图',
            'image_to_image': '图生图',
            'multi_image': '多图融合',
            'continue': '多轮修改'
        };

        const taskTypeIcon = task.task_type === 'video' ? '🎬' : (task.task_type === 'banana_image' ? '🍌' : '🖼️');

        const isSelected = selectedTaskIds.has(task.task_id);

        return `
            <div class="task-item ${selectedTaskId === task.task_id ? 'selected' : ''} ${isSelected ? 'batch-selected' : ''}">
                <div class="task-item-content">
                    <input type="checkbox" class="task-checkbox" 
                           data-task-id="${task.task_id}"
                           ${isSelected ? 'checked' : ''}
                           onclick="toggleTaskSelection(event, '${task.task_id}')">
                    <div class="task-info" onclick="selectTask('${task.task_id}')">
                        <div class="task-id">
                            <span class="task-type-icon">${taskTypeIcon}</span>
                            ${task.task_id}
                        </div>
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
        'first_last_frame': '首尾帧生成',
        'text_to_image': '文生图',
        'image_to_image': '图生图',
        'multi_image': '多图融合'
    };

    const statusMap = {
        'queued': '排队中',
        'running': '进行中',
        'succeeded': '已完成',
        'failed': '失败',
        'cancelled': '已取消',
        'expired': '已过期'
    };

    const taskTypeMap = {
        'video': '🎬 视频',
        'image': '🖼️ 图片'
    };

    document.getElementById('detail-task-id').textContent = task.task_id;
    document.getElementById('detail-task-type').textContent = taskTypeMap[task.task_type] || task.task_type;
    document.getElementById('detail-account').textContent = task.account_name || '未知';
    document.getElementById('detail-type').textContent = typeMap[task.generation_type] || task.generation_type || '-';
    document.getElementById('detail-status').textContent = statusMap[task.status] || task.status;

    // 显示提交者 (仅管理员可见)
    const submitterRow = document.getElementById('detail-submitter-row');
    if (userRole === 'admin' && submitterRow) {
        submitterRow.style.display = 'flex';
        const submitter = task.submitted_by || 'admin';
        // 映射提交者标识到可读名称
        let submitterLabel = submitter;
        if (submitter === 'admin') {
            submitterLabel = '🔑 管理员';
        } else if (submitter.startsWith('guest_')) {
            const gid = submitter.replace('guest_', '');
            submitterLabel = `👤 访客 ${gid}`;
        }
        document.getElementById('detail-submitter').textContent = submitterLabel;
    } else if (submitterRow) {
        submitterRow.style.display = 'none';
    }

    // 提取并显示提示词
    const promptRow = document.getElementById('detail-prompt-row');
    const promptEl = document.getElementById('detail-prompt');
    let prompt = '';

    if (task.params) {
        try {
            const params = JSON.parse(task.params);
            // 视频任务的 prompt 在 content 数组中
            if (task.task_type === 'video' && params.content) {
                const textContent = params.content.find(c => c.type === 'text');
                if (textContent) {
                    prompt = textContent.text || '';
                }
            } else if (params.prompt) {
                // 图片任务直接有 prompt 字段
                prompt = params.prompt;
            }
        } catch (e) {
            console.error('解析params失败:', e);
        }
    }

    if (prompt) {
        promptRow.style.display = 'flex';
        // 截断过长的prompt
        const maxLen = 200;
        if (prompt.length > maxLen) {
            promptEl.textContent = prompt.substring(0, maxLen) + '...';
            promptEl.title = prompt;  // 完整内容显示在hover提示中
        } else {
            promptEl.textContent = prompt;
            promptEl.title = '';
        }
    } else {
        promptRow.style.display = 'none';
    }

    // 提取并显示参考图
    const refImagesRow = document.getElementById('detail-ref-images-row');
    const refImagesContainer = document.getElementById('detail-ref-images');
    let refImages = [];

    if (task.params) {
        try {
            const params = JSON.parse(task.params);
            if (task.task_type === 'video') {
                // 视频任务：优先从 frame_paths 提取本地保存的帧
                if (params.frame_paths) {
                    if (params.frame_paths.first_frame) {
                        const filename = params.frame_paths.first_frame.split(/[/\\]/).pop();
                        refImages.push({
                            url: `${API_BASE}/tasks/video/frame/${task.task_id}/${filename}`,
                            label: '首帧'
                        });
                    }
                    if (params.frame_paths.last_frame) {
                        const filename = params.frame_paths.last_frame.split(/[/\\]/).pop();
                        refImages.push({
                            url: `${API_BASE}/tasks/video/frame/${task.task_id}/${filename}`,
                            label: '尾帧'
                        });
                    }
                }
                // URL 方式的帧
                if (params.first_frame_url) {
                    refImages.push({ url: params.first_frame_url, label: '首帧' });
                }
                if (params.last_frame_url) {
                    refImages.push({ url: params.last_frame_url, label: '尾帧' });
                }
                // 旧格式：从 content 数组中提取
                if (params.content) {
                    params.content.forEach(item => {
                        if (item.type === 'image_url' && item.image_url && item.image_url.url) {
                            // 跳过 base64，因为已经在 frame_paths 中处理了
                            if (!item.image_url.url.startsWith('data:')) {
                                refImages.push({
                                    url: item.image_url.url,
                                    label: item.role === 'first_frame' ? '首帧' : (item.role === 'last_frame' ? '尾帧' : '参考')
                                });
                            }
                        }
                    });
                }
            } else if (params.ref_image_paths && params.ref_image_paths.length > 0) {
                // 优化的图片任务：使用本地参考图
                params.ref_image_paths.forEach((path, idx) => {
                    const filename = path.split(/[/\\]/).pop();
                    // 根据任务类型选择正确的 API 端点
                    let url;
                    if (task.task_type === 'banana_image') {
                        url = `${API_BASE}/banana/images/file/${task.task_id}/${filename}`;
                    } else {
                        url = `${API_BASE}/images/file/${task.task_id}/${filename}`;
                    }
                    refImages.push({ url, label: `参考${idx + 1}` });
                });
            } else if (params.image) {
                // 旧版图片任务：从 image 字段提取
                if (Array.isArray(params.image)) {
                    params.image.forEach((url, idx) => {
                        refImages.push({ url, label: `参考${idx + 1}` });
                    });
                } else {
                    refImages.push({ url: params.image, label: '参考图' });
                }
            }
        } catch (e) {
            console.error('解析参考图失败:', e);
        }
    }

    if (refImages.length > 0) {
        refImagesRow.style.display = 'flex';
        refImagesContainer.innerHTML = refImages.map(img => {
            // 判断是否是base64（过长不显示完整）
            const isBase64 = img.url.startsWith('data:');
            const displayUrl = isBase64 ? img.url : img.url;
            return `<div class="ref-image-thumb" title="${img.label}">
                <img src="${displayUrl}" alt="${img.label}" onclick="window.open('${isBase64 ? '' : img.url}', '_blank')">
                <span class="ref-label">${img.label}</span>
            </div>`;
        }).join('');
    } else {
        refImagesRow.style.display = 'none';
    }

    document.getElementById('detail-created').textContent = formatTime(task.created_at);

    // Token/图片数量显示
    const tokensRow = document.getElementById('detail-tokens-row');
    const imagesRow = document.getElementById('detail-images-row');

    if (task.task_type === 'video') {
        tokensRow.style.display = 'flex';
        imagesRow.style.display = 'none';
        document.getElementById('detail-tokens').textContent = task.token_usage ? task.token_usage.toLocaleString() : '-';
    } else {
        tokensRow.style.display = 'none';
        imagesRow.style.display = 'flex';
        document.getElementById('detail-images-count').textContent = task.image_count || '-';
    }

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
    const imagesContainer = document.getElementById('detail-images-container');

    if (task.task_type === 'video' && task.result_url) {
        videoContainer.style.display = 'block';
        imagesContainer.style.display = 'none';
        document.getElementById('detail-video').src = task.result_url;
        downloadBtn.href = task.result_url;
        downloadBtn.style.display = 'inline-flex';
    } else if (task.task_type === 'image' && task.result_urls) {
        videoContainer.style.display = 'none';
        imagesContainer.style.display = 'block';
        downloadBtn.style.display = 'none';

        try {
            const images = JSON.parse(task.result_urls);
            const grid = document.getElementById('detail-images-grid');
            grid.innerHTML = images.map((img, idx) => {
                if (img.error) {
                    return `<div class="image-result-item error">
                        <span class="error-icon">❌</span>
                        <span>${img.error}</span>
                    </div>`;
                }
                const url = img.url || '';
                return `<div class="image-result-item">
                    <img src="${url}" alt="图片${idx + 1}" onclick="window.open('${url}', '_blank')">
                    <a href="${url}" target="_blank" class="download-link" title="下载">⬇️</a>
                </div>`;
            }).join('');
        } catch (e) {
            console.error('解析图片结果失败:', e);
        }
    } else if (task.task_type === 'banana_image' && task.result_urls) {
        // Banana 图片 - 使用本地文件路径
        videoContainer.style.display = 'none';
        imagesContainer.style.display = 'block';
        downloadBtn.style.display = 'none';

        try {
            const images = JSON.parse(task.result_urls);
            const grid = document.getElementById('detail-images-grid');
            grid.innerHTML = images.map((img, idx) => {
                // 从本地路径提取文件名，构建 API URL
                const filepath = img.path || '';
                const filename = filepath.split(/[/\\]/).pop();
                const imageUrl = `${API_BASE}/banana/images/file/${task.task_id}/${filename}`;

                return `<div class="image-result-item">
                    <img src="${imageUrl}" alt="Banana图片${idx + 1}" onclick="window.open('${imageUrl}', '_blank')">
                    <a href="${imageUrl}" download="${filename}" class="download-link" title="下载">⬇️</a>
                </div>`;
            }).join('');
        } catch (e) {
            console.error('解析Banana图片结果失败:', e);
            document.getElementById('detail-images-grid').innerHTML = '<div class="loading">解析图片失败</div>';
        }
    } else {
        videoContainer.style.display = 'none';
        imagesContainer.style.display = 'none';
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

/**
 * 重试任务 - 回填参数到对应的生成页面
 */
async function retryTask() {
    if (!selectedTaskId) return;

    const task = tasks.find(t => t.task_id === selectedTaskId);
    if (!task) {
        showToast('任务不存在', 'error');
        return;
    }

    let params = {};
    try {
        if (task.params) params = JSON.parse(task.params);
    } catch (e) {
        showToast('解析任务参数失败', 'error');
        return;
    }

    // 根据任务类型跳转并回填
    if (task.task_type === 'video') {
        switchSection('generate');
        switchMode('video');

        if (params.prompt) document.getElementById('prompt-input').value = params.prompt;
        if (params.ratio) document.getElementById('ratio').value = params.ratio;
        if (params.resolution) document.getElementById('resolution').value = params.resolution;
        if (params.duration) {
            document.getElementById('duration').value = params.duration;
            document.getElementById('duration-value').textContent = params.duration + '秒';
        }
        if (params.count) document.getElementById('video-count').value = params.count;

        // 加载帧图片到 UI
        const framePaths = params.frame_paths || {};
        let pathsToCheck = [];
        if (framePaths.first_frame) pathsToCheck.push(framePaths.first_frame);
        if (framePaths.last_frame) pathsToCheck.push(framePaths.last_frame);

        if (pathsToCheck.length > 0) {
            try {
                const resp = await fetch(`${API_BASE}/upload/check-files`, {
                    method: 'POST',
                    headers: authHeaders(),
                    body: JSON.stringify({ paths: pathsToCheck })
                });

                if (resp.ok) {
                    const data = await resp.json();

                    // 加载首帧
                    if (framePaths.first_frame && data.results[framePaths.first_frame]) {
                        const filename = framePaths.first_frame.split(/[/\\]/).pop();
                        const imageUrl = `${API_BASE}/tasks/video/frame/${task.task_id}/${filename}`;
                        firstFrameData = {
                            type: 'url',
                            value: imageUrl,
                            existingPath: framePaths.first_frame
                        };
                        showPreview('first-frame', imageUrl);
                    }

                    // 加载尾帧
                    if (framePaths.last_frame && data.results[framePaths.last_frame]) {
                        const filename = framePaths.last_frame.split(/[/\\]/).pop();
                        const imageUrl = `${API_BASE}/tasks/video/frame/${task.task_id}/${filename}`;
                        lastFrameData = {
                            type: 'url',
                            value: imageUrl,
                            existingPath: framePaths.last_frame
                        };
                        showPreview('last-frame', imageUrl);
                    }

                    updateGenerationType();

                    // 检查是否有过期的帧
                    let expiredCount = 0;
                    if (framePaths.first_frame && !data.results[framePaths.first_frame]) expiredCount++;
                    if (framePaths.last_frame && !data.results[framePaths.last_frame]) expiredCount++;
                    if (expiredCount > 0) {
                        showToast(`${expiredCount} 张帧图片已过期，请重新上传`, 'warning');
                    }
                }
            } catch (e) {
                console.warn('加载帧图片失败:', e);
                showToast('帧图片加载失败，请重新上传', 'warning');
            }
        }

        showToast('已回填视频任务参数', 'success');

    } else if (task.task_type === 'image') {
        switchSection('generate');
        switchMode('image');

        if (params.prompt) document.getElementById('image-prompt-input').value = params.prompt;
        if (params.sequential_image_generation === 'auto') {
            document.getElementById('sequential-mode').checked = true;
        }

        // 加载参考图到 UI
        const refPaths = params.ref_image_paths || [];
        if (refPaths.length > 0) {
            // 清空当前参考图
            referenceImages = [];

            // 检查文件是否存在并加载
            try {
                const resp = await fetch(`${API_BASE}/upload/check-files`, {
                    method: 'POST',
                    headers: authHeaders(),
                    body: JSON.stringify({ paths: refPaths })
                });

                if (resp.ok) {
                    const data = await resp.json();
                    let loadedCount = 0;

                    refPaths.forEach((path, idx) => {
                        if (data.results[path]) {
                            const filename = path.split(/[/\\]/).pop();
                            const imageUrl = `${API_BASE}/images/file/${task.task_id}/${filename}`;
                            referenceImages.push({
                                name: filename,
                                localPreview: imageUrl,
                                type: 'url',  // 标记为已存在的 URL 类型
                                existingPath: path
                            });
                            loadedCount++;
                        }
                    });

                    renderRefImages();
                    updateImageGenerationType();

                    if (loadedCount < refPaths.length) {
                        showToast(`${refPaths.length - loadedCount} 张参考图已过期，请重新上传`, 'warning');
                    }
                }
            } catch (e) {
                console.warn('加载参考图失败:', e);
                showToast('参考图加载失败，请重新上传', 'warning');
            }
        }

        showToast('已回填图片任务参数', 'success');

    } else if (task.task_type === 'banana_image') {
        switchSection('generate');
        switchMode('banana');

        if (params.prompt) document.getElementById('banana-prompt-input').value = params.prompt;
        if (params.aspect_ratio) document.getElementById('banana-ratio').value = params.aspect_ratio;
        if (params.resolution) document.getElementById('banana-resolution').value = params.resolution;

        // 加载参考图到 UI
        const refPaths = params.ref_image_paths || [];
        if (refPaths.length > 0) {
            // 清空当前参考图
            bananaReferenceImages = [];

            // 检查文件是否存在并加载
            try {
                const resp = await fetch(`${API_BASE}/upload/check-files`, {
                    method: 'POST',
                    headers: authHeaders(),
                    body: JSON.stringify({ paths: refPaths })
                });

                if (resp.ok) {
                    const data = await resp.json();
                    let loadedCount = 0;

                    refPaths.forEach((path, idx) => {
                        if (data.results[path]) {
                            const filename = path.split(/[/\\]/).pop();
                            const imageUrl = `${API_BASE}/banana/images/file/${task.task_id}/${filename}`;
                            bananaReferenceImages.push({
                                name: filename,
                                localPreview: imageUrl,
                                type: 'url',
                                existingPath: path
                            });
                            loadedCount++;
                        }
                    });

                    renderBananaRefImages();
                    updateBananaGenerationType();

                    if (loadedCount < refPaths.length) {
                        showToast(`${refPaths.length - loadedCount} 张参考图已过期，请重新上传`, 'warning');
                    }
                }
            } catch (e) {
                console.warn('加载Banana参考图失败:', e);
                showToast('参考图加载失败，请重新上传', 'warning');
            }
        }

        showToast('已回填 Banana 任务参数', 'success');

    } else {
        showToast('不支持重试此类型任务', 'warning');
        return;
    }

    document.getElementById('task-detail').style.display = 'none';
}

/**
 * 检查文件是否存在，如有过期则提示
 */
async function checkFilesExist(paths) {
    try {
        const resp = await fetch(`${API_BASE}/upload/check-files`, {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify({ paths: paths })
        });
        if (resp.ok) {
            const data = await resp.json();
            const hasExpired = paths.some(path => !data.results[path]);
            if (hasExpired) {
                showToast('部分参考图片已过期，请重新上传', 'warning');
            }
        }
    } catch (e) {
        console.warn('检查文件失败:', e);
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
        // 检查所有进行中的任务 (视频、图片和Banana)
        const runningVideoTasks = tasks.filter(t => t.task_type === 'video' && (t.status === 'queued' || t.status === 'running'));
        const runningImageTasks = tasks.filter(t => (t.task_type === 'image' || t.task_type === 'banana_image') && t.status === 'running');

        const hasRunningTasks = runningVideoTasks.length > 0 || runningImageTasks.length > 0;

        if (hasRunningTasks) {
            console.log(`[轮询] 视频任务: ${runningVideoTasks.length}, 图片任务: ${runningImageTasks.length}`);

            // 记录当前状态
            tasks.forEach(t => {
                previousStatuses[t.task_id] = t.status;
            });

            // 同步视频任务 (需要调用sync接口)
            for (const task of runningVideoTasks) {
                try {
                    await fetch(`${API_BASE}/tasks/${task.task_id}/sync`, {
                        method: 'POST',
                        headers: authHeaders()
                    });
                } catch (err) {
                    console.error(`同步任务 ${task.task_id} 失败:`, err);
                }
            }

            // 重新加载任务列表 (图片任务状态由后端自动更新)
            await loadTasks();

            // 检查状态变化，显示通知
            tasks.forEach(t => {
                const prevStatus = previousStatuses[t.task_id];
                if (prevStatus && prevStatus !== t.status) {
                    const typeLabel = t.task_type === 'video' ? '视频' : '图片';
                    if (t.status === 'succeeded') {
                        showToast(`${typeLabel}任务 ${t.task_id.slice(-8)} 已完成！`, 'success');
                    } else if (t.status === 'failed') {
                        showToast(`${typeLabel}任务 ${t.task_id.slice(-8)} 失败`, 'error');
                    }
                }
            });

            // 如果当前在队列页面且有选中任务，更新详情
            if (document.getElementById('queue-section').classList.contains('active') && selectedTaskId) {
                showTaskDetail(selectedTaskId);
            }

            // 刷新账户配额
            loadAccounts();
        }
    }, 3000);  // 图片生成较快，缩短轮询间隔
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

// ======================== Banana 生图功能 ========================

function renderBananaAccountList() {
    const container = document.getElementById('banana-account-list');

    if (accounts.length === 0) {
        container.innerHTML = '<div class="loading">暂无账户，请先在设置中添加</div>';
        return;
    }

    // 过滤有 banana_base_url 的账户
    const bananaAccounts = accounts.filter(a => a.banana_base_url);

    if (bananaAccounts.length === 0) {
        container.innerHTML = '<div class="loading">暂无配置 Banana API 的账户</div>';
        return;
    }

    container.innerHTML = bananaAccounts.map(account => {
        return `
            <div class="account-item ${selectedBananaAccountId === account.id ? 'selected' : ''}" 
                 onclick="selectBananaAccount(${account.id})">
                <div class="account-info">
                    <div class="account-name">${account.name}</div>
                    <div class="account-quota">
                        模型: <span class="model-hint">${account.banana_model_name || 'gemini-3-pro-image-preview'}</span>
                    </div>
                </div>
            </div>
        `;
    }).join('');

    // 如果未选择账户，默认选择第一个有Banana能力的
    if (selectedBananaAccountId === null && bananaAccounts.length > 0) {
        selectBananaAccount(bananaAccounts[0].id);
    }
}

function selectBananaAccount(accountId) {
    selectedBananaAccountId = accountId;
    renderBananaAccountList();
    updateBananaGenerateButton();

    // 加载该账户的用量
    loadBananaUsage(accountId);
}

window.selectBananaAccount = selectBananaAccount;

function handleBananaRefImagesSelect(e) {
    const files = Array.from(e.target.files);

    if (bananaReferenceImages.length + files.length > 14) {
        showToast('参考图片最多14张', 'error');
        return;
    }

    files.forEach(file => {
        if (!file.type.startsWith('image/')) {
            showToast(`${file.name} 不是图片文件`, 'error');
            return;
        }

        // 先读取本地预览
        const reader = new FileReader();
        reader.onload = async (ev) => {
            const localPreview = ev.target.result;

            // 添加为上传中状态
            bananaReferenceImages.push({
                name: file.name,
                localPreview: localPreview,
                type: 'uploading',
                progress: 0
            });

            renderBananaRefImages();
            updateBananaGenerationType();
            updateBananaGenerateButton();

            // 开始上传
            const currentIndex = bananaReferenceImages.findIndex(
                img => img.localPreview === localPreview && img.type === 'uploading'
            );

            try {
                const result = await uploadFile(file, (progress) => {
                    if (currentIndex >= 0 && bananaReferenceImages[currentIndex]) {
                        bananaReferenceImages[currentIndex].progress = progress;
                        renderBananaRefImages();
                    }
                });

                // 上传成功
                if (currentIndex >= 0 && bananaReferenceImages[currentIndex]) {
                    bananaReferenceImages[currentIndex].type = 'uploaded';
                    bananaReferenceImages[currentIndex].fileId = result.file_id;
                    delete bananaReferenceImages[currentIndex].progress;
                }

                renderBananaRefImages();
                updateBananaGenerateButton();

            } catch (err) {
                showToast(`上传失败: ${err.message}`, 'error');
                // 移除失败的图片
                const failIndex = bananaReferenceImages.findIndex(
                    img => img.localPreview === localPreview && img.type === 'uploading'
                );
                if (failIndex >= 0) {
                    bananaReferenceImages.splice(failIndex, 1);
                }
                renderBananaRefImages();
                updateBananaGenerateButton();
            }
        };
        reader.readAsDataURL(file);
    });

    // 清空input以便重复选择相同文件
    e.target.value = '';
}

function renderBananaRefImages() {
    const container = document.getElementById('banana-ref-images-container');

    // 清空现有预览
    container.innerHTML = '';

    // 添加已有图片
    bananaReferenceImages.forEach((img, index) => {
        const item = document.createElement('div');
        item.className = 'ref-image-item' + (img.type === 'uploading' ? ' uploading' : '');

        let progressHtml = '';
        if (img.type === 'uploading') {
            progressHtml = `<div class="upload-progress"><div class="upload-progress-bar" style="width: ${img.progress || 0}%"></div></div>`;
        }

        const src = img.localPreview || img.data;
        item.innerHTML = `
            <img src="${src}" alt="${img.name}">
            <button type="button" class="ref-image-remove" onclick="removeBananaRefImage(${index})">✕</button>
            ${progressHtml}
        `;
        container.appendChild(item);
    });

    // 添加"添加"按钮
    if (bananaReferenceImages.length < 14) {
        const addDiv = document.createElement('div');
        addDiv.className = 'ref-image-add';
        addDiv.id = 'banana-ref-image-add';
        addDiv.onclick = () => document.getElementById('banana-ref-images-file').click();
        addDiv.innerHTML = `
            <span class="add-icon">+</span>
            <span class="add-text">添加</span>
        `;
        container.appendChild(addDiv);
    }
}

async function removeBananaRefImage(index) {
    const img = bananaReferenceImages[index];
    if (img?.fileId) {
        deleteUploadedFile(img.fileId);
    }
    bananaReferenceImages.splice(index, 1);
    renderBananaRefImages();
    updateBananaGenerationType();
    updateBananaGenerateButton();
}

window.removeBananaRefImage = removeBananaRefImage;

function updateBananaGenerationType() {
    const hasImages = bananaReferenceImages.length > 0;
    let type = '纯文生图';

    if (hasImages) {
        if (bananaReferenceImages.length > 1) {
            type = `多图融合 (${bananaReferenceImages.length}张)`;
        } else {
            type = '单图参考';
        }
    }

    document.getElementById('banana-generation-type').textContent = type;
}

function updateBananaGenerateButton() {
    const btn = document.getElementById('banana-generate-btn');
    const prompt = document.getElementById('banana-prompt-input').value.trim();

    let canGenerate = selectedBananaAccountId !== null && prompt.length > 0;

    // 检查是否有图片正在上传
    const isUploading = bananaReferenceImages.some(img => img.type === 'uploading');
    if (isUploading) {
        canGenerate = false;
        btn.classList.add('uploading-blocked');
    } else {
        btn.classList.remove('uploading-blocked');
    }

    // 检查账户是否有Banana配置
    if (canGenerate) {
        const account = accounts.find(a => a.id === selectedBananaAccountId);
        if (!account || !account.banana_base_url) {
            canGenerate = false;
        }
    }

    btn.disabled = !canGenerate;
}

async function handleBananaGenerate() {
    const prompt = document.getElementById('banana-prompt-input').value.trim();

    if (!prompt) {
        showToast('请输入图片描述', 'error');
        return;
    }

    if (!selectedBananaAccountId) {
        showToast('请选择账户', 'error');
        return;
    }

    const btn = document.getElementById('banana-generate-btn');
    btn.disabled = true;
    btn.innerHTML = '<span class="btn-icon">⏳</span><span>生成中...</span>';

    try {
        const resolution = document.getElementById('banana-resolution').value;
        const ratio = document.getElementById('banana-ratio').value;

        const body = {
            account_id: selectedBananaAccountId,
            prompt: prompt,
            aspect_ratio: ratio,
            resolution: resolution,
        };

        // 添加参考图片 (使用 file_id)
        if (bananaReferenceImages.length > 0) {
            const uploadedFileIds = bananaReferenceImages
                .filter(img => img.type === 'uploaded' && img.fileId)
                .map(img => img.fileId);
            if (uploadedFileIds.length > 0) {
                body.file_ids = uploadedFileIds;
            }
        }

        const resp = await fetch(`${API_BASE}/banana/images`, {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify(body)
        });

        if (resp.ok) {
            const task = await resp.json();
            showToast(`Banana图片任务已提交，正在生成中...`, 'success');

            // 刷新存储状态
            loadBananaStorage();

            // 不再跳转，留在当前页面方便继续提交
            // switchSection('queue');
        } else {
            const data = await resp.json();
            showToast(data.detail || '生成失败', 'error');
        }
    } catch (err) {
        console.error(err);
        showToast('网络错误', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<span class="btn-icon">🍌</span><span>生成图片</span>';
        updateBananaGenerateButton();
    }
}

async function loadBananaStorage() {
    try {
        const resp = await fetch(`${API_BASE}/banana/storage`, {
            headers: authHeaders()
        });

        if (resp.ok) {
            const data = await resp.json();
            document.getElementById('banana-storage-size').textContent = data.size_display;
        }
    } catch (err) {
        console.error('加载Banana存储信息失败:', err);
    }
}

async function loadBananaUsage(accountId) {
    if (!accountId) {
        document.getElementById('banana-usage-count').textContent = '-';
        return;
    }

    try {
        const resp = await fetch(`${API_BASE}/banana/usage?account_id=${accountId}`, {
            headers: authHeaders()
        });

        if (resp.ok) {
            const data = await resp.json();
            document.getElementById('banana-usage-count').textContent = `${data.images_last_5h} 张`;
        } else {
            document.getElementById('banana-usage-count').textContent = '-';
        }
    } catch (err) {
        console.error('加载Banana用量失败:', err);
        document.getElementById('banana-usage-count').textContent = '-';
    }
}

async function cleanupBananaStorage() {
    if (!confirm('确定清理所有 Banana 图片？此操作不可恢复。')) {
        return;
    }

    try {
        const resp = await fetch(`${API_BASE}/banana/storage/cleanup`, {
            method: 'POST',
            headers: authHeaders()
        });

        if (resp.ok) {
            const data = await resp.json();
            showToast(data.message, 'success');
            loadBananaStorage();
        } else {
            const data = await resp.json();
            showToast(data.detail || '清理失败', 'error');
        }
    } catch (err) {
        showToast('网络错误', 'error');
    }
}

async function refreshBananaInfo() {
    // 同时刷新存储空间和使用量
    await loadBananaStorage();
    if (selectedBananaAccountId) {
        await loadBananaUsage(selectedBananaAccountId);
    }
    showToast('已刷新', 'info');
}

window.loadBananaStorage = loadBananaStorage;
window.cleanupBananaStorage = cleanupBananaStorage;
window.refreshBananaInfo = refreshBananaInfo;

// ======================== 火山图片存储管理 ========================

async function loadVolcanoStorage() {
    try {
        const resp = await fetch(`${API_BASE}/images/storage/info`, {
            headers: authHeaders()
        });

        if (resp.ok) {
            const data = await resp.json();
            document.getElementById('volcano-storage-size').textContent = data.size_display;
            document.getElementById('volcano-storage-files').textContent = `${data.file_count} 个文件`;
        }
    } catch (err) {
        console.error('加载火山存储信息失败:', err);
    }
}

async function cleanupVolcanoStorage() {
    if (!confirm('确定清理所有火山图片参考图？此操作不可恢复。')) {
        return;
    }

    try {
        const resp = await fetch(`${API_BASE}/images/storage/cleanup`, {
            method: 'POST',
            headers: authHeaders()
        });

        if (resp.ok) {
            const data = await resp.json();
            showToast(data.message, 'success');
            loadVolcanoStorage();
        } else {
            const data = await resp.json();
            showToast(data.detail || '清理失败', 'error');
        }
    } catch (err) {
        showToast('网络错误', 'error');
    }
}

window.loadVolcanoStorage = loadVolcanoStorage;
window.cleanupVolcanoStorage = cleanupVolcanoStorage;

// ======================== 视频帧存储管理 ========================

async function loadVideoStorage() {
    try {
        const resp = await fetch(`${API_BASE}/tasks/video/storage/info`, {
            headers: authHeaders()
        });

        if (resp.ok) {
            const data = await resp.json();
            document.getElementById('video-storage-size').textContent = data.size_display;
            document.getElementById('video-storage-files').textContent = `${data.file_count} 个文件`;
        }
    } catch (err) {
        console.error('加载视频帧存储信息失败:', err);
    }
}

async function cleanupVideoStorage() {
    if (!confirm('确定清理所有视频帧图片？此操作不可恢复。')) {
        return;
    }

    try {
        const resp = await fetch(`${API_BASE}/tasks/video/storage/cleanup`, {
            method: 'POST',
            headers: authHeaders()
        });

        if (resp.ok) {
            const data = await resp.json();
            showToast(data.message, 'success');
            loadVideoStorage();
        } else {
            const data = await resp.json();
            showToast(data.detail || '清理失败', 'error');
        }
    } catch (err) {
        showToast('网络错误', 'error');
    }
}

window.loadVideoStorage = loadVideoStorage;
window.cleanupVideoStorage = cleanupVideoStorage;

// 任务操作函数暴露到 window (供 HTML onclick 调用)
window.retryTask = retryTask;
window.syncSelectedTask = syncSelectedTask;
window.deleteSelectedTask = deleteSelectedTask;
