/* ============================================================
 * UstozHub — Book teacher (Phase 3)
 * Read-only календарь со свободными слотами + modal для бронирования
 * ============================================================ */
(function () {
    'use strict';

    const cfg = window.UstozBook || {};
    const el = document.getElementById('calendar');
    if (!el) return;

    const modal = document.getElementById('book-modal');
    const $when = document.getElementById('book-when');
    const $duration = document.getElementById('book-duration');
    const $subject = document.getElementById('book-subject');
    const $trial = document.getElementById('book-trial');
    const $message = document.getElementById('book-message');
    const $error = document.getElementById('book-error');
    const $confirm = document.getElementById('book-confirm');

    let selectedSlot = null;

    function openModal(slot) {
        selectedSlot = slot;
        const start = new Date(slot.start);
        const end = new Date(slot.end);
        const fmt = d => d.toLocaleString(cfg.locale, {
            day: '2-digit', month: 'long', hour: '2-digit', minute: '2-digit',
        });
        $when.textContent = fmt(start) + ' – ' + end.toLocaleTimeString(cfg.locale, { hour: '2-digit', minute: '2-digit' });
        $duration.textContent = slot.extendedProps.duration_minutes + ' мин';
        $error.hidden = true;
        $message.value = '';
        $trial.checked = false;
        if ($subject) $subject.value = '';
        modal.hidden = false;
    }

    function closeModal() {
        modal.hidden = true;
        selectedSlot = null;
    }

    modal.addEventListener('click', e => {
        if (e.target.matches('[data-close]')) closeModal();
    });

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
        const text = await res.text();
        let data;
        try { data = text ? JSON.parse(text) : {}; } catch { data = { error: text }; }
        if (!res.ok) {
            const err = new Error(data.error || `HTTP ${res.status}`);
            err.status = res.status;
            throw err;
        }
        return data;
    }

    const calendar = new FullCalendar.Calendar(el, {
        locale: cfg.locale === 'uz' ? 'en' : (cfg.locale || 'ru'),
        initialView: 'timeGridWeek',
        firstDay: 1,
        nowIndicator: true,
        slotMinTime: '07:00:00',
        slotMaxTime: '23:00:00',
        slotDuration: '00:30:00',
        scrollTime: '09:00:00',
        height: 'auto',
        expandRows: true,
        selectable: false,
        editable: false,
        weekNumbers: false,
        headerToolbar: {
            left: 'prev,next today',
            center: 'title',
            right: 'timeGridDay,timeGridWeek',
        },
        buttonText: {
            today: cfg.locale === 'ru' ? 'Сегодня' : 'Today',
            day:   cfg.locale === 'ru' ? 'День' : 'Day',
            week:  cfg.locale === 'ru' ? 'Неделя' : 'Week',
        },
        validRange: { start: new Date() }, // нельзя смотреть в прошлое
        events: async (info, success, failure) => {
            try {
                const url = `${cfg.urls.slots}?start=${encodeURIComponent(info.startStr)}&end=${encodeURIComponent(info.endStr)}`;
                const data = await api('GET', url);
                success(data.events);
            } catch (e) {
                console.error('slots load failed', e);
                failure(e);
            }
        },
        eventClick: info => {
            // Auth checks
            if (!cfg.isAuth) {
                if (confirm(cfg.i18n.loginRequired)) {
                    window.location.href = cfg.urls.login;
                }
                return;
            }
            if (!cfg.isStudent) {
                alert(cfg.i18n.teachersCantBook);
                return;
            }
            openModal({
                id: info.event.id,
                start: info.event.start,
                end: info.event.end,
                extendedProps: info.event.extendedProps,
            });
        },
    });

    calendar.render();

    $confirm.addEventListener('click', async () => {
        if (!selectedSlot) return;
        $error.hidden = true;
        $confirm.disabled = true;
        try {
            const body = {
                slot_id: parseInt(selectedSlot.id, 10),
                subject_id: $subject ? (parseInt($subject.value, 10) || null) : null,
                is_trial: $trial.checked,
                message: $message.value.trim().slice(0, 500),
            };
            const data = await api('POST', cfg.urls.book, body);
            alert(cfg.i18n.success);
            // Перенаправляем на "Мои бронирования"
            window.location.href = cfg.urls.myBookings;
        } catch (e) {
            $error.textContent = e.message;
            $error.hidden = false;
        } finally {
            $confirm.disabled = false;
        }
    });
})();
