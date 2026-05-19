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
        const ms = new Date(expiresAt) - new Date();
        if (ms <= 0) return '<span class="bk-countdown">истекло</span>';
        const m = Math.floor(ms / 60000);
        const s = Math.floor((ms % 60000) / 1000);
        return `<span class="bk-countdown">⏱ ${m}:${String(s).padStart(2,'0')}</span>`;
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
            <div class="bk-card" data-id="${b.id}" data-meeting-url="${escapeHtml(b.meeting_url || '')}">
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

        // Join Lesson — приоритет для confirmed с meeting_url в окне [-15min, end]
        if (b.status === 'confirmed' && b.meeting_url && isJoinable(b)) {
            buttons.push(`<a class="bk-btn join" href="${b.meeting_url}" target="_blank" rel="noopener">
                <i class="fa-solid fa-video"></i> ${cfg.i18n.joinLesson}
            </a>`);
        }

        if (cfg.role === 'teacher' && b.status === 'pending') {
            buttons.push(`<button class="bk-btn primary" data-action="confirm">${cfg.i18n.confirm}</button>`);
            buttons.push(`<button class="bk-btn danger" data-action="reject">${cfg.i18n.reject}</button>`);
        }
        if (cfg.role === 'teacher' && b.status === 'confirmed') {
            // Учитель может задать/изменить ссылку
            const lbl = b.meeting_url ? cfg.i18n.editLink : cfg.i18n.setLink;
            buttons.push(`<button class="bk-btn secondary" data-action="set-link">${lbl}</button>`);
            buttons.push(`<button class="bk-btn danger" data-action="cancel">${cfg.i18n.cancel}</button>`);
        }
        if (cfg.role === 'student' && (b.status === 'pending' || b.status === 'confirmed')) {
            buttons.push(`<button class="bk-btn danger" data-action="cancel">${cfg.i18n.cancel}</button>`);
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
            const meetingUrl = prompt(cfg.i18n.askMeetingUrl, '');
            if (meetingUrl === null) return; // отмена
            const reply = prompt(cfg.i18n.confirmReply, '');
            if (reply === null) return;
            url = cfg.urls.confirm.replace('__ID__', id);
            body = { reply, meeting_url: (meetingUrl || '').trim() };
        } else if (action === 'reject') {
            const reply = prompt(cfg.i18n.rejectReason, '');
            if (reply === null) return;
            url = cfg.urls.reject.replace('__ID__', id);
            body = { reply };
        } else if (action === 'cancel') {
            if (!confirm(cfg.i18n.confirmCancel)) return;
            url = cfg.urls.cancel.replace('__ID__', id);
            body = {};
        } else if (action === 'set-link') {
            const current = card.dataset.meetingUrl || '';
            const next = prompt(cfg.i18n.askMeetingUrl, current);
            if (next === null) return;
            url = cfg.urls.setLink.replace('__ID__', id);
            body = { meeting_url: (next || '').trim() };
        } else {
            return;
        }

        btn.disabled = true;
        try {
            await api('POST', url, body);
            await loadList();
        } catch (e) {
            alert('Ошибка: ' + e.message);
            btn.disabled = false;
        }
    });

    // Обновление таймеров hold
    setInterval(() => {
        document.querySelectorAll('.bk-card').forEach(card => {
            const cd = card.querySelector('.bk-countdown');
            if (!cd) return;
            // Полная перезагрузка раз в 30 сек — проще чем парсить из DOM
        });
    }, 30000);
    setInterval(loadList, 30000); // refresh раз в 30 сек

    loadList();
})();
