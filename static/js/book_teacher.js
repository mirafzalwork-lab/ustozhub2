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

    // Намерение из CTA на странице учителя (?trial=1&subject=N): предзаполняем
    // модалку, иначе обещание «бесплатный пробный» терялось при открытии.
    const _urlParams = new URLSearchParams(window.location.search);
    const pendingTrial = _urlParams.get('trial') === '1';
    const pendingSubject = _urlParams.get('subject') || '';

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
    const $trialWrap = $trial ? $trial.closest('.cal-field') : null;
    const $message = document.getElementById('book-message');
    const $error = document.getElementById('book-error');
    const $confirm = document.getElementById('book-confirm');
    const $price = document.getElementById('book-price');
    const $elig = document.getElementById('book-elig');

    // Состояние права ученика (из eligibility API): доступен ли бесплатный
    // пробный, нужен ли депозит и его сумма. Backend — источник истины; фронт
    // только отображает. Загружается один раз для авторизованного ученика.
    let elig = null;

    function eligBalance() {
        if (elig && elig.wallet_balance != null) return parseFloat(elig.wallet_balance);
        if (typeof cfg.balance === 'number') return cfg.balance;
        return null;
    }

    // Баннер статуса над формой: «первый урок бесплатный» либо «нужен депозит».
    function renderElig() {
        if (!$elig) return;
        if (!cfg.isStudent || !elig) { $elig.hidden = true; return; }
        const sum = i18n.priceSum || 'сум';
        if (elig.free_trial_available) {
            $elig.className = 'book-elig book-elig--free';
            $elig.innerHTML = '<i class="fa-solid fa-gift"></i><div><strong>' +
                (i18n.eligFreeTitle || 'Ваш первый урок — бесплатный') + '</strong>' +
                '<span class="book-elig__sub">' + (i18n.eligFreeSub || '') + '</span></div>';
            $elig.hidden = false;
            return;
        }
        if (elig.deposit_required) {
            const dep = parseFloat(elig.deposit_amount || '0');
            const depFmt = dep.toLocaleString(cfg.locale);
            let inner = '<i class="fa-solid fa-shield-halved"></i><div><strong>' +
                (i18n.eligDepTitle || 'Требуется депозит') + ' — ' + depFmt + ' ' + sum + '</strong>' +
                '<span class="book-elig__sub">' + (i18n.eligDepSub || '') + '</span>';
            const bal = eligBalance();
            if (bal != null && elig.sufficient_balance === false) {
                const need = Math.max(dep - bal, 0).toLocaleString(cfg.locale);
                const url = cfg.urls.topup + '?amount=' + Math.round(dep) +
                    '&next=' + encodeURIComponent(location.pathname);
                inner += '<span class="book-elig__sub">' + (i18n.priceBalance || 'На балансе:') + ' ' +
                    bal.toLocaleString(cfg.locale) + ' ' + sum + ' — ' + (i18n.priceNotEnough || 'не хватает') +
                    ' ' + need + '.</span><a class="book-topup-link" href="' + url + '">' +
                    (i18n.topup || 'Пополнить баланс') + '</a>';
            }
            inner += '</div>';
            $elig.className = 'book-elig book-elig--deposit';
            $elig.innerHTML = inner;
            $elig.hidden = false;
            return;
        }
        $elig.hidden = true;
    }
    // Экран подтверждения
    const $formRegion = document.getElementById('book-form-region');
    const $success = document.getElementById('book-success');
    const $successWhen = document.getElementById('book-success-when');
    const $foot = document.getElementById('book-foot');
    const $successFoot = document.getElementById('book-success-foot');
    const $another = document.getElementById('book-another');
    const $mybookingsLink = document.getElementById('book-mybookings-link');
    // Live-states
    const $stateViews = $success ? $success.querySelectorAll('[data-state-view]') : [];
    const $cdMin = document.getElementById('book-countdown-min');
    const $cdSec = document.getElementById('book-countdown-sec');
    const $cdFill = document.getElementById('book-countdown-fill');
    const $countdown = document.getElementById('book-countdown');
    const $cdLabel = document.getElementById('book-countdown-label');
    const $cdTime = document.getElementById('book-countdown-time');
    const $cdUntil = document.getElementById('book-countdown-until');
    const $cdBar = document.getElementById('book-countdown-bar');
    const $confirmedWhen = document.getElementById('book-confirmed-when');
    const $confirmedReply = document.getElementById('book-confirmed-reply');
    const $confirmedMeeting = document.getElementById('book-confirmed-meeting');
    const $rejectedReply = document.getElementById('book-rejected-reply');
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
        const trialPrice = parseFloat(opt.dataset.trialPrice || '0');
        const trialDuration = parseInt(opt.dataset.trialDuration || '60', 10);

        // Право ученика (eligibility): бесплатный пробный доступен / нужен депозит.
        // Пока не загружено — считаем пробный доступным, чтобы UI не мигал.
        const freeAvail = !elig || elig.free_trial_available === true;
        const depReq = !!(elig && elig.deposit_required === true);

        // Чекбокс «пробный»: бесплатный — только пока доступен; платный — всегда.
        const hasTrial = (isFreeTrial && freeAvail) || trialPrice > 0;
        if ($trialWrap) $trialWrap.style.display = hasTrial ? '' : 'none';
        if (!hasTrial && $trial.checked) $trial.checked = false;

        // Пробный — платный: используем точную сумму trial_price из TeacherSubject
        if ($trial.checked && !isFreeTrial && trialPrice > 0) {
            $price.className = 'book-price is-trial-paid';
            let html = '<i class="fa-solid fa-gem"></i> ' +
                (i18n.priceTrialPaid || 'Платный пробный:') + ' <strong>' +
                trialPrice.toLocaleString(cfg.locale) + ' ' + (i18n.priceSum || 'сум') + '</strong> ' +
                '<span class="book-price__rate">(' + trialDuration + ' ' +
                (i18n.priceMinutes || 'мин') + ')</span>';
            // Баланс ученика известен заранее — показываем нехватку ДО клика.
            if (typeof cfg.balance === 'number') {
                const bal = cfg.balance.toLocaleString(cfg.locale);
                if (cfg.balance < trialPrice) {
                    const need = (trialPrice - cfg.balance).toLocaleString(cfg.locale);
                    const url = cfg.urls.topup + '?amount=' + Math.round(trialPrice) +
                        '&next=' + encodeURIComponent(location.pathname);
                    html += '<div class="book-price__warn">' +
                        (i18n.priceBalance || 'На балансе:') + ' ' + bal + ' ' + (i18n.priceSum || 'сум') +
                        ' — ' + (i18n.priceNotEnough || 'не хватает') + ' ' + need + '. ' +
                        '<a class="book-topup-link" href="' + url + '">' +
                        (i18n.topup || 'Пополнить баланс') + '</a></div>';
                } else {
                    html += '<div class="book-price__ok">' +
                        (i18n.priceBalance || 'На балансе:') + ' ' + bal + ' ' + (i18n.priceSum || 'сум') + '</div>';
                }
            }
            $price.innerHTML = html;
            $price.hidden = false;
            return;
        }

        // Депозит требуется (бесплатный пробный израсходован) → разовый урок = депозит.
        if (depReq) {
            const dep = parseFloat(elig.deposit_amount || '0');
            $price.className = 'book-price is-deposit';
            let html = '<i class="fa-solid fa-shield-halved"></i> ' +
                (i18n.depositTitle || 'Оплата урока (депозит):') + ' <strong>' +
                dep.toLocaleString(cfg.locale) + ' ' + (i18n.priceSum || 'сум') + '</strong>' +
                '<div class="book-price__note">' + (i18n.depositNote || '') + '</div>';
            const bal = eligBalance();
            if (bal != null && elig.sufficient_balance === false) {
                const need = Math.max(dep - bal, 0).toLocaleString(cfg.locale);
                const url = cfg.urls.topup + '?amount=' + Math.round(dep) +
                    '&next=' + encodeURIComponent(location.pathname);
                html += '<div class="book-price__warn">' +
                    (i18n.priceBalance || 'На балансе:') + ' ' + bal.toLocaleString(cfg.locale) + ' ' + (i18n.priceSum || 'сум') +
                    ' — ' + (i18n.priceNotEnough || 'не хватает') + ' ' + need + '. ' +
                    '<a class="book-topup-link" href="' + url + '">' +
                    (i18n.topup || 'Пополнить баланс') + '</a></div>';
            } else if (bal != null) {
                html += '<div class="book-price__ok">' +
                    (i18n.priceBalance || 'На балансе:') + ' ' + bal.toLocaleString(cfg.locale) + ' ' + (i18n.priceSum || 'сум') + '</div>';
            }
            $price.innerHTML = html;
            $price.hidden = false;
            return;
        }

        // Бесплатный пробный доступен → первая бронь ученика бесплатна (даже без
        // отметки «пробный» — backend делает первый урок пробным). Либо гость
        // отметил бесплатный пробный до загрузки eligibility.
        if ((elig && elig.free_trial_available) || ($trial.checked && isFreeTrial)) {
            $price.className = 'book-price is-free';
            $price.innerHTML = '<i class="fa-solid fa-gift"></i> ' +
                ((elig && elig.free_trial_available)
                    ? (i18n.priceFirstFree || 'Первый урок бесплатно (пробный)')
                    : (i18n.priceFree || 'Пробный урок — бесплатно'));
            $price.hidden = false;
            return;
        }

        // Обычный урок
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
        // Предзаполняем из CTA-намерения (пробный по конкретному предмету);
        // это значения по умолчанию — пользователь может их изменить.
        $trial.checked = pendingTrial;
        if ($subject) $subject.value = pendingSubject;
        // показать форму, скрыть успех
        $formRegion.hidden = false;
        $success.hidden = true;
        $foot.hidden = false;
        $successFoot.hidden = true;
        renderElig();
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
        stopWatcher();
        document.removeEventListener('keydown', _btTrapTab, true);
        document.removeEventListener('keydown', _btTrapEsc);
        if (_btPrevFocus && typeof _btPrevFocus.focus === 'function') {
            try { _btPrevFocus.focus(); } catch (_) {}
        }
        _btPrevFocus = null;
    }

    // ============================================================
    // Live booking watcher: countdown + WS listener
    // ============================================================
    let watcherTimer = null;
    let watcherDeadline = null;
    let watcherTotalMs = 0;
    let watcherBookingId = null;

    function showState(state) {
        if (!$success) return;
        $success.dataset.state = state;
        $stateViews.forEach(view => {
            view.hidden = view.dataset.stateView !== state;
        });
        updateFooterForState(state);
    }

    function updateFooterForState(state) {
        if (!$successFoot) return;
        const ctaPrimary = $successFoot.querySelector('.btn-primary');
        const ctaSecondary = $successFoot.querySelector('.btn-secondary');
        if (state === 'confirmed') {
            if ($mybookingsLink) {
                $mybookingsLink.innerHTML = '<i class="fa fa-list-check"></i> ' + (i18n.ctaMyBookings || 'К моим урокам');
            }
            if (ctaSecondary) ctaSecondary.textContent = i18n.ctaBookAnother || 'Забронировать ещё';
        } else if (state === 'rejected' || state === 'expired') {
            if ($mybookingsLink) {
                $mybookingsLink.href = '#';
                $mybookingsLink.innerHTML = '<i class="fa fa-calendar"></i> ' + (i18n.ctaPickOther || 'Выбрать другой слот');
                $mybookingsLink.onclick = (e) => {
                    e.preventDefault();
                    closeModal();
                    calendar.refetchEvents();
                };
            }
            if (ctaSecondary) ctaSecondary.textContent = i18n.ctaBackToTeacher || 'Назад к учителю';
        }
    }

    function startWatcher(booking, fallbackWhen) {
        watcherBookingId = booking.id;
        const expiresAt = booking.expires_at ? new Date(booking.expires_at).getTime() : null;
        watcherDeadline = expiresAt;
        watcherTotalMs = expiresAt ? Math.max(expiresAt - Date.now(), 0) : 0;

        if ($successWhen) $successWhen.textContent = fallbackWhen;
        if ($confirmedWhen) $confirmedWhen.textContent = fallbackWhen;

        showState('waiting');
        tickCountdown();
        if (watcherTimer) clearInterval(watcherTimer);
        watcherTimer = setInterval(tickCountdown, 1000);
        window.addEventListener('ustoz:ws', onWsEvent);
    }

    function stopWatcher() {
        if (watcherTimer) { clearInterval(watcherTimer); watcherTimer = null; }
        window.removeEventListener('ustoz:ws', onWsEvent);
        watcherBookingId = null;
        watcherDeadline = null;
    }

    function tickCountdown() {
        if (!watcherDeadline || !$countdown) return;
        const remainingMs = Math.max(watcherDeadline - Date.now(), 0);
        const totalSec = Math.floor(remainingMs / 1000);

        // Дальняя бронь (>90 мин до дедлайна): тикающий ММ:СС бессмысленен —
        // показываем абсолютный дедлайн «подтвердит до DD.MM HH:MM».
        if (totalSec > 90 * 60) {
            if ($cdLabel && $countdown.dataset.labelUntil) $cdLabel.textContent = $countdown.dataset.labelUntil;
            if ($cdTime) $cdTime.hidden = true;
            if ($cdBar) $cdBar.hidden = true;
            if ($cdUntil) {
                $cdUntil.hidden = false;
                const loc = document.documentElement.lang || undefined;
                $cdUntil.textContent = new Date(watcherDeadline).toLocaleString(loc, {
                    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
                });
            }
            $countdown.classList.remove('is-urgent');
            return;
        }

        // Ближняя бронь: обычный тикающий таймер ММ:СС.
        if ($cdLabel && $countdown.dataset.labelSoon) $cdLabel.textContent = $countdown.dataset.labelSoon;
        if ($cdTime) $cdTime.hidden = false;
        if ($cdBar) $cdBar.hidden = false;
        if ($cdUntil) $cdUntil.hidden = true;
        if (!$cdMin || !$cdSec) return;
        const m = Math.floor(totalSec / 60);
        const s = totalSec % 60;
        $cdMin.textContent = String(m).padStart(2, '0');
        $cdSec.textContent = String(s).padStart(2, '0');
        if ($cdFill && watcherTotalMs > 0) {
            const pct = Math.max((remainingMs / watcherTotalMs) * 100, 0);
            $cdFill.style.width = pct + '%';
        }
        // Срочно → красный когда осталось <2 мин (или <20% времени)
        const urgent = totalSec <= 120 || (watcherTotalMs && remainingMs / watcherTotalMs < 0.2);
        $countdown.classList.toggle('is-urgent', urgent);
        if (remainingMs <= 0) {
            // Локальный таймер закончился — показываем expired (сервер уже сам освободит слот)
            handleExpired();
        }
    }

    function onWsEvent(e) {
        const data = e.detail || {};
        if (data.type !== 'booking_status_changed') return;
        const p = data.payload || {};
        if (!watcherBookingId || p.booking_id !== watcherBookingId) return;
        if (p.decision === 'confirmed') {
            handleConfirmed(p);
        } else if (p.decision === 'rejected') {
            handleRejected(p);
        }
    }

    function handleConfirmed(payload) {
        if ($confirmedReply) $confirmedReply.textContent = payload.teacher_reply || '';
        if ($confirmedMeeting) {
            // Ведём в нашу комнату урока (учёт присутствия/доска/спор), а не на
            // сырой meeting_url: вход мимо комнаты ломает фиксацию присутствия.
            const roomHref = payload.lesson_room_url || payload.meeting_url;
            if (roomHref) {
                $confirmedMeeting.href = roomHref;
                // Своя страница — открываем в том же табе, не в новом окне.
                if (payload.lesson_room_url) $confirmedMeeting.removeAttribute('target');
                $confirmedMeeting.hidden = false;
            } else {
                $confirmedMeeting.hidden = true;
            }
        }
        showState('confirmed');
        if (watcherTimer) { clearInterval(watcherTimer); watcherTimer = null; }
        window.removeEventListener('ustoz:ws', onWsEvent);
    }

    function handleRejected(payload) {
        if ($rejectedReply) $rejectedReply.textContent = payload.teacher_reply || '';
        showState('rejected');
        if (watcherTimer) { clearInterval(watcherTimer); watcherTimer = null; }
        window.removeEventListener('ustoz:ws', onWsEvent);
        // Слот может вернуться в free после reject — refresh
        try { calendar.refetchEvents(); } catch (_) {}
    }

    function handleExpired() {
        showState('expired');
        if (watcherTimer) { clearInterval(watcherTimer); watcherTimer = null; }
        window.removeEventListener('ustoz:ws', onWsEvent);
        try { calendar.refetchEvents(); } catch (_) {}
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
            err.data = data;
            throw err;
        }
        return data;
    }

    const calendar = new FullCalendar.Calendar(el, {
        locale: cfg.locale || 'ru',
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
        // Подписи кнопок и дни недели локализует сам FullCalendar из бандла
        // locales-all (включая uz). Раньше locale uz подменялся на en + кастомный
        // buttonText форсил английский — из-за этого uz не локализовался.
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

    // Право ученика на бронь (бесплатный пробный / депозит). Backend решает;
    // фронт лишь отображает. Тихо игнорируем ошибку (баннер просто не покажется,
    // enforcement всё равно на сервере). Перезагружаем после брони — первый урок
    // мог израсходовать бесплатный пробный, дальше нужен депозит.
    function loadElig() {
        if (!cfg.isStudent || !cfg.urls.eligibility) return;
        api('GET', cfg.urls.eligibility)
            .then(d => { elig = d; renderElig(); refreshPrice(); })
            .catch(() => {});
    }
    loadElig();

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
            const resp = await api('POST', cfg.urls.book, body);
            const booking = (resp && resp.booking) ? resp.booking : {};
            // Live watcher: countdown до expires_at + WS-listener на decision
            $formRegion.hidden = true;
            $foot.hidden = true;
            $success.hidden = false;
            $successFoot.hidden = false;
            startWatcher(booking, bookedWhen);
            // Забронированный слот больше не свободен — убираем с календаря
            const ev = calendar.getEventById(bookedId);
            if (ev) ev.remove();
            // Право могло измениться (первый урок израсходовал пробный) — обновим.
            loadElig();
        } catch (e) {
            // Бесплатный пробный уже использован — обновим статус, чтобы UI
            // переключился на депозит.
            if (e.status === 409 && e.data && e.data.code === 'free_trial_used') {
                loadElig();
            }
            const topupUrl = e.data && e.data.topup_url;
            if (topupUrl) {
                // Недостаточно средств → показываем кнопку «Пополнить баланс»,
                // которая ведёт на пополнение и возвращает обратно к бронированию.
                $error.textContent = '';
                const msg = document.createElement('div');
                msg.textContent = e.message;
                msg.style.marginBottom = '10px';
                const btn = document.createElement('a');
                btn.href = topupUrl;
                btn.className = 'btn btn-primary';
                btn.textContent = (cfg.i18n && cfg.i18n.topup) || 'Пополнить баланс';
                $error.appendChild(msg);
                $error.appendChild(btn);
            } else {
                $error.textContent = e.message;
            }
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
