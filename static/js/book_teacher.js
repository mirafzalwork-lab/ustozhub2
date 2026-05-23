/* ============================================================
 * UstozHub — Book teacher (Phase 3)
 * Read-only календарь со свободными слотами + modal для бронирования
 * ============================================================ */
(function () {
    'use strict';

    const cfg = window.UstozBook || {};
    const el = document.getElementById('calendar');
    if (!el) return;

    const i18n = cfg.i18n || {};

    // Toast helper (вместо alert)
    let _toastTimer = null;
    function toast(msg, isError) {
        let $t = document.getElementById('book-toast');
        if (!$t) {
            $t = document.createElement('div');
            $t.id = 'book-toast';
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
    const modal = document.getElementById('book-modal');
    const $when = document.getElementById('book-when');
    const $duration = document.getElementById('book-duration');
    const $subject = document.getElementById('book-subject');
    const $trial = document.getElementById('book-trial');
    const $message = document.getElementById('book-message');
    const $error = document.getElementById('book-error');
    const $confirm = document.getElementById('book-confirm');
    const $price = document.getElementById('book-price');
    // Экран подтверждения
    const $formRegion = document.getElementById('book-form-region');
    const $success = document.getElementById('book-success');
    const $successWhen = document.getElementById('book-success-when');
    const $foot = document.getElementById('book-foot');
    const $successFoot = document.getElementById('book-success-foot');
    const $another = document.getElementById('book-another');
    // Фильтр по времени дня
    const $timeChips = document.getElementById('book-time-chips');
    const $filterHint = document.getElementById('book-filter-hint');
    let timeWindow = 'any';

    let selectedSlot = null;

    const fmtWhen = slot => {
        const start = new Date(slot.start);
        const end = new Date(slot.end);
        const f = d => d.toLocaleString(cfg.locale, {
            day: '2-digit', month: 'long', hour: '2-digit', minute: '2-digit',
        });
        return f(start) + ' – ' + end.toLocaleTimeString(cfg.locale, { hour: '2-digit', minute: '2-digit' });
    };

    // Пересчёт стоимости урока из выбранного предмета и длительности слота
    function refreshPrice() {
        if (!$price) return;
        const opt = $subject && $subject.selectedOptions[0];
        if (!opt || !opt.value) { $price.hidden = true; return; }
        const rate = parseFloat(opt.dataset.rate || '0');
        const isFreeTrial = opt.dataset.freeTrial === '1';
        if ($trial.checked && isFreeTrial) {
            $price.className = 'book-price is-free';
            $price.innerHTML = '<i class="fa-solid fa-gift"></i> ' + (i18n.priceFree || 'Пробный урок — бесплатно');
            $price.hidden = false;
            return;
        }
        const mins = (selectedSlot && selectedSlot.extendedProps.duration_minutes) || 60;
        const cost = Math.round(rate * mins / 60);
        $price.className = 'book-price';
        $price.innerHTML = '<i class="fa-regular fa-money-bill-1"></i> ' +
            (i18n.priceFmt || 'Стоимость урока:') + ' <strong>' + (i18n.priceApprox || '≈') + ' ' +
            cost.toLocaleString(cfg.locale) + ' ' + (i18n.priceSum || 'сум') + '</strong> ' +
            '<span class="book-price__rate">(' + rate.toLocaleString(cfg.locale) + ' ' +
            (i18n.pricePerHour || 'сум/час') + ')</span>';
        $price.hidden = false;
    }

    // ---- a11y: focus trap ----
    let _btPrevFocus = null;
    function _btFocusables() {
        return modal.querySelectorAll(
            'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), ' +
            'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        );
    }
    function _btTrapTab(e) {
        if (e.key !== 'Tab' || modal.hidden) return;
        const f = _btFocusables();
        if (!f.length) return;
        const first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
    function _btTrapEsc(e) {
        if (e.key === 'Escape' && !modal.hidden) closeModal();
    }

    function openModal(slot) {
        selectedSlot = slot;
        $when.textContent = fmtWhen(slot);
        $duration.textContent = slot.extendedProps.duration_minutes + ' ' + (i18n.minutes || 'мин');
        $error.hidden = true;
        $message.value = '';
        $trial.checked = false;
        if ($subject) $subject.value = '';
        // показать форму, скрыть успех
        $formRegion.hidden = false;
        $success.hidden = true;
        $foot.hidden = false;
        $successFoot.hidden = true;
        refreshPrice();
        modal.hidden = false;
        // a11y: trap + restore focus
        _btPrevFocus = document.activeElement;
        document.addEventListener('keydown', _btTrapTab, true);
        document.addEventListener('keydown', _btTrapEsc);
        const f = _btFocusables();
        if (f.length) f[0].focus();
    }

    function closeModal() {
        modal.hidden = true;
        selectedSlot = null;
        document.removeEventListener('keydown', _btTrapTab, true);
        document.removeEventListener('keydown', _btTrapEsc);
        if (_btPrevFocus && typeof _btPrevFocus.focus === 'function') {
            try { _btPrevFocus.focus(); } catch (_) {}
        }
        _btPrevFocus = null;
    }

    modal.addEventListener('click', e => {
        if (e.target.matches('[data-close]')) closeModal();
    });

    if ($subject) $subject.addEventListener('change', refreshPrice);
    $trial.addEventListener('change', refreshPrice);

    // ---- Фильтр по времени дня ----------------------------------------
    function inWindow(date) {
        if (timeWindow === 'any') return true;
        const h = new Date(date).getHours();
        if (timeWindow === 'morning') return h < 12;
        if (timeWindow === 'day') return h >= 12 && h < 17;
        if (timeWindow === 'evening') return h >= 17;
        return true;
    }
    if ($timeChips) {
        $timeChips.addEventListener('click', e => {
            const chip = e.target.closest('.book-time-chip');
            if (!chip) return;
            timeWindow = chip.dataset.window;
            $timeChips.querySelectorAll('.book-time-chip').forEach(c => c.classList.toggle('is-active', c === chip));
            calendar.refetchEvents();
        });
    }

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
                const filtered = (data.events || []).filter(ev => inWindow(ev.start));
                if ($filterHint) {
                    if (timeWindow !== 'any' && filtered.length === 0) {
                        $filterHint.innerHTML = '<i class="fa-regular fa-circle-question"></i> ' + (i18n.filterNone || 'Нет свободных слотов в выбранное время дня.');
                    } else {
                        $filterHint.innerHTML = '<strong>' + filtered.length + '</strong> ' + (i18n.filterCount || 'слотов в этом периоде');
                    }
                }
                success(filtered);
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
                toast(cfg.i18n.teachersCantBook, true);
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
        const bookedWhen = fmtWhen(selectedSlot);
        const bookedId = selectedSlot.id;
        try {
            const body = {
                slot_id: parseInt(selectedSlot.id, 10),
                subject_id: $subject ? (parseInt($subject.value, 10) || null) : null,
                is_trial: $trial.checked,
                message: $message.value.trim().slice(0, 500),
            };
            await api('POST', cfg.urls.book, body);
            // Экран подтверждения вместо alert + мгновенного редиректа
            $successWhen.textContent = bookedWhen;
            $formRegion.hidden = true;
            $foot.hidden = true;
            $success.hidden = false;
            $successFoot.hidden = false;
            // Забронированный слот больше не свободен — убираем с календаря
            const ev = calendar.getEventById(bookedId);
            if (ev) ev.remove();
        } catch (e) {
            $error.textContent = e.message;
            $error.hidden = false;
        } finally {
            $confirm.disabled = false;
        }
    });

    if ($another) {
        $another.addEventListener('click', () => {
            closeModal();
            calendar.refetchEvents();
        });
    }
})();
