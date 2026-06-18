/* ============================================================
 * UstozHub — My Bookings page (Phase 5)
 * Time-based tabs (urgent/upcoming/past) + day grouping +
 * 1-click confirm + quick-reply chips + urgent visual cues
 * ============================================================ */
(function () {
    'use strict';

    const cfg = window.UstozMyBookings || {};
    const i18n = cfg.i18n || {};
    const $list = document.getElementById('bk-list');
    const $filters = document.getElementById('bk-filters');
    const $stats = document.getElementById('bk-stats');

    // Стартовая вкладка берётся из активной кнопки в разметке (учитель → «Срочно»,
    // ученик → «Предстоящие»), а не хардкодится — иначе ученик видел ложно-пустой экран.
    let currentTab = ($filters && $filters.querySelector('.bk-filter.is-active')?.dataset.tab) || 'urgent';
    let allBookings = [];          // полная выгрузка с сервера (без status-фильтра)

    const URGENT_THRESHOLD_MS = 5 * 60 * 1000;       // <5 мин до expires_at — красный пульс
    const UPCOMING_24H_MS = 24 * 60 * 60 * 1000;     // confirmed в ближайшие 24h тоже считаем «срочными» для учителя

    // ---- api ----
    async function api(method, url, body) {
        const opts = {
            method,
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': cfg.csrf },
            credentials: 'same-origin',
        };
        if (body !== undefined) opts.body = JSON.stringify(body);
        const res = await fetch(url, opts);
        const data = res.headers.get('content-type')?.includes('json') ? await res.json() : {};
        if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
        return data;
    }

    function escapeHtml(s) {
        return String(s || '').replace(/[&<>"']/g, c => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        }[c]));
    }

    // ---- classification helpers ----
    function startOfDay(d) {
        const c = new Date(d);
        c.setHours(0, 0, 0, 0);
        return c;
    }
    function daysDiff(a, b) {
        return Math.round((startOfDay(a) - startOfDay(b)) / 86400000);
    }
    function isToday(b) {
        return daysDiff(new Date(b.slot.start), new Date()) === 0;
    }
    function isUrgent(b) {
        if (b.status !== 'pending' || !b.expires_at) return false;
        const ms = new Date(b.expires_at) - new Date();
        return ms > 0 && ms <= URGENT_THRESHOLD_MS;
    }
    function isPast(b) {
        if (b.status === 'completed' || b.status === 'expired'
            || b.status === 'cancelled_by_student' || b.status === 'cancelled_by_teacher'
            || b.status === 'no_show_student' || b.status === 'no_show_teacher') {
            return true;
        }
        return new Date(b.slot.end) < new Date();
    }
    function classifyForTab(b) {
        if (isPast(b)) return 'past';
        if (b.status === 'pending') return 'urgent';
        // confirmed в ближайшие 24 часа → тоже urgent для учителя (видеть «скоро»)
        if (b.status === 'confirmed' && cfg.role === 'teacher') {
            const ms = new Date(b.slot.start) - new Date();
            if (ms >= 0 && ms <= UPCOMING_24H_MS) return 'urgent';
        }
        return 'upcoming';
    }

    // Группировка для отображения по дню
    function dayKey(d) {
        const c = startOfDay(d);
        return c.getFullYear() + '-' + String(c.getMonth() + 1).padStart(2, '0') + '-' + String(c.getDate()).padStart(2, '0');
    }
    function dayLabel(date) {
        const dd = daysDiff(date, new Date());
        if (dd === 0) return (i18n.today || 'Сегодня');
        if (dd === 1) return (i18n.tomorrow || 'Завтра');
        if (dd === -1) return 'Вчера';
        return new Date(date).toLocaleDateString(cfg.locale, {
            day: 'numeric', month: 'long', weekday: 'short',
        });
    }

    // ---- render: status badge / countdown / card ----
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
        if (ms <= 3600000) {
            const m = Math.floor(ms / 60000);
            const s = Math.floor((ms % 60000) / 1000);
            return `<span class="bk-countdown"><i class="fas fa-clock"></i> осталось ${m}:${String(s).padStart(2, '0')}</span>`;
        }
        const str = d.toLocaleString('ru', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' });
        return `<span class="bk-countdown"><i class="fas fa-clock"></i> подтвердить до ${str}</span>`;
    }

    function renderCard(b) {
        const start = new Date(b.slot.start);
        const end = new Date(b.slot.end);
        const fmtTime = d => d.toLocaleTimeString(cfg.locale, { hour: '2-digit', minute: '2-digit' });
        const monthName = start.toLocaleString(cfg.locale, { month: 'short' });

        // Чьё имя показывать
        const counterparty = cfg.role === 'student' ? b.teacher : b.student;
        const counterpartyLabel = cfg.role === 'student' ? 'с' : 'для';

        const subjectMeta = b.subject ? `<span>${escapeHtml(b.subject.name)}</span>` : '';

        // «Деньги под проверкой» (escrow/grace): урок прошёл, выплата учителю
        // ещё не ушла — ученик может открыть спор, учитель ждёт выплату.
        let escrowBadge = '';
        if (b.escrow_hold && b.payout_at) {
            const pa = new Date(b.payout_at);
            const paStr = pa.toLocaleString(cfg.locale, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
            const txt = cfg.role === 'student'
                ? `Деньги под проверкой до ${paStr}`
                : `Выплата после проверки · ${paStr}`;
            escrowBadge = `<span class="bk-escrow" title="Средства удерживаются платформой до окончания периода проверки"><i class="fas fa-shield-halved"></i> ${escapeHtml(txt)}</span>`;
        }
        const messageBlock = b.student_message
            ? `<div class="msg"><i class="fas fa-envelope"></i> ${escapeHtml(b.student_message)}</div>` : '';
        const teacherReplyBlock = b.teacher_reply
            ? `<div class="msg"><i class="fas fa-comment"></i> ${escapeHtml(b.teacher_reply)}</div>` : '';
        const meetingUrlMeta = b.meeting_url
            ? `<div class="msg" style="background:#EEF2FF; color:#3730A3;"><i class="fas fa-video"></i> ${escapeHtml(b.meeting_url)}</div>` : '';

        const actions = renderActions(b);

        // Phase 5: визуальные классы
        const classes = ['bk-card'];
        if (b.status === 'pending') classes.push('is-pending');
        if (isUrgent(b)) classes.push('is-urgent');
        if (isToday(b) && (b.status === 'pending' || b.status === 'confirmed')) classes.push('is-today');
        if (b.is_trial) classes.push('is-trial');
        if (isPast(b)) classes.push('is-past');

        return `
            <div class="${classes.join(' ')}"
                 data-id="${b.id}"
                 data-teacher-id="${b.teacher.id}"
                 data-meeting-url="${escapeHtml(b.meeting_url || '')}"
                 data-trial-label="${escapeHtml(i18n.trialLabel || 'Trial')}">
                ${classes.includes('is-today') ? `<span class="bk-today-badge">${escapeHtml(i18n.urgentBadge || 'Сегодня')}</span>` : ''}
                <div class="bk-date">
                    <div class="day">${start.getDate()}</div>
                    <div class="month">${monthName}</div>
                    <div class="time">${fmtTime(start)}–${fmtTime(end)}</div>
                </div>
                <div class="bk-info">
                    <h3>${counterpartyLabel} ${escapeHtml(counterparty.name)}</h3>
                    <div class="meta">
                        <span class="bk-status ${statusBadgeClass(b.status)}">${escapeHtml(b.status_display)}</span>
                        ${b.status === 'pending' ? renderCountdown(b.expires_at) : ''}
                        ${escrowBadge}
                        ${subjectMeta}
                        ${b.is_trial ? '<span><i class="fas fa-gift"></i> пробный</span>' : ''}
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

        if (b.status === 'confirmed' && (b.lesson_room_url || b.meeting_url)) {
            if (isJoinable(b)) {
                // Всегда ведём в комнату урока (как на дашборде): там файлы, доска и
                // учёт присутствия. Сервер (lesson_room) сам решит — встроить наш
                // Jitsi или редиректнуть на внешнюю ссылку (Zoom и т.п.).
                const href = b.lesson_room_url || b.meeting_url;
                // Тот же таб: урок (наша комната) не отрывается от контекста,
                // не плодятся вкладки. Внешнюю ссылку комната откроет сама.
                buttons.push(`<a class="bk-btn join" href="${href}">
                    <i class="fa-solid fa-video"></i> ${i18n.joinLesson}
                </a>`);
            } else if (!isPast(b)) {
                // Урок ещё не скоро — показываем неактивную подсказку, когда откроется вход,
                // чтобы пользователь не искал «как зайти».
                const lead = cfg.joinLeadMinutes || 10;
                const hint = (i18n.opensSoon || 'Вход откроется за {n} мин до начала').replace('{n}', lead);
                buttons.push(`<span class="bk-btn join is-disabled" title="${escapeHtml(hint)}" aria-disabled="true">
                    <i class="fa-regular fa-clock"></i> ${escapeHtml(hint)}
                </span>`);
            }
        }

        // ===== TEACHER pending: 1-click confirm + ✉ с сообщением + reject =====
        if (cfg.role === 'teacher' && b.status === 'pending') {
            buttons.push(`<button class="bk-btn primary" data-action="confirm-fast">
                <i class="fa-solid fa-check"></i> ${i18n.confirm}
            </button>`);
            buttons.push(`<button class="bk-btn secondary" data-action="confirm" title="${escapeHtml(i18n.confirmWithMsg || 'С сообщением')}">
                <i class="fa-regular fa-envelope"></i>
            </button>`);
            buttons.push(`<button class="bk-btn danger" data-action="reject">${i18n.reject}</button>`);
        }

        if ((b.status === 'pending' || b.status === 'confirmed') && cfg.urls.ical) {
            buttons.push(`<a class="bk-btn secondary" href="${cfg.urls.ical.replace('__ID__', b.id)}"
                title="${i18n.addToCalendar}" aria-label="${i18n.addToCalendar}">
                <i class="fa-regular fa-calendar-plus"></i></a>`);
        }
        if (cfg.role === 'teacher' && b.status === 'confirmed') {
            const lbl = b.meeting_url ? i18n.editLink : i18n.setLink;
            buttons.push(`<button class="bk-btn secondary" data-action="set-link">${lbl}</button>`);
            buttons.push(`<button class="bk-btn danger" data-action="cancel">${i18n.cancel}</button>`);
        }
        if (cfg.role === 'student' && (b.status === 'pending' || b.status === 'confirmed')) {
            buttons.push(`<button class="bk-btn secondary" data-action="reschedule">
                <i class="fa-regular fa-clock"></i> ${i18n.reschedule || 'Перенести'}</button>`);
            buttons.push(`<button class="bk-btn danger" data-action="cancel">${i18n.cancel}</button>`);
        }
        if (cfg.role === 'student' && b.status === 'completed' && b.review_url) {
            const lbl = b.has_review ? (i18n.editReview || 'Изменить отзыв')
                                     : (i18n.leaveReview || 'Оставить отзыв');
            buttons.push(`<a class="bk-btn ${b.has_review ? 'secondary' : 'primary'}" href="${b.review_url}">
                <i class="fa-solid fa-star"></i> ${lbl}
            </a>`);
        }
        // Спор (ТЗ шаг 8): ученик может открыть спор по оплаченному уроку в grace-окне.
        if (cfg.role === 'student' && b.dispute_status) {
            const disputeLabels = {
                'open': i18n.disputeOpen || 'на рассмотрении',
                'resolved_refund': i18n.disputeRefund || 'возврат сделан',
                'resolved_rejected': i18n.disputeRejected || 'отклонён',
                'cancelled': i18n.disputeCancelled || 'отозван',
            };
            const disputeLabel = disputeLabels[b.dispute_status] || b.dispute_status;
            buttons.push(`<span class="bk-btn secondary" style="cursor:default">
                <i class="fa-solid fa-flag"></i> ${i18n.disputeStatus || 'Спор'}: ${escapeHtml(disputeLabel)}
            </span>`);
        } else if (cfg.role === 'student' && b.can_dispute && b.dispute_url) {
            buttons.push(`<a class="bk-btn danger" href="${b.dispute_url}">
                <i class="fa-solid fa-flag"></i> ${i18n.openDispute || 'Открыть спор'}
            </a>`);
        }
        return buttons.join('');
    }

    function isJoinable(b) {
        const start = new Date(b.slot.start);
        const end = new Date(b.slot.end);
        const now = new Date();
        // Окно должно совпадать с серверным (lesson_room / lesson_attendance_api):
        // открыто за lead минут до начала и ещё grace минут после конца —
        // чтобы можно было вернуться после обрыва связи.
        const lead = (cfg.joinLeadMinutes || 10) * 60 * 1000;
        const grace = (cfg.joinGraceMinutes || 30) * 60 * 1000;
        return (start - now <= lead) && (now <= new Date(end.getTime() + grace));
    }

    // ---- stats strip ----
    function renderStats(bookings) {
        const today = bookings.filter(b => isToday(b) && (b.status === 'pending' || b.status === 'confirmed'));
        const pending = bookings.filter(b => b.status === 'pending');
        const urgent = bookings.filter(isUrgent);
        const upcoming = bookings.filter(b => b.status === 'confirmed' && !isPast(b));

        // показываем только если есть хоть что-то
        if (!today.length && !pending.length && !upcoming.length) {
            $stats.hidden = true;
            $stats.innerHTML = '';
            return;
        }
        $stats.hidden = false;
        const parts = [];
        if (today.length) parts.push(`
            <div class="bk-stat">
                <div class="bk-stat__icon"><i class="fas fa-calendar-day"></i></div>
                <div>
                    <div class="bk-stat__val">${today.length}</div>
                    <div class="bk-stat__lbl">${escapeHtml(i18n.statTodayLessons || 'сегодня')}</div>
                </div>
            </div>`);
        if (pending.length) parts.push(`
            <div class="bk-stat">
                <div class="bk-stat__icon"><i class="fas fa-hourglass-half"></i></div>
                <div>
                    <div class="bk-stat__val">${pending.length}</div>
                    <div class="bk-stat__lbl">${escapeHtml(i18n.statPending || 'ждут')}</div>
                </div>
            </div>`);
        if (urgent.length) parts.push(`
            <div class="bk-stat">
                <div class="bk-stat__icon urgent"><i class="fas fa-fire"></i></div>
                <div>
                    <div class="bk-stat__val" style="color:#DC2626;">${urgent.length}</div>
                    <div class="bk-stat__lbl">${escapeHtml(i18n.statUrgent || 'истекают')}</div>
                </div>
            </div>`);
        if (upcoming.length) parts.push(`
            <div class="bk-stat">
                <div class="bk-stat__icon"><i class="fas fa-calendar-check"></i></div>
                <div>
                    <div class="bk-stat__val">${upcoming.length}</div>
                    <div class="bk-stat__lbl">${escapeHtml(i18n.statUpcoming || 'предстоит')}</div>
                </div>
            </div>`);
        $stats.innerHTML = parts.join('');
    }

    // ---- tab counts (badges на табах) ----
    function updateTabCounts(bookings) {
        const counts = { urgent: 0, upcoming: 0, past: 0 };
        bookings.forEach(b => { counts[classifyForTab(b)] = (counts[classifyForTab(b)] || 0) + 1; });
        const urgentNow = bookings.filter(isUrgent).length;
        $filters.querySelectorAll('.bk-filter-count').forEach(el => {
            const key = el.dataset.count;
            const n = counts[key] || 0;
            el.textContent = n;
            el.classList.toggle('has-items', n > 0);
            el.classList.toggle('is-urgent', key === 'urgent' && urgentNow > 0);
        });
    }

    // ---- empty states ----
    function renderEmptyForTab(tab) {
        if (tab === 'urgent') {
            const text = cfg.role === 'teacher'
                ? (i18n.emptyUrgentTeacher || 'Все запросы обработаны 🎉')
                : (i18n.emptyUrgentStudent || 'Нет уроков, ждущих подтверждения');
            return `<div class="bk-empty">
                <i class="fa-regular fa-circle-check" style="color:#10B981;"></i>
                <div>${escapeHtml(text)}</div>
            </div>`;
        }
        if (tab === 'upcoming') {
            const text = cfg.role === 'teacher'
                ? (i18n.emptyUpcomingTeacher || 'У вас нет предстоящих уроков')
                : (i18n.emptyUpcomingStudent || 'У вас нет предстоящих уроков');
            const cta = cfg.role === 'student'
                ? `<a href="${cfg.i18n.homeUrl || '/'}" class="bk-btn primary" style="margin-top:14px; display:inline-flex; gap:6px; padding:10px 18px;">
                    <i class="fa-solid fa-magnifying-glass"></i> ${escapeHtml(i18n.findTeacher || 'Найти учителя')}
                </a>` : '';
            return `<div class="bk-empty">
                <i class="fa-regular fa-calendar"></i>
                <div>${escapeHtml(text)}</div>
                ${cta}
            </div>`;
        }
        return `<div class="bk-empty">
            <i class="fa-regular fa-clock"></i>
            <div>${escapeHtml(i18n.emptyPast || 'Здесь будут уроки после их завершения')}</div>
        </div>`;
    }

    // ---- grouped list rendering ----
    function renderGroupedList(bookings) {
        if (!bookings.length) return renderEmptyForTab(currentTab);
        // Sort: urgent → pending по expires_at, остальные по start_at
        const sorted = bookings.slice().sort((a, b) => {
            if (a.status === 'pending' && b.status === 'pending') {
                return new Date(a.expires_at || a.slot.start) - new Date(b.expires_at || b.slot.start);
            }
            // past → новые сверху, остальные — ближайшие сверху
            if (currentTab === 'past') return new Date(b.slot.start) - new Date(a.slot.start);
            return new Date(a.slot.start) - new Date(b.slot.start);
        });

        // Group by day
        const groups = [];
        let lastKey = null;
        sorted.forEach(b => {
            const key = dayKey(b.slot.start);
            if (key !== lastKey) {
                groups.push({ key, date: b.slot.start, items: [] });
                lastKey = key;
            }
            groups[groups.length - 1].items.push(b);
        });

        return groups.map(g => {
            const todayClass = daysDiff(g.date, new Date()) === 0 ? ' is-today' : '';
            return `
                <div class="bk-day-header${todayClass}">
                    <span>${escapeHtml(dayLabel(g.date))}</span>
                    <span class="bk-day-header__line"></span>
                    <span class="bk-day-header__count">${g.items.length}</span>
                </div>
                ${g.items.map(renderCard).join('')}
            `;
        }).join('');
    }

    // ---- modal / toast (без изменений из Phase 4) ----
    const $overlay = document.getElementById('bkm-overlay');
    const $mTitle = document.getElementById('bkm-title');
    const $mText = document.getElementById('bkm-text');
    const $mBody = document.getElementById('bkm-body');
    const $mOk = document.getElementById('bkm-ok');
    const $mCancel = document.getElementById('bkm-cancel');
    let _modalResolve = null;
    let _previousFocus = null;

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
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
    function closeModal(result) {
        $overlay.classList.remove('is-open');
        $overlay.setAttribute('aria-hidden', 'true');
        document.removeEventListener('keydown', _trapTab, true);
        const r = _modalResolve; _modalResolve = null;
        if (r) r(result);
        if (_previousFocus && typeof _previousFocus.focus === 'function') {
            try { _previousFocus.focus(); } catch (_) {}
        }
        _previousFocus = null;
    }

    function modal(opts) {
        return new Promise(resolve => {
            _modalResolve = resolve;
            $mTitle.textContent = opts.title || '';
            $mText.textContent = opts.text || '';
            $mText.style.display = opts.text ? '' : 'none';

            // Phase 5: quick-reply chips сверху textarea
            let chipsHtml = '';
            if (opts.chips && opts.chips.length) {
                chipsHtml = `<div class="bkm-chips">` +
                    opts.chips.map((c, i) => `<button type="button" class="bkm-chip" data-chip-text="${escapeHtml(c)}">${escapeHtml(c)}</button>`).join('') +
                    `</div>`;
            }

            $mBody.innerHTML = chipsHtml + (opts.fields || []).map(f => {
                let ctrl;
                if (f.type === 'textarea') {
                    ctrl = `<textarea data-field="${f.name}" placeholder="${escapeHtml(f.placeholder || '')}">${escapeHtml(f.value || '')}</textarea>`;
                } else if (f.type === 'select') {
                    const opts2 = (f.options || []).map(o =>
                        `<option value="${escapeHtml(String(o.value))}">${escapeHtml(o.label)}</option>`).join('');
                    ctrl = `<select data-field="${f.name}">${opts2}</select>`;
                } else {
                    ctrl = `<input type="text" data-field="${f.name}" value="${escapeHtml(f.value || '')}" placeholder="${escapeHtml(f.placeholder || '')}">`;
                }
                return `<div class="bkm-field">
                    ${f.label ? `<label>${escapeHtml(f.label)}</label>` : ''}
                    ${ctrl}
                    ${f.hint ? `<div class="bkm-hint">${escapeHtml(f.hint)}</div>` : ''}
                </div>`;
            }).join('');

            // chips → подставляют текст в первый textarea
            if (chipsHtml) {
                $mBody.querySelectorAll('.bkm-chip').forEach(chip => {
                    chip.addEventListener('click', () => {
                        const ta = $mBody.querySelector('textarea[data-field]');
                        if (ta) {
                            ta.value = chip.dataset.chipText;
                            $mBody.querySelectorAll('.bkm-chip').forEach(c => c.classList.remove('is-active'));
                            chip.classList.add('is-active');
                            ta.focus();
                        }
                    });
                });
            }

            $mOk.textContent = opts.okText || i18n.ok || 'OK';
            $mOk.className = 'bkm-btn ' + (opts.okClass || 'primary');
            $mCancel.textContent = i18n.cancelBtn || 'Cancel';
            _previousFocus = document.activeElement;
            $overlay.classList.add('is-open');
            $overlay.setAttribute('aria-hidden', 'false');
            document.addEventListener('keydown', _trapTab, true);
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

    // ---- main render ----
    function render() {
        const filtered = allBookings.filter(b => classifyForTab(b) === currentTab);
        renderStats(allBookings);
        updateTabCounts(allBookings);
        $list.innerHTML = renderGroupedList(filtered);
    }

    async function loadList() {
        if (!allBookings.length) {
            $list.innerHTML = '<div class="bk-empty"><i class="fas fa-spinner fa-spin"></i><div>' +
                (i18n.loading || 'Загрузка…') + '</div></div>';
        }
        try {
            // Без status — грузим ВСЁ и фильтруем на клиенте
            const data = await api('GET', cfg.urls.list);
            allBookings = data.bookings || [];
            render();
        } catch (e) {
            $list.innerHTML =
                '<div class="bk-empty">' +
                    '<i class="fas fa-triangle-exclamation" style="color:var(--danger-color,#EF4444)"></i>' +
                    '<div>' + (i18n.loadError || 'Не удалось загрузить. Проверьте соединение.') + '</div>' +
                    '<button type="button" id="bk-retry" class="bk-btn primary" style="margin-top:12px">' +
                        (i18n.retry || 'Повторить') +
                    '</button>' +
                '</div>';
            var rb = document.getElementById('bk-retry');
            if (rb) rb.addEventListener('click', loadList);
        }
    }

    // Tick для countdown'ов и для urgent-detection (не дёргаем сервер, только перерисовываем)
    setInterval(() => {
        if (!allBookings.length) return;
        // Дёшево: если есть pending — перерисуем (countdown + urgent class могут смениться)
        if (allBookings.some(b => b.status === 'pending')) render();
    }, 30000);  // каждые 30 сек

    // ---- events ----
    $filters.addEventListener('click', e => {
        const btn = e.target.closest('.bk-filter');
        if (!btn) return;
        $filters.querySelectorAll('.bk-filter').forEach(b => b.classList.remove('is-active'));
        btn.classList.add('is-active');
        currentTab = btn.dataset.tab || 'urgent';
        render();
    });

    $list.addEventListener('click', async e => {
        const btn = e.target.closest('[data-action]');
        if (!btn) return;
        const card = btn.closest('.bk-card');
        const id = card?.dataset.id;
        if (!id) return;
        const action = btn.dataset.action;

        let url, body;

        // Phase 5: 1-кликовый confirm без модала (auto-Jitsi)
        if (action === 'confirm-fast') {
            url = cfg.urls.confirm.replace('__ID__', id);
            body = { reply: '', meeting_url: '' };
            // Сразу submit, без подтверждения — undo можно через reject если что
        } else if (action === 'confirm') {
            const res = await modal({
                title: i18n.confirmTitle,
                okText: i18n.confirm,
                okClass: 'primary',
                fields: [
                    { name: 'meeting_url', label: i18n.meetingUrlLabel, hint: i18n.meetingUrlHint, type: 'text', placeholder: 'https://…' },
                    { name: 'reply', label: i18n.replyLabel, type: 'textarea' },
                ],
            });
            if (res === null) return;
            url = cfg.urls.confirm.replace('__ID__', id);
            body = { reply: res.reply || '', meeting_url: res.meeting_url || '' };
        } else if (action === 'reject') {
            // Phase 5: quick-reply chips
            const chips = [
                i18n.quickRejectBusy || 'В это время занят',
                i18n.quickRejectSick || 'Заболел, не смогу провести',
                i18n.quickRejectReschedule || 'Давайте перенесём',
            ];
            const res = await modal({
                title: i18n.rejectTitle,
                okText: i18n.reject,
                okClass: 'danger',
                chips: chips,
                fields: [{ name: 'reply', label: i18n.reasonLabel, type: 'textarea', placeholder: 'Подробнее (необязательно)…' }],
            });
            if (res === null) return;
            url = cfg.urls.reject.replace('__ID__', id);
            body = { reply: res.reply || '' };
        } else if (action === 'cancel') {
            const res = await modal({
                title: i18n.cancelTitle,
                text: i18n.confirmCancel + (i18n.cancelPolicy ? ' ' + i18n.cancelPolicy : ''),
                okText: i18n.cancel,
                okClass: 'danger',
            });
            if (res === null) return;
            url = cfg.urls.cancel.replace('__ID__', id);
            body = {};
        } else if (action === 'set-link') {
            const current = card.dataset.meetingUrl || '';
            const res = await modal({
                title: i18n.setLinkTitle,
                okText: i18n.ok,
                fields: [{ name: 'meeting_url', label: i18n.meetingUrlLabel, hint: i18n.meetingUrlHint, type: 'text', value: current, placeholder: 'https://…' }],
            });
            if (res === null) return;
            url = cfg.urls.setLink.replace('__ID__', id);
            body = { meeting_url: res.meeting_url || '' };
        } else if (action === 'reschedule') {
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
            if (!slots.length) { toast(i18n.noFreeSlots, true); return; }
            const fmt = s => new Date(s.start).toLocaleString(cfg.locale,
                { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
            const res = await modal({
                title: i18n.rescheduleTitle,
                text: i18n.rescheduleHint,
                okText: i18n.reschedule,
                fields: [{
                    name: 'slot_id', label: i18n.pickSlot, type: 'select',
                    options: slots.map(s => ({ value: s.id, label: fmt(s) })),
                }],
            });
            if (res === null) return;
            try {
                await api('POST', cfg.urls.reschedule.replace('__ID__', id),
                    { slot_id: parseInt(res.slot_id, 10) });
                toast(i18n.rescheduled);
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
            if (action === 'confirm-fast') {
                toast(i18n.confirmedFast || 'Подтверждено ✓');
            }
            await loadList();
        } catch (e) {
            toast('Ошибка: ' + e.message, true);
            btn.disabled = false;
        }
    });

    // ---- realtime ----
    let _refreshTimer = null;
    function refreshSoon() {
        clearTimeout(_refreshTimer);
        _refreshTimer = setTimeout(loadList, 400);
    }
    window.addEventListener('ustoz:ws', e => {
        const t = (e.detail || {}).type;
        if (t === 'new_notification' || t === 'booking_update' || t === 'booking_status_changed') refreshSoon();
    });

    setInterval(loadList, 60000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) loadList(); });

    loadList();
})();
