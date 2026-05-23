/* ============================================================
 * UstozHub — My Bookings page
 * Список с фильтрами + действия (confirm/reject/cancel)
 * ============================================================ */
(function () {
    'use strict';

    const cfg = window.UstozMyBookings || {};
    const $list = document.getElementById('bk-list');
    const $filters = document.getElementById('bk-filters');
    let currentStatus = '';

    // ---- api ----
    async function api(method, url, body) {
        const opts = {
            method,
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': cfg.csrf,
            },
            credentials: 'same-origin',
        };
        if (body !== undefined) opts.body = JSON.stringify(body);
        const res = await fetch(url, opts);
        const data = res.headers.get('content-type')?.includes('json') ? await res.json() : {};
        if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
        return data;
    }

    // ---- render ----
    function statusBadgeClass(s) {
        if (s === 'pending') return 'pending';
        if (s === 'confirmed') return 'confirmed';
        if (s === 'completed') return 'completed';
        if (s === 'expired') return 'expired';
        return 'cancelled';
    }

    function renderCountdown(expiresAt) {
        if (!expiresAt) return '';
        const d = new Date(expiresAt);
        const ms = d - new Date();
        if (ms <= 0) return '<span class="bk-countdown">истекло</span>';
        // В последний час — живой отсчёт, иначе дата дедлайна (за час до урока)
        if (ms <= 3600000) {
            const m = Math.floor(ms / 60000);
            const s = Math.floor((ms % 60000) / 1000);
            return `<span class="bk-countdown">⏱ осталось ${m}:${String(s).padStart(2,'0')}</span>`;
        }
        const str = d.toLocaleString('ru', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' });
        return `<span class="bk-countdown">⏱ подтвердить до ${str}</span>`;
    }

    function renderCard(b) {
        const start = new Date(b.slot.start);
        const end = new Date(b.slot.end);
        const fmtTime = d => d.toLocaleTimeString(cfg.locale, { hour: '2-digit', minute: '2-digit' });
        const monthName = start.toLocaleString(cfg.locale, { month: 'short' });

        // Чьё имя показывать: студент видит учителя, учитель — студента
        const counterparty = cfg.role === 'student' ? b.teacher : b.student;
        const counterpartyLabel = cfg.role === 'student' ? 'с' : 'для';

        const subjectMeta = b.subject ? `<span>${escapeHtml(b.subject.name)}</span>` : '';
        const trialMeta = b.is_trial ? '<span>🎁 пробный</span>' : '';
        const messageBlock = b.student_message
            ? `<div class="msg">📩 ${escapeHtml(b.student_message)}</div>` : '';
        const teacherReplyBlock = b.teacher_reply
            ? `<div class="msg">💬 ${escapeHtml(b.teacher_reply)}</div>` : '';

        // Действия по роли и статусу
        const actions = renderActions(b);

        const meetingUrlMeta = b.meeting_url
            ? `<div class="msg" style="background:#EEF2FF; color:#3730A3;">🎥 ${escapeHtml(b.meeting_url)}</div>` : '';

        return `
            <div class="bk-card" data-id="${b.id}" data-teacher-id="${b.teacher.id}" data-meeting-url="${escapeHtml(b.meeting_url || '')}">
                <div class="bk-date">
                    <div class="day">${start.getDate()}</div>
                    <div class="month">${monthName}</div>
                    <div class="time">${fmtTime(start)}–${fmtTime(end)}</div>
                </div>
                <div class="bk-info">
                    <h3>${counterpartyLabel} ${escapeHtml(counterparty.name)}</h3>
                    <div class="meta">
                        <span class="bk-status ${statusBadgeClass(b.status)}">${b.status_display}</span>
                        ${b.status === 'pending' ? renderCountdown(b.expires_at) : ''}
                        ${subjectMeta}
                        ${trialMeta}
                        <span>${b.slot.duration_minutes} мин</span>
                    </div>
                    ${messageBlock}
                    ${teacherReplyBlock}
                    ${meetingUrlMeta}
                </div>
                <div class="bk-actions">
                    ${actions}
                </div>
            </div>
        `;
    }

    function renderActions(b) {
        const buttons = [];

        // Join Lesson — приоритет для confirmed с meeting_url в окне [-15min, end].
        // Наш Jitsi открываем во встроенной комнате внутри сайта,
        // кастомные внешние ссылки (Zoom и т.п.) — напрямую.
        if (b.status === 'confirmed' && b.meeting_url && isJoinable(b)) {
            const href = (b.meeting_is_jitsi && b.lesson_room_url) ? b.lesson_room_url : b.meeting_url;
            buttons.push(`<a class="bk-btn join" href="${href}" target="_blank" rel="noopener">
                <i class="fa-solid fa-video"></i> ${cfg.i18n.joinLesson}
            </a>`);
        }

        if (cfg.role === 'teacher' && b.status === 'pending') {
            buttons.push(`<button class="bk-btn primary" data-action="confirm">${cfg.i18n.confirm}</button>`);
            buttons.push(`<button class="bk-btn danger" data-action="reject">${cfg.i18n.reject}</button>`);
        }
        // Добавить в календарь (.ics) — для активных уроков, обе роли
        if ((b.status === 'pending' || b.status === 'confirmed') && cfg.urls.ical) {
            buttons.push(`<a class="bk-btn secondary" href="${cfg.urls.ical.replace('__ID__', b.id)}"
                title="${cfg.i18n.addToCalendar}" aria-label="${cfg.i18n.addToCalendar}">
                <i class="fa-regular fa-calendar-plus"></i></a>`);
        }
        if (cfg.role === 'teacher' && b.status === 'confirmed') {
            // Учитель может задать/изменить ссылку
            const lbl = b.meeting_url ? cfg.i18n.editLink : cfg.i18n.setLink;
            buttons.push(`<button class="bk-btn secondary" data-action="set-link">${lbl}</button>`);
            buttons.push(`<button class="bk-btn danger" data-action="cancel">${cfg.i18n.cancel}</button>`);
        }
        if (cfg.role === 'student' && (b.status === 'pending' || b.status === 'confirmed')) {
            // Перенос на другой свободный слот
            buttons.push(`<button class="bk-btn secondary" data-action="reschedule">
                <i class="fa-regular fa-clock"></i> ${cfg.i18n.reschedule || 'Перенести'}</button>`);
            buttons.push(`<button class="bk-btn danger" data-action="cancel">${cfg.i18n.cancel}</button>`);
        }
        // Завершённый урок — ученик может оставить/обновить отзыв
        if (cfg.role === 'student' && b.status === 'completed' && b.review_url) {
            const lbl = b.has_review ? (cfg.i18n.editReview || 'Изменить отзыв')
                                     : (cfg.i18n.leaveReview || 'Оставить отзыв');
            buttons.push(`<a class="bk-btn ${b.has_review ? 'secondary' : 'primary'}" href="${b.review_url}">
                <i class="fa-solid fa-star"></i> ${lbl}
            </a>`);
        }
        return buttons.join('');
    }

    function isJoinable(b) {
        // Кнопка Join видна за 15 минут до start_at и до конца урока
        const start = new Date(b.slot.start);
        const end = new Date(b.slot.end);
        const now = new Date();
        return (start - now <= 15 * 60 * 1000) && (now <= end);
    }

    function escapeHtml(s) {
        return String(s || '').replace(/[&<>"']/g, c => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        }[c]));
    }

    // ---- modal / toast (замена браузерных prompt/confirm/alert) ----
    const $overlay = document.getElementById('bkm-overlay');
    const $mTitle = document.getElementById('bkm-title');
    const $mText = document.getElementById('bkm-text');
    const $mBody = document.getElementById('bkm-body');
    const $mOk = document.getElementById('bkm-ok');
    const $mCancel = document.getElementById('bkm-cancel');
    let _modalResolve = null;
    let _previousFocus = null;   // a11y: куда вернуть фокус после закрытия

    function _focusables(root) {
        return root.querySelectorAll(
            'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), ' +
            'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        );
    }

    function _trapTab(e) {
        if (e.key !== 'Tab' || !$overlay.classList.contains('is-open')) return;
        const f = _focusables($overlay);
        if (!f.length) return;
        const first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) {
            e.preventDefault(); last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault(); first.focus();
        }
    }

    function closeModal(result) {
        $overlay.classList.remove('is-open');
        $overlay.setAttribute('aria-hidden', 'true');
        document.removeEventListener('keydown', _trapTab, true);
        const r = _modalResolve; _modalResolve = null;
        if (r) r(result);
        // a11y: возвращаем фокус на элемент, открывший модалку
        if (_previousFocus && typeof _previousFocus.focus === 'function') {
            try { _previousFocus.focus(); } catch (_) {}
        }
        _previousFocus = null;
    }

    /**
     * Открывает модалку. opts: { title, text, fields:[{name,label,hint,type,value,placeholder}],
     * okText, okClass, danger }. Возвращает Promise: объект значений полей или null (отмена).
     */
    function modal(opts) {
        return new Promise(resolve => {
            _modalResolve = resolve;
            $mTitle.textContent = opts.title || '';
            $mText.textContent = opts.text || '';
            $mText.style.display = opts.text ? '' : 'none';
            $mBody.innerHTML = (opts.fields || []).map(f => {
                let ctrl;
                if (f.type === 'textarea') {
                    ctrl = `<textarea data-field="${f.name}" placeholder="${escapeHtml(f.placeholder||'')}">${escapeHtml(f.value||'')}</textarea>`;
                } else if (f.type === 'select') {
                    const opts2 = (f.options || []).map(o =>
                        `<option value="${escapeHtml(String(o.value))}">${escapeHtml(o.label)}</option>`).join('');
                    ctrl = `<select data-field="${f.name}">${opts2}</select>`;
                } else {
                    ctrl = `<input type="text" data-field="${f.name}" value="${escapeHtml(f.value||'')}" placeholder="${escapeHtml(f.placeholder||'')}">`;
                }
                return `<div class="bkm-field">
                    ${f.label ? `<label>${escapeHtml(f.label)}</label>` : ''}
                    ${ctrl}
                    ${f.hint ? `<div class="bkm-hint">${escapeHtml(f.hint)}</div>` : ''}
                </div>`;
            }).join('');
            $mOk.textContent = opts.okText || cfg.i18n.ok || 'OK';
            $mOk.className = 'bkm-btn ' + (opts.okClass || 'primary');
            $mCancel.textContent = cfg.i18n.cancelBtn || 'Cancel';
            // a11y: запоминаем активный элемент, чтобы вернуть фокус при закрытии
            _previousFocus = document.activeElement;
            $overlay.classList.add('is-open');
            $overlay.setAttribute('aria-hidden', 'false');
            // Перехватываем Tab внутри модалки (focus trap)
            document.addEventListener('keydown', _trapTab, true);
            // Фокусируем первое интерактивное поле; если нет — OK-кнопку
            const first = $mBody.querySelector('[data-field]') || $mOk;
            if (first) first.focus();
        });
    }

    $mOk.addEventListener('click', () => {
        const values = {};
        $mBody.querySelectorAll('[data-field]').forEach(el => {
            values[el.dataset.field] = el.value.trim();
        });
        closeModal(values);
    });
    $mCancel.addEventListener('click', () => closeModal(null));
    $overlay.addEventListener('click', e => { if (e.target === $overlay) closeModal(null); });
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape' && $overlay.classList.contains('is-open')) closeModal(null);
    });

    let _toastTimer = null;
    function toast(msg, isError) {
        const $t = document.getElementById('bk-toast');
        $t.textContent = msg;
        $t.className = 'bk-toast is-open' + (isError ? ' error' : '');
        clearTimeout(_toastTimer);
        _toastTimer = setTimeout(() => { $t.className = 'bk-toast'; }, 3500);
    }

    async function loadList() {
        $list.innerHTML = '<div class="bk-empty"><i class="fa-regular fa-clock"></i><div>Загрузка...</div></div>';
        try {
            const qs = currentStatus ? `?status=${currentStatus}` : '';
            const data = await api('GET', cfg.urls.list + qs);
            if (!data.bookings.length) {
                $list.innerHTML = `<div class="bk-empty"><i class="fa-regular fa-calendar-xmark"></i><div>${cfg.i18n.empty}</div></div>`;
                return;
            }
            $list.innerHTML = data.bookings.map(renderCard).join('');
        } catch (e) {
            $list.innerHTML = `<div class="bk-empty">Ошибка: ${escapeHtml(e.message)}</div>`;
        }
    }

    // ---- events ----
    $filters.addEventListener('click', e => {
        if (!e.target.matches('.bk-filter')) return;
        $filters.querySelectorAll('.bk-filter').forEach(b => b.classList.remove('is-active'));
        e.target.classList.add('is-active');
        currentStatus = e.target.dataset.status || '';
        loadList();
    });

    $list.addEventListener('click', async e => {
        const btn = e.target.closest('[data-action]');
        if (!btn) return;
        const card = btn.closest('.bk-card');
        const id = card?.dataset.id;
        if (!id) return;
        const action = btn.dataset.action;

        let url, body;
        if (action === 'confirm') {
            const res = await modal({
                title: cfg.i18n.confirmTitle,
                okText: cfg.i18n.confirm,
                okClass: 'primary',
                fields: [
                    { name: 'meeting_url', label: cfg.i18n.meetingUrlLabel, hint: cfg.i18n.meetingUrlHint, type: 'text', placeholder: 'https://…' },
                    { name: 'reply', label: cfg.i18n.replyLabel, type: 'textarea' },
                ],
            });
            if (res === null) return;
            url = cfg.urls.confirm.replace('__ID__', id);
            body = { reply: res.reply || '', meeting_url: res.meeting_url || '' };
        } else if (action === 'reject') {
            const res = await modal({
                title: cfg.i18n.rejectTitle,
                okText: cfg.i18n.reject,
                okClass: 'danger',
                fields: [{ name: 'reply', label: cfg.i18n.reasonLabel, type: 'textarea' }],
            });
            if (res === null) return;
            url = cfg.urls.reject.replace('__ID__', id);
            body = { reply: res.reply || '' };
        } else if (action === 'cancel') {
            const res = await modal({
                title: cfg.i18n.cancelTitle,
                text: cfg.i18n.confirmCancel,
                okText: cfg.i18n.cancel,
                okClass: 'danger',
            });
            if (res === null) return;
            url = cfg.urls.cancel.replace('__ID__', id);
            body = {};
        } else if (action === 'set-link') {
            const current = card.dataset.meetingUrl || '';
            const res = await modal({
                title: cfg.i18n.setLinkTitle,
                okText: cfg.i18n.ok,
                fields: [{ name: 'meeting_url', label: cfg.i18n.meetingUrlLabel, hint: cfg.i18n.meetingUrlHint, type: 'text', value: current, placeholder: 'https://…' }],
            });
            if (res === null) return;
            url = cfg.urls.setLink.replace('__ID__', id);
            body = { meeting_url: res.meeting_url || '' };
        } else if (action === 'reschedule') {
            // Перенос: грузим свободные слоты учителя → выбор → POST reschedule
            const teacherId = card.dataset.teacherId;
            btn.disabled = true;
            let slots = [];
            try {
                const now = new Date();
                const end = new Date(now.getTime() + 30 * 24 * 3600 * 1000);
                const u = cfg.urls.publicSlots.replace('__TID__', teacherId) +
                    `?start=${encodeURIComponent(now.toISOString())}&end=${encodeURIComponent(end.toISOString())}`;
                const data = await api('GET', u);
                slots = data.events || [];
            } catch (err) {
                toast('Ошибка: ' + err.message, true); btn.disabled = false; return;
            }
            btn.disabled = false;
            if (!slots.length) { toast(cfg.i18n.noFreeSlots, true); return; }
            const fmt = s => new Date(s.start).toLocaleString(cfg.locale,
                { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
            const res = await modal({
                title: cfg.i18n.rescheduleTitle,
                text: cfg.i18n.rescheduleHint,
                okText: cfg.i18n.reschedule,
                fields: [{
                    name: 'slot_id', label: cfg.i18n.pickSlot, type: 'select',
                    options: slots.map(s => ({ value: s.id, label: fmt(s) })),
                }],
            });
            if (res === null) return;
            try {
                await api('POST', cfg.urls.reschedule.replace('__ID__', id),
                    { slot_id: parseInt(res.slot_id, 10) });
                toast(cfg.i18n.rescheduled);
                await loadList();
            } catch (err) {
                toast('Ошибка: ' + err.message, true);
            }
            return;
        } else {
            return;
        }

        btn.disabled = true;
        try {
            await api('POST', url, body);
            await loadList();
        } catch (e) {
            toast('Ошибка: ' + e.message, true);
            btn.disabled = false;
        }
    });

    // ---- realtime: обновляемся по WS-пушу (см. base.html → 'ustoz:ws') ----
    let _refreshTimer = null;
    function refreshSoon() {
        clearTimeout(_refreshTimer);
        _refreshTimer = setTimeout(loadList, 400); // дебаунс пачки событий
    }
    window.addEventListener('ustoz:ws', e => {
        const t = (e.detail || {}).type;
        if (t === 'new_notification' || t === 'booking_update') refreshSoon();
    });

    // Фолбэк-поллинг (на случай если WS недоступен) — реже, чем раньше
    setInterval(loadList, 60000);
    // Обновляемся при возврате на вкладку
    document.addEventListener('visibilitychange', () => { if (!document.hidden) loadList(); });

    loadList();
})();
