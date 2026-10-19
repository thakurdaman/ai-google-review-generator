// admin.js - Reseller Dashboard Interactions
document.addEventListener('DOMContentLoaded', () => {
    // Tab switching
    const tabs = ['businesses', 'owners', 'payments'];
    tabs.forEach(tabName => {
        const btn = document.getElementById(`tab-btn-${tabName}`);
        if (btn) {
            btn.addEventListener('click', () => {
                tabs.forEach(t => {
                    const b = document.getElementById(`tab-btn-${t}`);
                    const s = document.getElementById(`section-${t}`);
                    if (b) {
                        b.className = 'tab-btn px-4 py-2 rounded-xl text-xs sm:text-sm font-semibold bg-slate-800 text-slate-300 hover:bg-slate-700 flex items-center space-x-2 shrink-0';
                    }
                    if (s) s.classList.add('hidden');
                });
                btn.className = 'tab-btn active px-4 py-2 rounded-xl text-xs sm:text-sm font-bold bg-brand-600 text-white shadow-sm flex items-center space-x-2 shrink-0';
                const currentSec = document.getElementById(`section-${tabName}`);
                if (currentSec) currentSec.classList.remove('hidden');
            });
        }
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
    const btnAddBiz = document.getElementById('btn-open-add-business');
    if (btnAddBiz) {
        btnAddBiz.addEventListener('click', () => {
            openModal('modal-add-business');
        });
    }

    // Open Settings Modal
    const btnSettings = document.getElementById('btn-open-settings');
    if (btnSettings) {
        btnSettings.addEventListener('click', async () => {
            openModal('modal-settings');
            const redirectUriElem = document.getElementById('google-redirect-uri-display');
            if (redirectUriElem) {
                redirectUriElem.textContent = `${window.location.origin}/auth/google/callback`;
            }
            try {
                const resp = await fetch('/api/admin/settings');
                if (resp.ok) {
                    const data = await resp.json();
                    document.getElementById('settings-openai-key').value = data.openai_api_key || '';
                    document.getElementById('settings-razorpay-key').value = data.razorpay_key_id || '';
                    document.getElementById('settings-razorpay-secret').value = data.razorpay_key_secret || '';
                    if (document.getElementById('settings-google-id')) {
                        document.getElementById('settings-google-id').value = data.google_client_id || '';
                    }
                    if (document.getElementById('settings-google-secret')) {
                        document.getElementById('settings-google-secret').value = data.google_client_secret || '';
                    }
                    const chkPayments = document.getElementById('settings-payments-enabled');
                    if (chkPayments) {
                        chkPayments.checked = data.payments_enabled === true;
                    }
                }
            } catch (e) {}
        });
    }

    // Submit Add Business
    const formAddBiz = document.getElementById('form-add-business');
    if (formAddBiz) {
        formAddBiz.addEventListener('submit', async (e) => {
            e.preventDefault();
            const payload = {
                name: document.getElementById('add-name').value.trim(),
                slug: document.getElementById('add-slug').value.trim(),
                category: document.getElementById('add-category').value,
                google_business_profile_url: document.getElementById('add-google-url').value.trim(),
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
    }

    // Submit Edit Business
    const formEditBiz = document.getElementById('form-edit-business');
    if (formEditBiz) {
        formEditBiz.addEventListener('submit', async (e) => {
            e.preventDefault();
            const id = document.getElementById('edit-id').value;
            const payload = {
                name: document.getElementById('edit-name').value.trim(),
                slug: document.getElementById('edit-slug').value.trim(),
                category: document.getElementById('edit-category').value.trim(),
                google_business_profile_url: document.getElementById('edit-google-url').value.trim(),
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
    }

    // Save Settings
    const formSettings = document.getElementById('form-settings');
    if (formSettings) {
        formSettings.addEventListener('submit', async (e) => {
            e.preventDefault();
            const openAiKey = document.getElementById('settings-openai-key').value.trim();
            const rzpKey = document.getElementById('settings-razorpay-key').value.trim();
            const rzpSecret = document.getElementById('settings-razorpay-secret').value.trim();
            const googleIdElem = document.getElementById('settings-google-id');
            const googleSecretElem = document.getElementById('settings-google-secret');
            const googleId = googleIdElem ? googleIdElem.value.trim() : '';
            const googleSecret = googleSecretElem ? googleSecretElem.value.trim() : '';
            const chkPayments = document.getElementById('settings-payments-enabled');
            const paymentsEnabled = chkPayments ? chkPayments.checked : false;

            try {
                const resp = await fetch('/api/admin/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        openai_api_key: openAiKey,
                        razorpay_key_id: rzpKey,
                        razorpay_key_secret: rzpSecret,
                        google_client_id: googleId,
                        google_client_secret: googleSecret,
                        payments_enabled: paymentsEnabled
                    })
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
    }

    // Test OpenAI Connection
    const btnTestAi = document.getElementById('btn-test-ai');
    if (btnTestAi) {
        btnTestAi.addEventListener('click', async () => {
            const apiKey = document.getElementById('settings-openai-key').value.trim();
            btnTestAi.disabled = true;
            btnTestAi.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-1"></i> Testing...';

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
                btnTestAi.disabled = false;
                btnTestAi.innerHTML = '<i class="fa-solid fa-vial"></i> <span>Test OpenAI Key</span>';
            }
        });
    }
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

async function inspectBusiness(id) {
    openModal('modal-inspect-business');
    const content = document.getElementById('inspect-content');
    content.innerHTML = `
        <div class="py-8 text-center text-slate-400">
            <i class="fa-solid fa-circle-notch fa-spin text-2xl mb-2"></i>
            <p class="text-xs">Loading business analytics and audit logs...</p>
        </div>
    `;

    try {
        const resp = await fetch(`/api/admin/businesses/${id}/inspect`);
        if (!resp.ok) throw new Error('Could not load business details');
        const data = await resp.json();
        const b = data.business;
        const m = data.metrics;
        const logs = data.logs || [];
        const payments = data.payments || [];

        document.getElementById('inspect-business-name').textContent = `${b.name} (${b.category})`;

        // Build Inspect HTML
        let logsHtml = '';
        if (logs.length === 0) {
            logsHtml = '<p class="text-xs text-slate-500 py-2">No activity logged yet.</p>';
        } else {
            logsHtml = logs.map(l => {
                let icon = 'fa-clock text-slate-400';
                if (l.action === 'signup') icon = 'fa-user-plus text-brand-400';
                else if (l.action === 'login') icon = 'fa-right-to-bracket text-emerald-400';
                else if (l.action === 'google_url_updated') icon = 'fa-link text-amber-400';
                else if (l.action.includes('mandate') || l.action.includes('subscription')) icon = 'fa-credit-card text-indigo-400';

                return `
                    <div class="flex items-start space-x-2.5 text-xs py-2 border-b border-slate-800 last:border-0">
                        <i class="fa-solid ${icon} mt-0.5 text-xs"></i>
                        <div class="flex-1">
                            <div class="flex items-center justify-between">
                                <span class="font-bold text-slate-200">${escapeHtml(l.action.replace(/_/g, ' ').toUpperCase())}</span>
                                <span class="text-[10px] text-slate-500 font-mono">${l.created_at}</span>
                            </div>
                            <p class="text-slate-400 text-[11px] mt-0.5">${escapeHtml(l.details || '')}</p>
                        </div>
                    </div>
                `;
            }).join('');
        }

        let paymentsHtml = '';
        if (payments.length === 0) {
            paymentsHtml = '<p class="text-xs text-slate-500 py-2">No payment transactions yet.</p>';
        } else {
            paymentsHtml = payments.map(p => `
                <div class="flex items-center justify-between text-xs py-2 border-b border-slate-800 last:border-0">
                    <div>
                        <span class="font-bold text-white">₹${parseFloat(p.amount).toFixed(2)}</span>
                        <span class="text-[11px] text-slate-400"> • ${p.plan_type.toUpperCase()} (${p.payment_mode === 'upi_autopay' ? 'UPI Autopay' : 'One-Time'})</span>
                    </div>
                    <div class="text-right">
                        <span class="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/20 text-emerald-300">${p.status}</span>
                        <span class="text-[10px] text-slate-500 block font-mono">${p.created_at}</span>
                    </div>
                </div>
            `).join('');
        }

        content.innerHTML = `
            <!-- Top Status Bar -->
            <div class="bg-slate-800/80 p-4 rounded-2xl border border-slate-700/80 flex flex-wrap items-center justify-between gap-3 text-xs">
                <div>
                    <span class="text-slate-400 block mb-0.5">Owner Account:</span>
                    <strong class="text-white">${escapeHtml(b.owner_email || 'Manual Reseller Client')}</strong>
                </div>
                <div>
                    <span class="text-slate-400 block mb-0.5">Subscription Status:</span>
                    <span class="px-2.5 py-0.5 rounded-full text-[10px] font-bold ${['active', 'trialing'].includes(b.subscription_status) ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30' : 'bg-rose-500/20 text-rose-300 border border-rose-500/30'}">
                        ${b.subscription_status ? b.subscription_status.toUpperCase() : 'ACTIVE'}
                    </span>
                </div>
                <div>
                    <span class="text-slate-400 block mb-0.5">Plan & Mode:</span>
                    <strong class="text-amber-300">${(b.plan_tier || 'Monthly').toUpperCase()} (${b.payment_method === 'upi_autopay' ? 'UPI Autopay' : 'One-Time'})</strong>
                </div>
                <div>
                    <span class="text-slate-400 block mb-0.5">Cycle End:</span>
                    <strong class="text-slate-300">${b.subscription_cycle_end || 'Active'}</strong>
                </div>
            </div>

            <!-- Identical Metrics to Owner Dashboard -->
            <div class="grid grid-cols-4 gap-2 text-center">
                <div class="bg-slate-800/60 p-2.5 rounded-xl border border-slate-700/60">
                    <span class="text-[10px] text-slate-400 block">Scans</span>
                    <span class="text-base font-bold text-white">${m.scans_count}</span>
                </div>
                <div class="bg-slate-800/60 p-2.5 rounded-xl border border-slate-700/60">
                    <span class="text-[10px] text-slate-400 block">5★ Reviews</span>
                    <span class="text-base font-bold text-amber-300">${m.reviews_count}</span>
                </div>
                <div class="bg-slate-800/60 p-2.5 rounded-xl border border-slate-700/60">
                    <span class="text-[10px] text-slate-400 block">Conversion</span>
                    <span class="text-base font-bold text-emerald-400">${m.conversion_rate}%</span>
                </div>
                <div class="bg-slate-800/60 p-2.5 rounded-xl border border-slate-700/60">
                    <span class="text-[10px] text-slate-400 block">AI Replies</span>
                    <span class="text-base font-bold text-brand-400">${m.replies_posted_count || 0}</span>
                </div>
            </div>

            <!-- Read-Only Google Review Link Settings -->
            <div class="bg-slate-800/60 p-4 rounded-xl border border-slate-700/60 space-y-2 text-xs">
                <div class="flex items-center justify-between">
                    <span class="font-bold text-slate-300">Google Business Profile Link (Direct Review URL):</span>
                    <span class="text-[10px] text-slate-400">Last updated: ${b.last_google_url_update || 'Initial setup'}</span>
                </div>
                <div class="bg-slate-900 p-2.5 rounded-lg border border-slate-800 font-mono text-[11px] text-brand-300 break-all flex items-center justify-between">
                    <span>${escapeHtml(b.google_review_url)}</span>
                    <a href="${escapeHtml(b.google_review_url)}" target="_blank" class="ml-2 text-slate-400 hover:text-white" title="Open Link">
                        <i class="fa-solid fa-arrow-up-right-from-square"></i>
                    </a>
                </div>
                <p class="text-[10px] text-slate-500 italic">Direct review link auto-generated from Place ID. Owner can update from settings.</p>
            </div>

            <!-- Activity Audit Logs Timeline -->
            <div class="bg-slate-800/60 p-4 rounded-xl border border-slate-700/60 space-y-2">
                <h4 class="text-xs font-bold text-white flex items-center">
                    <i class="fa-solid fa-timeline text-indigo-400 mr-2"></i>
                    Activity Audit Logs (Logins, Link Updates, Mandates)
                </h4>
                <div class="max-h-48 overflow-y-auto pr-1">
                    ${logsHtml}
                </div>
            </div>

            <!-- Payment & Invoices Breakdown -->
            <div class="bg-slate-800/60 p-4 rounded-xl border border-slate-700/60 space-y-2">
                <h4 class="text-xs font-bold text-white flex items-center">
                    <i class="fa-solid fa-file-invoice text-emerald-400 mr-2"></i>
                    Payment History & Invoices for this Business
                </h4>
                <div class="max-h-40 overflow-y-auto pr-1">
                    ${paymentsHtml}
                </div>
            </div>
        `;

    } catch (err) {
        content.innerHTML = `<p class="text-xs text-rose-400 text-center py-6">${err.message}</p>`;
    }
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

function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}
