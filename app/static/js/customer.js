// customer.js - Mobile-first NFC & QR Review Flow
document.addEventListener('DOMContentLoaded', () => {
    let currentRating = 0;
    let selectedTags = new Set();
    let currentDrafts = [];
    let selectedDraftIndex = 0;

    // Parse business tags
    let rawTags = [];
    try {
        rawTags = typeof BUSINESS_DATA.tags === 'string' ? JSON.parse(BUSINESS_DATA.tags) : (BUSINESS_DATA.tags || []);
    } catch (e) {
        rawTags = ["Friendly Staff", "Quick Service", "Great Quality", "Clean Environment"];
    }
    if (!rawTags.length) {
        rawTags = ["Friendly Staff", "Quick Service", "Great Quality", "Clean Environment"];
    }

    // Record page view / scan event on load
    recordAnalyticsEvent('scan');

    // Setup Rating Stars
    const starButtons = document.querySelectorAll('.star-btn');
    const ratingLabel = document.getElementById('rating-label');

    const ratingDescriptions = {
        1: { text: "1 Star • Terrible", color: "text-rose-500" },
        2: { text: "2 Stars • Poor", color: "text-orange-500" },
        3: { text: "3 Stars • Average", color: "text-amber-500" },
        4: { text: "4 Stars • Great!", color: "text-emerald-500" },
        5: { text: "5 Stars • Exceptional!", color: "text-emerald-600" }
    };

    function updateStarsDisplay(rating) {
        starButtons.forEach(btn => {
            const starVal = parseInt(btn.getAttribute('data-rating'));
            if (starVal <= rating) {
                btn.classList.remove('text-slate-200');
                btn.classList.add('text-amber-400');
            } else {
                btn.classList.remove('text-amber-400');
                btn.classList.add('text-slate-200');
            }
        });
    }

    starButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            currentRating = parseInt(btn.getAttribute('data-rating'));
            updateStarsDisplay(currentRating);
            
            const info = ratingDescriptions[currentRating];
            ratingLabel.textContent = info.text;
            ratingLabel.className = `text-sm font-bold ${info.color} transition`;

            recordAnalyticsEvent('star_selected', currentRating);

            // Branch based on rating
            handleRatingBranch(currentRating);
        });

        btn.addEventListener('mouseenter', () => {
            const hoverVal = parseInt(btn.getAttribute('data-rating'));
            updateStarsDisplay(hoverVal);
        });
    });

    document.getElementById('step-rating').addEventListener('mouseleave', () => {
        updateStarsDisplay(currentRating);
    });

    // Populate Chips for Step 2A
    const chipsContainer = document.getElementById('chips-container');
    rawTags.forEach(tag => {
        const chipBtn = document.createElement('button');
        chipBtn.type = 'button';
        chipBtn.className = 'chip px-3 py-1.5 rounded-full text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-200 flex items-center space-x-1 hover:bg-slate-200';
        chipBtn.innerHTML = `<span>${tag}</span>`;
        chipBtn.addEventListener('click', () => {
            if (selectedTags.has(tag)) {
                selectedTags.delete(tag);
                chipBtn.classList.remove('selected');
            } else {
                selectedTags.add(tag);
                chipBtn.classList.add('selected');
            }
        });
        chipsContainer.appendChild(chipBtn);
    });

    function handleRatingBranch(rating) {
        const stepPositive = document.getElementById('step-positive');
        const stepNegative = document.getElementById('step-negative');
        const stepAiResults = document.getElementById('step-ai-results');
        const stepFeedbackThanks = document.getElementById('step-feedback-thanks');

        // Hide downstream states
        stepAiResults.classList.add('hidden');
        stepFeedbackThanks.classList.add('hidden');

        if (rating >= 4) {
            // Positive Flow -> Show quick tap options + AI generation
            stepNegative.classList.add('hidden');
            stepPositive.classList.remove('hidden');
            // Auto-scroll slightly to positive container
            stepPositive.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        } else {
            // Negative Flow -> Strictly skip AI, show private feedback
            stepPositive.classList.add('hidden');
            stepNegative.classList.remove('hidden');
            stepNegative.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
    }

    // AI Generation Handler
    const btnGenerateAi = document.getElementById('btn-generate-ai');
    btnGenerateAi.addEventListener('click', async () => {
        if (selectedTags.size === 0) {
            // Select default first tag if none selected
            if (rawTags.length > 0) {
                selectedTags.add(rawTags[0]);
                const firstChip = chipsContainer.querySelector('.chip');
                if (firstChip) firstChip.classList.add('selected');
            }
        }

        const customNote = document.getElementById('custom-note-input').value.trim();

        // Show loading state
        const originalBtnHtml = btnGenerateAi.innerHTML;
        btnGenerateAi.disabled = true;
        btnGenerateAi.innerHTML = `
            <i class="fa-solid fa-circle-notch fa-spin"></i>
            <span>AI is writing reviews...</span>
        `;

        try {
            const resp = await fetch('/api/reviews/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    business_id: BUSINESS_DATA.id,
                    rating: currentRating,
                    selected_tags: Array.from(selectedTags),
                    custom_note: customNote
                })
            });

            if (!resp.ok) {
                const errData = await resp.json().catch(() => ({}));
                throw new Error(errData.detail || 'Could not generate review drafts');
            }

            const data = await resp.json();
            currentDrafts = data.drafts || [];
            
            // Render the 3 AI drafts
            renderDrafts(currentDrafts);

            const badge = document.getElementById('ai-engine-badge');
            if (badge && data.engine) {
                badge.innerHTML = `<i class="fa-solid fa-sparkles text-amber-500 mr-1"></i>${data.engine.includes('OpenAI') ? 'GPT-4o mini' : 'Smart AI'}`;
            }

            // Reveal drafts step
            document.getElementById('step-positive').classList.add('hidden');
            const stepAiResults = document.getElementById('step-ai-results');
            stepAiResults.classList.remove('hidden');
            stepAiResults.scrollIntoView({ behavior: 'smooth', block: 'start' });

        } catch (err) {
            console.error('Error generating AI review:', err);
            showToast(err.message || 'Error creating review drafts. Please try again.', 'error');
        } finally {
            btnGenerateAi.disabled = false;
            btnGenerateAi.innerHTML = originalBtnHtml;
        }
    });

    function renderDrafts(drafts) {
        const list = document.getElementById('drafts-list');
        list.innerHTML = '';

        if (!drafts || drafts.length === 0) {
            list.innerHTML = '<p class="text-xs text-slate-500">No drafts available.</p>';
            return;
        }

        selectedDraftIndex = 0;
        const textarea = document.getElementById('final-review-text');
        textarea.value = drafts[0].text;

        drafts.forEach((draft, idx) => {
            const card = document.createElement('div');
            const isActive = idx === selectedDraftIndex;
            card.className = `review-card p-3 rounded-xl border text-left cursor-pointer ${isActive ? 'active-review' : 'border-slate-200 bg-white hover:bg-slate-50'}`;
            
            const toneIcon = idx === 0 ? 'fa-heart text-rose-500' : (idx === 1 ? 'fa-bolt text-amber-500' : 'fa-list-check text-blue-500');

            card.innerHTML = `
                <div class="flex items-center justify-between mb-1">
                    <span class="text-[11px] font-bold text-slate-700 flex items-center">
                        <i class="fa-solid ${toneIcon} mr-1.5 text-xs"></i>${draft.tone || `Option ${idx + 1}`}
                    </span>
                    <span class="check-indicator text-xs font-bold text-brand-600 ${isActive ? 'opacity-100' : 'opacity-0'}">
                        <i class="fa-solid fa-circle-check"></i>
                    </span>
                </div>
                <p class="text-xs text-slate-600 line-clamp-3 leading-relaxed">${escapeHtml(draft.text)}</p>
            `;

            card.addEventListener('click', () => {
                selectedDraftIndex = idx;
                document.querySelectorAll('.review-card').forEach(c => {
                    c.classList.remove('active-review');
                    const ind = c.querySelector('.check-indicator');
                    if (ind) ind.classList.add('opacity-0');
                });
                card.classList.add('active-review');
                const ind = card.querySelector('.check-indicator');
                if (ind) ind.classList.remove('opacity-0');

                textarea.value = draft.text;
                // subtle bounce
                textarea.classList.add('ring-2', 'ring-brand-400');
                setTimeout(() => textarea.classList.remove('ring-2', 'ring-brand-400'), 400);
            });

            list.appendChild(card);
        });
    }

    // "Post to Google" CTA Click
    const btnPostGoogle = document.getElementById('btn-post-google');
    btnPostGoogle.addEventListener('click', async () => {
        const textToCopy = document.getElementById('final-review-text').value.trim();
        
        // 1. Copy to clipboard
        await copyToClipboard(textToCopy);
        showToast('Review text copied to clipboard!', 'success');

        // 2. Log completion analytics
        recordAnalyticsEvent('completed_review', currentRating);

        // 3. Show redirect modal and guide user
        const modal = document.getElementById('redirect-modal');
        modal.classList.remove('hidden');

        // Automatically open Google review link in new tab / redirect after 1.5 seconds
        setTimeout(() => {
            window.open(BUSINESS_DATA.googleReviewUrl, '_blank');
        }, 1200);
    });

    // Private Feedback Form Submission (1-3 stars)
    const feedbackForm = document.getElementById('private-feedback-form');
    feedbackForm.addEventListener('submit', async (e) => {
        e.preventDefault();

        const message = document.getElementById('feedback-message').value.trim();
        const name = document.getElementById('feedback-name').value.trim();
        const contact = document.getElementById('feedback-contact').value.trim();

        if (!message) {
            showToast('Please provide your feedback', 'error');
            return;
        }

        const submitBtn = document.getElementById('btn-submit-feedback');
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-1"></i> Sending...';

        try {
            const resp = await fetch('/api/feedback/submit', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    business_id: BUSINESS_DATA.id,
                    rating: currentRating,
                    feedback_text: message,
                    customer_name: name,
                    customer_contact: contact
                })
            });

            if (!resp.ok) {
                throw new Error('Failed to submit feedback');
            }

            // Hide negative form, show grateful thank you screen
            document.getElementById('step-negative').classList.add('hidden');
            document.getElementById('step-feedback-thanks').classList.remove('hidden');
            showToast('Feedback submitted to management', 'success');

        } catch (err) {
            console.error('Error submitting feedback:', err);
            showToast('Unable to send feedback. Please try again.', 'error');
            submitBtn.disabled = false;
            submitBtn.innerHTML = '<i class="fa-solid fa-paper-plane text-xs mr-1"></i> Send Private Feedback';
        }
    });

    // Helper: Analytics Logger
    async function recordAnalyticsEvent(eventType, rating = null) {
        try {
            await fetch('/api/analytics/event', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    business_id: BUSINESS_DATA.id,
                    event_type: eventType,
                    rating: rating
                })
            });
        } catch (e) {
            // Non-blocking telemetry
        }
    }

    // Helper: Safe Clipboard Copy
    async function copyToClipboard(text) {
        if (navigator.clipboard && window.isSecureContext) {
            try {
                await navigator.clipboard.writeText(text);
                return true;
            } catch (err) {
                console.warn('Navigator clipboard error, trying fallback', err);
            }
        }
        // Fallback for older mobile webviews or insecure context
        const textArea = document.createElement("textarea");
        textArea.value = text;
        textArea.style.position = "fixed";
        textArea.style.left = "-999999px";
        textArea.style.top = "-999999px";
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        try {
            document.execCommand('copy');
            textArea.remove();
            return true;
        } catch (err) {
            textArea.remove();
            return false;
        }
    }

    function escapeHtml(str) {
        return str
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }
});
