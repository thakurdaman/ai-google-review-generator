// admin.js - Reseller Dashboard Interactions
document.addEventListener('DOMContentLoaded', () => {
    // Tab switching
    const tabBtnBusinesses = document.getElementById('tab-btn-businesses');
    const tabBtnFeedback = document.getElementById('tab-btn-feedback');
    const sectionBusinesses = document.getElementById('section-businesses');
    const sectionFeedback = document.getElementById('section-feedback');

    tabBtnBusinesses.addEventListener('click', () => {
        tabBtnBusinesses.className = 'tab-btn active px-4 py-2 rounded-xl text-xs sm:text-sm font-bold bg-brand-600 text-white shadow-sm flex items-center space-x-2';
        tabBtnFeedback.className = 'tab-btn px-4 py-2 rounded-xl text-xs sm:text-sm font-semibold bg-slate-800 text-slate-300 hover:bg-slate-700 flex items-center space-x-2';
        sectionBusinesses.classList.remove('hidden');
        sectionFeedback.classList.add('hidden');
    });

    tabBtnFeedback.addEventListener('click', () => {
        tabBtnFeedback.className = 'tab-btn active px-4 py-2 rounded-xl text-xs sm:text-sm font-bold bg-brand-600 text-white shadow-sm flex items-center space-x-2';
        tabBtnBusinesses.className = 'tab-btn px-4 py-2 rounded-xl text-xs sm:text-sm font-semibold bg-slate-800 text-slate-300 hover:bg-slate-700 flex items-center space-x-2';
        sectionBusinesses.classList.add('hidden');
        sectionFeedback.classList.remove('hidden');
    });

    // Real-time Business Search
    const searchInput = document.getElementById('business-search');
    if (searchInput) {
        searchInput.addEventListener('input', (e) => {
            const query = e.target.value.toLowerCase().trim();
            const cards = document.querySelectorAll('.business-card');
            cards.forEach(card => {
                const name = card.getAttribute('data-name') || '';
                const cat = card.getAttribute('data-category') || '';
                if (name.includes(query) || cat.includes(query)) {
                    card.style.display = '';
                } else {
                    card.style.display = 'none';
                }
            });
        });
    }

    // Auto-slug generator on Add Business
    const addNameInput = document.getElementById('add-name');
    const addSlugInput = document.getElementById('add-slug');
    if (addNameInput && addSlugInput) {
        addNameInput.addEventListener('input', () => {
            const slug = addNameInput.value
                .toLowerCase()
                .trim()
                .replace(/[^\w\s-]/g, '')
                .replace(/[\s_-]+/g, '-')
                .replace(/^-+|-+$/g, '');
            addSlugInput.value = slug;
        });
    }

    // Accent color sync
    const addColorPicker = document.getElementById('add-accent-color');
    const addColorHex = document.getElementById('add-accent-hex');
    if (addColorPicker && addColorHex) {
        addColorPicker.addEventListener('input', () => {
            addColorHex.value = addColorPicker.value;
        });
        addColorHex.addEventListener('input', () => {
            if (/^#[0-9A-F]{6}$/i.test(addColorHex.value)) {
                addColorPicker.value = addColorHex.value;
            }
        });
    }

    // Open Add Business Modal
    document.getElementById('btn-open-add-business').addEventListener('click', () => {
        openModal('modal-add-business');
    });

    // Open Settings Modal
    document.getElementById('btn-open-settings').addEventListener('click', async () => {
        openModal('modal-settings');
        try {
            const resp = await fetch('/api/admin/settings');
            if (resp.ok) {
                const data = await resp.json();
                document.getElementById('settings-openai-key').value = data.openai_api_key || '';
            }
        } catch (e) {}
    });

    // Submit Add Business
    document.getElementById('form-add-business').addEventListener('submit', async (e) => {
        e.preventDefault();
        const payload = {
            name: document.getElementById('add-name').value.trim(),
            slug: document.getElementById('add-slug').value.trim(),
            category: document.getElementById('add-category').value,
            google_review_url: document.getElementById('add-google-url').value.trim(),
            logo_url: document.getElementById('add-logo-url').value.trim(),
            accent_color: document.getElementById('add-accent-hex').value.trim() || '#2563EB',
            tags_preset: document.getElementById('add-tags').value.split(',').map(t => t.trim()).filter(Boolean)
        };

        try {
            const resp = await fetch('/api/admin/businesses', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (!resp.ok) {
                const err = await resp.json();
                throw new Error(err.detail || 'Failed to create business');
            }

            showToast('Business added successfully!', 'success');
            setTimeout(() => window.location.reload(), 600);
        } catch (err) {
            showToast(err.message, 'error');
        }
    });

    // Submit Edit Business
    document.getElementById('form-edit-business').addEventListener('submit', async (e) => {
        e.preventDefault();
        const id = document.getElementById('edit-id').value;
        const payload = {
            name: document.getElementById('edit-name').value.trim(),
            slug: document.getElementById('edit-slug').value.trim(),
            category: document.getElementById('edit-category').value.trim(),
            google_review_url: document.getElementById('edit-google-url').value.trim(),
            logo_url: document.getElementById('edit-logo-url').value.trim(),
            accent_color: document.getElementById('edit-accent-color').value.trim(),
            tags_preset: document.getElementById('edit-tags').value.split(',').map(t => t.trim()).filter(Boolean)
        };

        try {
            const resp = await fetch(`/api/admin/businesses/${id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (!resp.ok) {
                const err = await resp.json();
                throw new Error(err.detail || 'Failed to update business');
            }

            showToast('Business updated successfully!', 'success');
            setTimeout(() => window.location.reload(), 600);
        } catch (err) {
            showToast(err.message, 'error');
        }
    });

    // Save Settings
    document.getElementById('form-settings').addEventListener('submit', async (e) => {
        e.preventDefault();
        const apiKey = document.getElementById('settings-openai-key').value.trim();

        try {
            const resp = await fetch('/api/admin/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ openai_api_key: apiKey })
            });

            if (resp.ok) {
                showToast('Settings saved successfully!', 'success');
                closeModal('modal-settings');
            } else {
                throw new Error('Could not save settings');
            }
        } catch (err) {
            showToast(err.message, 'error');
        }
    });

    // Test OpenAI Connection
    document.getElementById('btn-test-ai').addEventListener('click', async () => {
        const apiKey = document.getElementById('settings-openai-key').value.trim();
        const btn = document.getElementById('btn-test-ai');
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-1"></i> Testing...';

        try {
            const resp = await fetch('/api/admin/settings/test-ai', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ api_key: apiKey })
            });

            const data = await resp.json();
            if (data.success) {
                showToast(data.message, 'success');
            } else {
                showToast(data.error || 'Connection failed', 'error');
            }
        } catch (err) {
            showToast('Test request failed', 'error');
        } finally {
            btn.disabled = false;
            btn.innerHTML = '<i class="fa-solid fa-vial"></i> <span>Test OpenAI Key</span>';
        }
    });
});

// Global Helpers
function openModal(id) {
    document.getElementById(id).classList.remove('hidden');
}

function closeModal(id) {
    document.getElementById(id).classList.add('hidden');
}

function copyLandingLink(slug) {
    const fullUrl = `${window.location.origin}/r/${slug}`;
    navigator.clipboard.writeText(fullUrl).then(() => {
        showToast(`Copied short link: /r/${slug}`, 'success');
    }).catch(() => {
        prompt('Copy this short link:', fullUrl);
    });
}

function openQrModal(id, name, slug) {
    document.getElementById('qr-modal-title').textContent = `${name} - QR & Stand`;
    const fullUrl = `${window.location.origin}/r/${slug}`;
    const qrImg = document.getElementById('qr-image-preview');
    qrImg.src = `/api/qr/${slug}.png`;
    
    document.getElementById('qr-direct-link-display').textContent = fullUrl;
    document.getElementById('qr-download-png').href = `/api/qr/${slug}.png?download=true`;
    document.getElementById('qr-flyer-btn').href = `/admin/business/${id}/flyer`;

    openModal('modal-qr');
}

async function openEditModal(id) {
    try {
        const resp = await fetch(`/api/admin/businesses/${id}`);
        if (!resp.ok) throw new Error('Could not load business details');
        const b = await resp.json();

        document.getElementById('edit-id').value = b.id;
        document.getElementById('edit-name').value = b.name;
        document.getElementById('edit-slug').value = b.slug;
        document.getElementById('edit-category').value = b.category;
        document.getElementById('edit-google-url').value = b.google_review_url;
        document.getElementById('edit-logo-url').value = b.logo_url || '';
        document.getElementById('edit-accent-color').value = b.accent_color || '#2563EB';

        let tags = [];
        try {
            tags = typeof b.tags_preset === 'string' ? JSON.parse(b.tags_preset) : b.tags_preset;
        } catch (e) {
            tags = [];
        }
        document.getElementById('edit-tags').value = tags.join(', ');

        openModal('modal-edit-business');
    } catch (err) {
        showToast(err.message, 'error');
    }
}

async function deleteBusiness(id, name) {
    if (!confirm(`Are you sure you want to delete client "${name}"? This will delete all its links, QR codes, and analytics.`)) {
        return;
    }

    try {
        const resp = await fetch(`/api/admin/businesses/${id}`, {
            method: 'DELETE'
        });

        if (!resp.ok) throw new Error('Could not delete business');

        showToast(`Business "${name}" removed.`, 'success');
        setTimeout(() => window.location.reload(), 600);
    } catch (err) {
        showToast(err.message, 'error');
    }
}
