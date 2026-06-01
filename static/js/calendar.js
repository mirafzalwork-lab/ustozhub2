/* ============================================================
 * UstozHub — Teacher Calendar (FullCalendar v6 + AJAX CRUD)
 * ============================================================ */
(function () {
    'use strict';

    const cfg = window.UstozCalendar || {};
    const i18n = cfg.i18n || {};
    const el = document.getElementById('calendar');
    if (!el) return;

    const DURATION_PRESETS = [30, 45, 60, 90, 120];

    // ---- Toast helper (вместо alert) -----------------------------------
    let _toastTimer = null;
    function toast(msg, isError) {
        let $t = document.getElementById('cal-toast');
        if (!$t) {
            $t = document.createElement('div');
            $t.id = 'cal-toast';
            $t.style.cssText = 'position:fixed;left:50%;bottom:28px;transform:translateX(-50%) translateY(20px);' +
                'background:#0f172a;color:#fff;padding:12px 20px;border-radius:10px;font-size:14px;' +
                'z-index:9999;opacity:0;pointer-events:none;transition:opacity .2s,transform .2s;max-width:90vw;';
            document.body.appendChild($t);
        }
        $t.textContent = msg;
        $t.style.background = isError ? '#B91C1C' : '#0f172a';
        $t.style.opacity = '1';
        $t.style.transform = 'translateX(-50%) translateY(0)';
        clearTimeout(_toastTimer);
        _toastTimer = setTimeout(() => {
            $t.style.opacity = '0';
            $t.style.transform = 'translateX(-50%) translateY(20px)';
        }, 3500);
    }

    // ---- Slot modal elements ------------------------------------------
    const modal = document.getElementById('cal-modal');
    const $title = document.getElementById('cal-modal-title');
    const $start = document.getElementById('cal-start');
    const $end = document.getElementById('cal-end');
    const $endField = document.getElementById('cal-end-field');
    const $durationChips = document.getElementById('cal-duration-chips');
    const $status = document.getElementById('cal-status');
    const $save = document.getElementById('cal-save');
    const $delete = document.getElementById('cal-delete');
    const $bookingInfo = document.getElementById('cal-booking-info');
    const $bkStudent = document.getElementById('cal-bk-student');
    const $bkStatus = document.getElementById('cal-bk-status');
    const $bkMessage = document.getElementById('cal-bk-message');
    const $error = document.getElementById('cal-error');

    let editingEvent = null;   // FullCalendar EventApi (для edit)
    let editingId = null;      // id редактируемого слота
    let durationMode = 60;     // выбранная длительность в минутах или 'custom'

    // ---- Duration chips -----------------------------------------------
    function setDurationMode(min) {
        durationMode = min;
        Array.from($durationChips.querySelectorAll('.cal-chip')).forEach(chip => {
            const val = chip.dataset.min === 'custom' ? 'custom' : parseInt(chip.dataset.min, 10);
            chip.classList.toggle('is-active', val === min);
        });
        $endField.hidden = (min !== 'custom');
        if (min === 'custom' && $start.value && !$end.value) {
            // подставим +60 мин для удобства
            $end.value = addMinutes($start.value, 60);
        }
    }

    $durationChips.addEventListener('click', (e) => {
        const chip = e.target.closest('.cal-chip');
        if (!chip) return;
        const val = chip.dataset.min === 'custom' ? 'custom' : parseInt(chip.dataset.min, 10);
        setDurationMode(val);
    });

    // ---- Modal open/close ---------------------------------------------
    function openCreateModal(startDate) {
        editingEvent = null;
        editingId = null;
        $title.textContent = i18n.newSlot || 'Новый слот';
        $start.value = toLocalInput(startDate);
        $start.disabled = $status.disabled = false;
        $status.value = 'free';
        setDurationMode(60);
        $end.value = '';
        $bookingInfo.hidden = true;
        $delete.hidden = true;
        $save.hidden = false;
        $error.hidden = true;
        modal.hidden = false;
        _calActivateTrap($start);
    }

    function openEditModal(event) {
        editingEvent = event;
        editingId = event.id;
        const props = event.extendedProps || {};
        const booking = props.booking;

        $title.textContent = booking ? (i18n.booking || 'Бронирование') : (i18n.slot || 'Слот');
        $start.value = toLocalInput(event.start);
        $status.value = props.status || 'free';
        $error.hidden = true;

        // Определяем длительность → выставляем chip или custom
        const mins = Math.round((event.end - event.start) / 60000);
        if (DURATION_PRESETS.indexOf(mins) !== -1) {
            setDurationMode(mins);
            $end.value = toLocalInput(event.end);
        } else {
            setDurationMode('custom');
            $end.value = toLocalInput(event.end);
        }

        if (booking) {
            $bookingInfo.hidden = false;
            $bkStudent.textContent = booking.student_name;
            $bkStatus.textContent = booking.status_display + (booking.is_trial ? ' (пробный)' : '');
            $bkMessage.textContent = booking.student_message || '—';
            // Слот с активной бронью — read-only
            $start.disabled = $status.disabled = true;
            $durationChips.style.pointerEvents = 'none';
            $durationChips.style.opacity = '.5';
            $save.hidden = true;
            $delete.hidden = true;
        } else {
            $bookingInfo.hidden = true;
            $start.disabled = $status.disabled = false;
            $durationChips.style.pointerEvents = '';
            $durationChips.style.opacity = '';
            $save.hidden = false;
            $delete.hidden = false;
        }
        modal.hidden = false;
        _calActivateTrap();
    }

    function closeModal() {
        modal.hidden = true;
        editingEvent = null;
        editingId = null;
        document.removeEventListener('keydown', _calTrapTab, true);
        if (_calPrevFocus && typeof _calPrevFocus.focus === 'function') {
            try { _calPrevFocus.focus(); } catch (_) {}
        }
        _calPrevFocus = null;
    }

    // ---- a11y: focus trap для модалки слота ---------------------------
    let _calPrevFocus = null;
    function _calFocusables(root) {
        return root.querySelectorAll(
            'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), ' +
            'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        );
    }
    function _calTrapTab(e) {
        if (e.key !== 'Tab' || modal.hidden) return;
        const f = _calFocusables(modal);
        if (!f.length) return;
        const first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
    function _calActivateTrap(targetEl) {
        _calPrevFocus = document.activeElement;
        document.addEventListener('keydown', _calTrapTab, true);
        const f = _calFocusables(modal);
        if (targetEl) targetEl.focus(); else if (f.length) f[0].focus();
        // Esc для закрытия
        const onEsc = (e) => {
            if (e.key === 'Escape' && !modal.hidden) { closeModal(); document.removeEventListener('keydown', onEsc); }
        };
        document.addEventListener('keydown', onEsc);
    }

    modal.addEventListener('click', (e) => {
        if (e.target.matches('[data-close]')) closeModal();
    });

    // ---- API helper ----------------------------------------------------
    async function api(method, url, body) {
        const opts = {
            method,
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': cfg.csrf },
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

    function showError($box, msg) {
        $box.textContent = msg;
        $box.hidden = false;
    }

    // Вычислить end (ISO) из текущего состояния модалки
    function computeEndIso() {
        if (durationMode === 'custom') {
            return $end.value ? fromLocalInput($end.value) : null;
        }
        return fromLocalInput(addMinutes($start.value, durationMode));
    }

    // На узких экранах неделя нечитаема — стартуем с дневного вида.
    const isNarrow = window.matchMedia('(max-width: 768px)').matches;

    // ---- FullCalendar setup -------------------------------------------
    const calendar = new FullCalendar.Calendar(el, {
        locale: cfg.locale === 'uz' ? 'en' : (cfg.locale || 'ru'),
        initialView: isNarrow ? 'timeGridDay' : 'timeGridWeek',
        firstDay: 1,
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
        headerToolbar: {
            left: 'prev,next today',
            center: 'title',
            right: 'timeGridDay,timeGridWeek,dayGridMonth,listWeek',
        },
        buttonText: {
            today: cfg.locale === 'ru' ? 'Сегодня' : (cfg.locale === 'uz' ? 'Bugun' : 'Today'),
            day:   cfg.locale === 'ru' ? 'День' : (cfg.locale === 'uz' ? 'Kun' : 'Day'),
            week:  cfg.locale === 'ru' ? 'Неделя' : (cfg.locale === 'uz' ? 'Hafta' : 'Week'),
            month: cfg.locale === 'ru' ? 'Месяц' : (cfg.locale === 'uz' ? 'Oy' : 'Month'),
            list:  cfg.locale === 'ru' ? 'Список' : (cfg.locale === 'uz' ? 'Roʻyxat' : 'List'),
        },

        events: async (info, success, failure) => {
            try {
                const url = `${cfg.urls.list}?start=${encodeURIComponent(info.startStr)}&end=${encodeURIComponent(info.endStr)}`;
                const data = await api('GET', url);
                renderStats(data.events);
                success(data.events);
            } catch (e) {
                console.error('events load failed', e);
                failure(e);
            }
        },

        // Drag-select → мгновенное создание свободного слота
        select: async (info) => {
            try {
                const data = await api('POST', cfg.urls.create, {
                    start: info.startStr,
                    end: info.endStr,
                    status: 'free',
                });
                calendar.addEvent(data.event);
            } catch (e) {
                toast((i18n.createFailed || 'Не удалось создать слот:') + ' ' + e.message, true);
            } finally {
                calendar.unselect();
            }
        },

        eventClick: (info) => openEditModal(info.event),

        eventDrop: async (info) => await patchEvent(info),
        eventResize: async (info) => await patchEvent(info),
    });

    async function patchEvent(info) {
        // Запоминаем прежние границы ДО запроса — для возможной отмены.
        const oldStart = info.oldEvent ? info.oldEvent.startStr : null;
        const oldEnd = info.oldEvent ? info.oldEvent.endStr : null;
        try {
            const data = await api('PATCH', cfg.urls.detail.replace('__ID__', info.event.id), {
                start: info.event.startStr,
                end: info.event.endStr,
            });
            info.event.setProp('backgroundColor', data.event.backgroundColor);
            info.event.setProp('borderColor', data.event.borderColor);
            // Двигать можно только свободные слоты, отмена безопасна.
            if (oldStart && oldEnd) {
                const id = info.event.id;
                showUndo(i18n.undoMoved || 'Слот перемещён.', async () => {
                    await api('PATCH', cfg.urls.detail.replace('__ID__', id), { start: oldStart, end: oldEnd });
                    calendar.refetchEvents();
                });
            }
        } catch (e) {
            info.revert();
            toast((i18n.moveFailed || 'Не удалось переместить слот:') + ' ' + e.message, true);
        }
    }

    // ---- Бейдж занятости за открытый период ---------------------------
    const $stats = document.getElementById('cal-stats');
    function renderStats(events) {
        if (!$stats) return;
        const c = { free: 0, held: 0, booked: 0, blocked: 0 };
        (events || []).forEach(ev => {
            const s = (ev.extendedProps && ev.extendedProps.status) || 'free';
            if (c[s] !== undefined) c[s] += 1;
        });
        const total = c.free + c.held + c.booked + c.blocked;
        if (total === 0) {
            $stats.innerHTML = '<span class="cal-stat cal-stat--muted">' +
                '<i class="fa-regular fa-calendar-xmark"></i> ' + (i18n.statsEmpty || 'Нет слотов в этом периоде.') + '</span>';
            $stats.hidden = false;
            return;
        }
        const bookedActive = c.booked + c.held;
        const parts = [
            '<span class="cal-stat cal-stat--free"><b>' + c.free + '</b> ' + (i18n.statsFree || 'свободно') + '</span>',
            '<span class="cal-stat cal-stat--booked"><b>' + bookedActive + '</b> ' + (i18n.statsBooked || 'забронировано') + '</span>',
        ];
        if (c.blocked) {
            parts.push('<span class="cal-stat cal-stat--blocked"><b>' + c.blocked + '</b> ' + (i18n.statsBlocked || 'заблокировано') + '</span>');
        }
        $stats.innerHTML = parts.join('');
        $stats.hidden = false;
    }

    // ---- Undo-тост -----------------------------------------------------
    const $undo = document.getElementById('cal-undo');
    const $undoText = document.getElementById('cal-undo-text');
    const $undoBtn = document.getElementById('cal-undo-btn');
    let undoTimer = null;
    let undoFn = null;

    function showUndo(text, fn) {
        if (!$undo) return;
        undoFn = fn;
        $undoText.textContent = text;
        $undo.hidden = false;
        $undo.classList.add('is-visible');
        clearTimeout(undoTimer);
        undoTimer = setTimeout(hideUndo, 8000);
    }
    function hideUndo() {
        if (!$undo) return;
        $undo.classList.remove('is-visible');
        clearTimeout(undoTimer);
        undoFn = null;
        // даём отыграть transition перед скрытием
        setTimeout(() => { if (!$undo.classList.contains('is-visible')) $undo.hidden = true; }, 250);
    }
    if ($undoBtn) {
        $undoBtn.addEventListener('click', async () => {
            const fn = undoFn;
            hideUndo();
            if (!fn) return;
            try { await fn(); } catch (e) { toast((i18n.undoFailed || 'Не удалось отменить действие.') + ' ' + e.message, true); }
        });
    }

    // ---- Slot modal save / delete -------------------------------------
    $save.addEventListener('click', async () => {
        $error.hidden = true;
        const startIso = fromLocalInput($start.value);
        if (!startIso) { showError($error, i18n.needStart || 'Укажите время начала.'); return; }
        const endIso = computeEndIso();
        if (!endIso) { showError($error, i18n.needStart || 'Укажите время конца.'); return; }

        const body = { start: startIso, end: endIso, status: $status.value };
        try {
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
            showError($error, e.message);
        }
    });

    $delete.addEventListener('click', async () => {
        if (!editingId) return;
        const _confirmText = i18n.confirmDeleteSlot || 'Удалить этот слот?';
        const _ok = window.confirmDialog
            ? await window.confirmDialog(_confirmText, { danger: true })
            : confirm(_confirmText);
        if (!_ok) return;
        // Сохраняем данные слота, чтобы можно было восстановить.
        const snapshot = editingEvent ? {
            start: editingEvent.start.toISOString(),
            end: editingEvent.end ? editingEvent.end.toISOString() : null,
            status: (editingEvent.extendedProps || {}).status || 'free',
        } : null;
        try {
            await api('DELETE', cfg.urls.detail.replace('__ID__', editingId));
            if (editingEvent) editingEvent.remove();
            closeModal();
            if (snapshot && snapshot.end) {
                showUndo(i18n.undoDeleted || 'Слот удалён.', async () => {
                    const data = await api('POST', cfg.urls.create, snapshot);
                    calendar.addEvent(data.event);
                    calendar.refetchEvents();
                });
            }
        } catch (e) {
            showError($error, e.message);
        }
    });

    // ---- "Создать слот" button ----------------------------------------
    const $createBtn = document.getElementById('create-slot-btn');
    if ($createBtn) {
        $createBtn.addEventListener('click', () => openCreateModal(nextHalfHour()));
    }

    // ---- Generate modal (с редактором недельного шаблона) -------------
    const genModal = document.getElementById('gen-modal');
    const $genWeeks = document.getElementById('gen-weeks');
    const $genDuration = document.getElementById('gen-duration');
    const $genRun = document.getElementById('gen-run');
    const $genError = document.getElementById('gen-error');
    const $genResult = document.getElementById('gen-result');
    const $genPreview = document.getElementById('gen-preview');
    const $weekEditor = document.getElementById('gen-week-editor');
    const $presets = document.querySelector('.cal-presets');
    const $bulkGenerate = document.getElementById('bulk-generate-btn');

    const DAY_LABELS = cfg.dayLabels || [
        ['monday', 'Пн'], ['tuesday', 'Вт'], ['wednesday', 'Ср'],
        ['thursday', 'Чт'], ['friday', 'Пт'], ['saturday', 'Сб'], ['sunday', 'Вс'],
    ];
    const PRESETS = {
        weekdays: { days: ['monday', 'tuesday', 'wednesday', 'thursday', 'friday'], from: '09:00', to: '18:00' },
        everyday: { days: DAY_LABELS.map(d => d[0]), from: '10:00', to: '20:00' },
        evenings: { days: DAY_LABELS.map(d => d[0]), from: '18:00', to: '22:00' },
    };

    // Минуты от 'HH:MM'
    function hm2min(v) {
        if (!v || v.indexOf(':') === -1) return null;
        const [h, m] = v.split(':').map(Number);
        return h * 60 + m;
    }

    function makeGenIntervalRow(from, to) {
        const row = document.createElement('div');
        row.className = 'cal-week-interval';
        row.innerHTML =
            '<input type="time" class="cal-week-from" value="' + (from || '09:00') + '">' +
            '<span class="cal-week-dash">—</span>' +
            '<input type="time" class="cal-week-to" value="' + (to || '12:00') + '">' +
            '<button type="button" class="cal-week-rm" title="Удалить">&times;</button>';
        row.querySelector('.cal-week-rm').addEventListener('click', () => { row.remove(); refreshPreview(); });
        row.querySelectorAll('input[type=time]').forEach(i => i.addEventListener('change', refreshPreview));
        return row;
    }

    function buildWeekEditor(schedule) {
        $weekEditor.innerHTML = '';
        DAY_LABELS.forEach(([key, label]) => {
            const intervals = (schedule && schedule[key]) || [];
            const on = intervals.length > 0;
            const row = document.createElement('div');
            row.className = 'cal-week-day' + (on ? ' is-on' : '');
            row.dataset.day = key;
            row.innerHTML =
                '<label class="cal-week-toggle">' +
                    '<input type="checkbox" ' + (on ? 'checked' : '') + '>' +
                    '<span>' + label + '</span>' +
                '</label>' +
                '<div class="cal-week-intervals"></div>' +
                '<div class="cal-week-actions">' +
                    '<button type="button" class="cal-week-copy" title="' + (i18n.copyDay || 'Скопировать этот день в другие') + '">' +
                        '<i class="fa-regular fa-copy"></i></button>' +
                    '<button type="button" class="cal-week-add"><i class="fa-solid fa-plus"></i> ' +
                        (i18n.addInterval || 'интервал') + '</button>' +
                '</div>';

            const list = row.querySelector('.cal-week-intervals');
            const toggle = row.querySelector('input[type=checkbox]');
            const addBtn = row.querySelector('.cal-week-add');
            const copyBtn = row.querySelector('.cal-week-copy');

            intervals.forEach(itv => list.appendChild(makeGenIntervalRow(itv.from, itv.to)));

            toggle.addEventListener('change', () => {
                if (toggle.checked && list.children.length === 0) {
                    list.appendChild(makeGenIntervalRow('09:00', '12:00'));
                }
                row.classList.toggle('is-on', toggle.checked);
                refreshPreview();
            });
            addBtn.addEventListener('click', () => {
                if (!toggle.checked) { toggle.checked = true; row.classList.add('is-on'); }
                list.appendChild(makeGenIntervalRow('', ''));
                refreshPreview();
            });
            copyBtn.addEventListener('click', (e) => { e.stopPropagation(); openCopyMenu(row, key); });
            $weekEditor.appendChild(row);
        });
    }

    // Прочитать интервалы конкретного дня прямо из редактора (даже невалидные пропускаем)
    function getDayIntervals(key) {
        const row = $weekEditor.querySelector('.cal-week-day[data-day="' + key + '"]');
        if (!row || !row.querySelector('input[type=checkbox]').checked) return [];
        const out = [];
        row.querySelectorAll('.cal-week-interval').forEach(ir => {
            const f = ir.querySelector('.cal-week-from').value;
            const t = ir.querySelector('.cal-week-to').value;
            if (f && t && hm2min(f) < hm2min(t)) out.push({ from: f, to: t });
        });
        return out;
    }

    // Скопировать интервалы дня source в указанные дни (перезаписывая их)
    function applyDayTo(sourceKey, targetKeys) {
        const src = getDayIntervals(sourceKey);
        if (!src.length) return;
        const sched = collectSchedule();
        sched[sourceKey] = src.map(i => ({ ...i }));
        targetKeys.forEach(k => { if (k !== sourceKey) sched[k] = src.map(i => ({ ...i })); });
        buildWeekEditor(sched);
        refreshPreview();
    }

    // Поповер «скопировать день в…»
    let $copyMenu = null;
    function closeCopyMenu() { if ($copyMenu) { $copyMenu.remove(); $copyMenu = null; } }
    function openCopyMenu(row, sourceKey) {
        closeCopyMenu();
        if (!getDayIntervals(sourceKey).length) return; // нечего копировать
        const weekdayKeys = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday'];
        const allKeys = DAY_LABELS.map(d => d[0]);
        $copyMenu = document.createElement('div');
        $copyMenu.className = 'cal-week-copy-menu';
        $copyMenu.innerHTML =
            '<button type="button" data-target="weekdays">' + (i18n.copyToWeekdays || 'В будни (Пн–Пт)') + '</button>' +
            '<button type="button" data-target="all">' + (i18n.copyToAll || 'Во все дни') + '</button>';
        $copyMenu.addEventListener('click', (e) => {
            const btn = e.target.closest('button');
            if (!btn) return;
            applyDayTo(sourceKey, btn.dataset.target === 'weekdays' ? weekdayKeys : allKeys);
            closeCopyMenu();
        });
        row.querySelector('.cal-week-actions').appendChild($copyMenu);
        // Закрытие по клику вне меню
        setTimeout(() => document.addEventListener('click', onDocClickCopy), 0);
    }
    function onDocClickCopy(e) {
        if ($copyMenu && !$copyMenu.contains(e.target) && !e.target.closest('.cal-week-copy')) {
            closeCopyMenu();
            document.removeEventListener('click', onDocClickCopy);
        }
    }

    // Собрать {day: [{from,to}]} из редактора (только включённые дни с валидными интервалами)
    function collectSchedule() {
        const out = {};
        $weekEditor.querySelectorAll('.cal-week-day').forEach(row => {
            if (!row.querySelector('input[type=checkbox]').checked) return;
            const itvs = [];
            row.querySelectorAll('.cal-week-interval').forEach(ir => {
                const f = ir.querySelector('.cal-week-from').value;
                const t = ir.querySelector('.cal-week-to').value;
                if (f && t && hm2min(f) < hm2min(t)) itvs.push({ from: f, to: t });
            });
            if (itvs.length) out[row.dataset.day] = itvs;
        });
        return out;
    }

    // Живой счётчик: ~ (сумма слотов за неделю) × weeks
    function refreshPreview() {
        const schedule = collectSchedule();
        const slotMin = parseInt($genDuration.value, 10);
        const weeks = parseInt($genWeeks.value, 10);
        let perWeek = 0;
        Object.values(schedule).forEach(itvs => {
            itvs.forEach(itv => {
                const span = hm2min(itv.to) - hm2min(itv.from);
                if (span > 0) perWeek += Math.floor(span / slotMin);
            });
        });
        if (perWeek === 0) {
            $genPreview.innerHTML = '<i class="fa-regular fa-circle-question"></i> ' +
                (i18n.previewEmpty || 'Включите хотя бы один день.');
            $genPreview.classList.add('is-empty');
        } else {
            $genPreview.classList.remove('is-empty');
            $genPreview.innerHTML = '<i class="fa-regular fa-calendar-check"></i> ' +
                (i18n.previewFmt || 'Будет создано примерно') + ' <strong>' + (perWeek * weeks) + '</strong> ' +
                (i18n.previewSlots || 'слотов') + ' ' + (i18n.previewWeeks || 'за период') + '.';
        }
        $genPreview.hidden = false;
    }

    function applyPreset(name) {
        if (name === 'clear') { buildWeekEditor({}); refreshPreview(); return; }
        const p = PRESETS[name];
        if (!p) return;
        const sched = {};
        p.days.forEach(d => { sched[d] = [{ from: p.from, to: p.to }]; });
        buildWeekEditor(sched);
        refreshPreview();
    }

    if ($presets) {
        $presets.addEventListener('click', (e) => {
            const btn = e.target.closest('.cal-preset');
            if (btn) applyPreset(btn.dataset.preset);
        });
    }
    $genWeeks.addEventListener('change', refreshPreview);
    $genDuration.addEventListener('change', refreshPreview);

    function openGen() {
        $genError.hidden = true;
        $genResult.hidden = true;
        $genRun.disabled = false;
        buildWeekEditor(cfg.schedule || {});
        refreshPreview();
        genModal.hidden = false;
    }
    function closeGen() { genModal.hidden = true; }
    if ($bulkGenerate) $bulkGenerate.addEventListener('click', openGen);
    genModal.addEventListener('click', (e) => { if (e.target.matches('[data-close-gen]')) closeGen(); });

    $genRun.addEventListener('click', async () => {
        $genError.hidden = true;
        $genResult.hidden = true;
        const schedule = collectSchedule();
        if (Object.keys(schedule).length === 0) {
            showError($genError, i18n.previewEmpty || 'Включите хотя бы один рабочий день.');
            return;
        }
        $genRun.disabled = true;
        try {
            const data = await api('POST', cfg.urls.bulkGenerate, {
                weeks: parseInt($genWeeks.value, 10),
                slot_minutes: parseInt($genDuration.value, 10),
                schedule: schedule,
            });
            cfg.schedule = schedule;  // запоминаем актуальный шаблон
            $genResult.innerHTML =
                '<i class="fa-solid fa-circle-check"></i> ' +
                (i18n.generatedFmt || 'Создано слотов:') + ' <strong>' + data.created + '</strong><br>' +
                '<span class="cal-muted-line">' + (i18n.skippedFmt || 'Пропущено:') + ' ' + data.skipped + '</span>';
            $genResult.hidden = false;
            calendar.refetchEvents();
        } catch (e) {
            showError($genError, e.message);
        } finally {
            $genRun.disabled = false;
        }
    });

    // ---- Delete modal --------------------------------------------------
    const delModal = document.getElementById('del-modal');
    const $delRun = document.getElementById('del-run');
    const $delError = document.getElementById('del-error');
    const $delRange = document.getElementById('del-range');
    const $bulkDelete = document.getElementById('bulk-delete-btn');

    function openDel() {
        $delError.hidden = true;
        $delRun.disabled = false;
        const view = calendar.view;
        $delRange.textContent = formatRange(view.currentStart, view.currentEnd);
        delModal.hidden = false;
    }
    function closeDel() { delModal.hidden = true; }
    if ($bulkDelete) $bulkDelete.addEventListener('click', openDel);
    delModal.addEventListener('click', (e) => { if (e.target.matches('[data-close-del]')) closeDel(); });

    $delRun.addEventListener('click', async () => {
        $delError.hidden = true;
        $delRun.disabled = true;
        const view = calendar.view;
        try {
            const data = await api('POST', cfg.urls.bulkDelete, {
                from: view.currentStart.toISOString(),
                to: view.currentEnd.toISOString(),
                only_free: true,
            });
            calendar.refetchEvents();
            closeDel();
            toast((i18n.deletedFmt || 'Удалено свободных слотов:') + ' ' + data.deleted);
        } catch (e) {
            showError($delError, e.message);
        } finally {
            $delRun.disabled = false;
        }
    });

    calendar.render();

    // ---- datetime helpers ---------------------------------------------
    function toLocalInput(d) {
        if (!d) return '';
        const dt = (d instanceof Date) ? d : new Date(d);
        const pad = n => String(n).padStart(2, '0');
        return `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}T${pad(dt.getHours())}:${pad(dt.getMinutes())}`;
    }
    function fromLocalInput(v) {
        if (!v) return null;
        return new Date(v).toISOString();
    }
    function addMinutes(localValue, minutes) {
        if (!localValue) return '';
        const dt = new Date(localValue);
        dt.setMinutes(dt.getMinutes() + minutes);
        return toLocalInput(dt);
    }
    function nextHalfHour() {
        const dt = new Date();
        dt.setSeconds(0, 0);
        const m = dt.getMinutes();
        dt.setMinutes(m <= 30 ? 30 : 60);
        if (dt < new Date()) dt.setMinutes(dt.getMinutes() + 30);
        return dt;
    }
    function formatRange(start, end) {
        const opts = { day: 'numeric', month: 'long' };
        const loc = cfg.locale === 'uz' ? 'ru' : (cfg.locale || 'ru');
        const e = new Date(end.getTime() - 1); // end exclusive
        return start.toLocaleDateString(loc, opts) + ' — ' + e.toLocaleDateString(loc, opts);
    }
})();
