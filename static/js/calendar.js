/* ============================================================
 * UstozHub — Teacher Calendar (FullCalendar v6 + AJAX CRUD)
 * ============================================================ */
(function () {
    'use strict';

    const cfg = window.UstozCalendar || {};
    const el = document.getElementById('calendar');
    if (!el) return;

    // ---- Modal helpers --------------------------------------------------
    const modal = document.getElementById('cal-modal');
    const $title = document.getElementById('cal-modal-title');
    const $start = document.getElementById('cal-start');
    const $end = document.getElementById('cal-end');
    const $status = document.getElementById('cal-status');
    const $save = document.getElementById('cal-save');
    const $delete = document.getElementById('cal-delete');
    const $bookingInfo = document.getElementById('cal-booking-info');
    const $bkStudent = document.getElementById('cal-bk-student');
    const $bkStatus = document.getElementById('cal-bk-status');
    const $bkMessage = document.getElementById('cal-bk-message');
    const $error = document.getElementById('cal-error');

    let editingEvent = null; // FullCalendar EventApi
    let editingId = null;

    function openModal({ title, start, end, status, booking, id }) {
        $title.textContent = title;
        $start.value = toLocalInput(start);
        $end.value = toLocalInput(end);
        $status.value = status || 'free';
        editingId = id || null;
        $delete.hidden = !id;
        $error.hidden = true;

        if (booking) {
            $bookingInfo.hidden = false;
            $bkStudent.textContent = booking.student_name;
            $bkStatus.textContent = booking.status_display + (booking.is_trial ? ' (пробный)' : '');
            $bkMessage.textContent = booking.student_message || '—';
            // Слот с активной бронью — нельзя редактировать
            $start.disabled = $end.disabled = $status.disabled = true;
            $save.hidden = true;
            $delete.hidden = true;
        } else {
            $bookingInfo.hidden = true;
            $start.disabled = $end.disabled = $status.disabled = false;
            $save.hidden = false;
        }
        modal.hidden = false;
    }

    function closeModal() {
        modal.hidden = true;
        editingEvent = null;
        editingId = null;
    }

    modal.addEventListener('click', (e) => {
        if (e.target.matches('[data-close]')) closeModal();
    });

    // ---- API helpers ----------------------------------------------------
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

    function showError(msg) {
        $error.textContent = msg;
        $error.hidden = false;
    }

    // ---- FullCalendar setup --------------------------------------------
    const calendar = new FullCalendar.Calendar(el, {
        locale: cfg.locale === 'uz' ? 'en' : (cfg.locale || 'ru'), // uz нет в FullCalendar, fallback на en
        initialView: 'timeGridWeek',
        firstDay: 1, // Monday
        nowIndicator: true,
        slotMinTime: '07:00:00',
        slotMaxTime: '23:00:00',
        slotDuration: '00:30:00',
        snapDuration: '00:15:00',
        scrollTime: '09:00:00',
        height: 'auto',
        expandRows: true,
        selectable: true,
        selectMirror: true,
        editable: true,
        eventResizableFromStart: true,
        dayMaxEvents: true,
        weekNumbers: false,
        headerToolbar: {
            left: 'prev,next today',
            center: 'title',
            right: 'timeGridDay,timeGridWeek,dayGridMonth',
        },
        buttonText: {
            today: cfg.locale === 'ru' ? 'Сегодня' : (cfg.locale === 'uz' ? 'Bugun' : 'Today'),
            day:   cfg.locale === 'ru' ? 'День' : (cfg.locale === 'uz' ? 'Kun' : 'Day'),
            week:  cfg.locale === 'ru' ? 'Неделя' : (cfg.locale === 'uz' ? 'Hafta' : 'Week'),
            month: cfg.locale === 'ru' ? 'Месяц' : (cfg.locale === 'uz' ? 'Oy' : 'Month'),
        },

        // -------- LOAD events from API --------
        events: async (info, success, failure) => {
            try {
                const url = `${cfg.urls.list}?start=${encodeURIComponent(info.startStr)}&end=${encodeURIComponent(info.endStr)}`;
                const data = await api('GET', url);
                success(data.events);
            } catch (e) {
                console.error('events load failed', e);
                failure(e);
            }
        },

        // -------- CREATE on drag-select --------
        select: async (info) => {
            try {
                const data = await api('POST', cfg.urls.create, {
                    start: info.startStr,
                    end: info.endStr,
                    status: 'free',
                });
                calendar.addEvent(data.event);
            } catch (e) {
                alert('Не удалось создать слот: ' + e.message);
            } finally {
                calendar.unselect();
            }
        },

        // -------- CLICK event --------
        eventClick: (info) => {
            editingEvent = info.event;
            const props = info.event.extendedProps;
            openModal({
                title: props.booking ? 'Бронирование' : 'Слот',
                start: info.event.start,
                end: info.event.end,
                status: props.status,
                booking: props.booking,
                id: info.event.id,
            });
        },

        // -------- DRAG / RESIZE --------
        eventDrop: async (info) => await patchEvent(info),
        eventResize: async (info) => await patchEvent(info),
    });

    async function patchEvent(info) {
        try {
            const data = await api('PATCH', cfg.urls.detail.replace('__ID__', info.event.id), {
                start: info.event.startStr,
                end: info.event.endStr,
            });
            // Обновим event целиком из ответа сервера
            info.event.setProp('backgroundColor', data.event.backgroundColor);
            info.event.setProp('borderColor', data.event.borderColor);
        } catch (e) {
            info.revert();
            alert('Не удалось переместить слот: ' + e.message);
        }
    }

    // -------- MODAL save --------
    $save.addEventListener('click', async () => {
        $error.hidden = true;
        try {
            const body = {
                start: fromLocalInput($start.value),
                end: fromLocalInput($end.value),
                status: $status.value,
            };
            if (editingId) {
                const data = await api('PATCH', cfg.urls.detail.replace('__ID__', editingId), body);
                if (editingEvent) {
                    editingEvent.setStart(data.event.start);
                    editingEvent.setEnd(data.event.end);
                    editingEvent.setProp('backgroundColor', data.event.backgroundColor);
                    editingEvent.setProp('borderColor', data.event.borderColor);
                    editingEvent.setExtendedProp('status', data.event.extendedProps.status);
                }
            } else {
                const data = await api('POST', cfg.urls.create, body);
                calendar.addEvent(data.event);
            }
            closeModal();
        } catch (e) {
            showError(e.message);
        }
    });

    // -------- MODAL delete --------
    $delete.addEventListener('click', async () => {
        if (!editingId || !confirm('Удалить этот слот?')) return;
        try {
            await api('DELETE', cfg.urls.detail.replace('__ID__', editingId));
            if (editingEvent) editingEvent.remove();
            closeModal();
        } catch (e) {
            showError(e.message);
        }
    });

    calendar.render();

    // ---- datetime-local helpers ----------------------------------------
    function toLocalInput(d) {
        if (!d) return '';
        const dt = (d instanceof Date) ? d : new Date(d);
        const pad = n => String(n).padStart(2, '0');
        return `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}T${pad(dt.getHours())}:${pad(dt.getMinutes())}`;
    }

    function fromLocalInput(v) {
        if (!v) return null;
        // Trust the local input; FullCalendar/server understand ISO
        return new Date(v).toISOString();
    }
})();
