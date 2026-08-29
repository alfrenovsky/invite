(function() {
    let slides = [];
    let progressSlots = [];
    const progressContainer = document.getElementById('progressBarsContainer');
    const tapLeft = document.getElementById('tapLeft');
    const tapRight = document.getElementById('tapRight');
    const btnDesktopPrev = document.getElementById('btnDesktopPrev');
    const btnDesktopNext = document.getElementById('btnDesktopNext');
    const btnPause = document.getElementById('btnPause');
    const toast = document.getElementById('toast');
    const container = document.getElementById('storyContainer');

    let currentIndex = 0;
    let isAutoplay = false;
    let isPaused = false;
    let slideStartTime = 0;
    let elapsedTimeOnSlide = 0;
    let animationFrameId = null;

    // Auto-Save Configuration
    const AUTOSAVE_CONFIG = {
        CLICK_DEBOUNCE_MS: 10000,
        INPUT_DEBOUNCE_MS: 10000,
        RETRY_DELAY_MS: 4000
    };

    const dirtyGuests = new Set();
    const savedGuestStates = {};
    let autoSaveTimer = null;
    let isSaving = false;

    // ==============================================================
    // 🔗 URL Hash Navigation (#1, #2, #3... or #rsvp)
    // ==============================================================
    function getTargetIndexFromHash() {
        if (!slides || slides.length === 0) return 0;
        const rawHash = (window.location.hash || '').replace(/^#/, '').trim().toLowerCase();
        if (!rawHash) return 0;

        // Check if numeric 1-indexed (#1, #2, #3...)
        const num = parseInt(rawHash, 10);
        if (!isNaN(num) && num >= 1 && num <= slides.length) {
            return num - 1;
        }

        // Check if matching slide slug/id (#rsvp, #intro, etc.)
        const foundIdx = slides.findIndex(s => {
            const sid = (s.getAttribute('data-slide-id') || '').toLowerCase();
            return sid === rawHash;
        });
        if (foundIdx !== -1) return foundIdx;

        return 0;
    }

    function updateUrlHash(index) {
        if (!slides || slides.length === 0) return;
        const slideNumber = index + 1;
        const targetHash = '#' + slideNumber;
        if (window.location.hash !== targetHash) {
            history.replaceState(null, '', targetHash);
        }
    }

    window.addEventListener('hashchange', () => {
        const targetIdx = getTargetIndexFromHash();
        if (targetIdx !== currentIndex) {
            goToSlide(targetIdx);
        }
    });

    // ==============================================================
    // 📊 Progress Bars UI
    // ==============================================================
    function buildProgressBars() {
        if (!progressContainer) return;
        progressContainer.innerHTML = '';
        slides.forEach((_, idx) => {
            const slot = document.createElement('div');
            slot.className = 'progress-bar-slot';
            slot.innerHTML = '<div class="progress-bar-fill"></div>';
            slot.addEventListener('click', (e) => {
                e.stopPropagation();
                goToSlide(idx);
            });
            progressContainer.appendChild(slot);
        });
        progressSlots = Array.from(progressContainer.children);
    }

    function updateProgressDisplay(percent) {
        progressSlots.forEach((slot, idx) => {
            const fill = slot.querySelector('.progress-bar-fill');
            if (!fill) return;
            if (idx < currentIndex) {
                slot.classList.add('completed');
                fill.style.width = '100%';
            } else if (idx === currentIndex) {
                slot.classList.remove('completed');
                fill.style.width = `${Math.min(100, Math.max(0, percent))}%`;
            } else {
                slot.classList.remove('completed');
                fill.style.width = '0%';
            }
        });
    }

    // ==============================================================
    // 📱 Slide Presentation & Animation Engine
    // ==============================================================
    function showSlide(index) {
        if (slides.length === 0) return;
        const isDesktop = window.innerWidth >= 768;
        const isLastSlide = (index === slides.length - 1);

        if (tapLeft && tapRight) {
            tapLeft.style.display = isLastSlide ? 'none' : 'block';
            tapRight.style.display = isLastSlide ? 'none' : 'block';
        }

        slides.forEach((s, idx) => {
            const offset = idx - index;
            s.classList.remove('active', 'side-slide', 'hidden-slide', 'prev-slide', 'next-slide');

            if (offset === 0) {
                s.classList.add('active');
                s.style.transform = isDesktop ? 'translateX(0) scale(1)' : 'translateX(0)';
                s.style.opacity = '1';
                s.style.visibility = 'visible';
                s.style.filter = 'blur(0px) brightness(1)';
                s.style.zIndex = '300';
                s.style.cursor = 'default';
            } else if (isDesktop) {
                const shiftPx = sign * (390 * 0.76 + (absOffset - 1) * 165);

                s.style.setProperty('--slide-transform', `translateX(${shiftPx}px) scale(0.40)`);
                s.style.setProperty('--slide-hover-transform', `translateX(${shiftPx}px) scale(0.43)`);
                s.style.transform = `translateX(${shiftPx}px) scale(0.40)`;

                s.style.zIndex = (20 - absOffset).toString();
                s.style.visibility = 'visible';
                s.style.cursor = 'pointer';

                const opacityVal = Math.max(0.35, 0.85 - (absOffset - 1) * 0.15).toString();
                s.style.opacity = opacityVal;

                if (offset < 0) {
                    s.classList.add('side-slide', 'prev-slide');
                    s.style.filter = 'blur(0px) brightness(0.95)';
                } else {
                    s.classList.add('side-slide', 'next-slide');
                    s.style.filter = 'blur(10px) brightness(0.55)';
                }
            } else {
                // Mobile Slide Clean State (Only active slide is visible!)
                s.style.opacity = '0';
                s.style.visibility = 'hidden';
                s.style.filter = 'none';
                if (offset > 0) {
                    s.classList.add('next-slide');
                    s.style.transform = 'translateX(100%)';
                    s.style.zIndex = (200 - offset).toString();
                } else {
                    s.classList.add('prev-slide');
                    s.style.transform = 'translateX(-100%)';
                    s.style.zIndex = (200 + offset).toString();
                }
                s.style.cursor = 'default';
            }
        });
    }

    window.addEventListener('resize', () => showSlide(currentIndex));

    function startSlideTimer() {
        clearTimers();
        if (slides.length === 0) return;
        const slide = slides[currentIndex];
        if (!slide) return;
        const duration = parseInt(slide.getAttribute('data-duration'), 10) || 0;

        if (!isAutoplay || duration <= 0) {
            updateProgressDisplay(100);
            return;
        }

        slideStartTime = performance.now() - elapsedTimeOnSlide;

        function tick(now) {
            if (!isAutoplay) {
                updateProgressDisplay(100);
                return;
            }

            if (!isPaused) {
                elapsedTimeOnSlide = now - slideStartTime;
            } else {
                slideStartTime = now - elapsedTimeOnSlide;
            }

            const percent = (elapsedTimeOnSlide / duration) * 100;
            updateProgressDisplay(percent);

            if (elapsedTimeOnSlide >= duration) {
                nextSlide();
            } else {
                animationFrameId = requestAnimationFrame(tick);
            }
        }

        animationFrameId = requestAnimationFrame(tick);
    }

    function clearTimers() {
        if (animationFrameId) {
            cancelAnimationFrame(animationFrameId);
            animationFrameId = null;
        }
    }

    function goToSlide(index) {
        if (slides.length === 0) return;
        flushAutoSave();
        if (index < 0) index = 0;
        if (index >= slides.length) index = slides.length - 1;

        currentIndex = index;
        elapsedTimeOnSlide = 0;
        updateUrlHash(currentIndex);
        showSlide(currentIndex);
        startSlideTimer();
    }

    function nextSlide() {
        if (currentIndex < slides.length - 1) {
            goToSlide(currentIndex + 1);
        } else {
            updateProgressDisplay(100);
        }
    }

    function prevSlide() {
        if (currentIndex > 0) {
            goToSlide(currentIndex - 1);
        } else {
            goToSlide(0);
        }
    }

    function togglePlayPause() {
        isAutoplay = !isAutoplay;
        if (btnPause) {
            btnPause.textContent = isAutoplay ? '⏸' : '▶';
            btnPause.title = isAutoplay ? 'Pausar avance automático' : 'Iniciar avance automático';
        }
        if (isAutoplay) {
            startSlideTimer();
        } else {
            clearTimers();
            updateProgressDisplay(100);
        }
    }

    // ==============================================================
    // 👆 Tap & Desktop Button Handlers
    // ==============================================================
    let isSwiping = false;

    function handleTap(direction) {
        if (isSwiping || isPullingToRefresh) return;
        if (direction === 'next') nextSlide();
        else prevSlide();
    }

    if (tapLeft) {
        tapLeft.addEventListener('click', (e) => {
            e.stopPropagation();
            handleTap('prev');
        });
    }
    if (tapRight) {
        tapRight.addEventListener('click', (e) => {
            e.stopPropagation();
            handleTap('next');
        });
    }
    if (btnDesktopPrev) btnDesktopPrev.addEventListener('click', prevSlide);
    if (btnDesktopNext) btnDesktopNext.addEventListener('click', nextSlide);
    if (btnPause) {
        btnPause.addEventListener('click', (e) => {
            e.stopPropagation();
            togglePlayPause();
        });
    }

    // ==============================================================
    // 🔄 Mobile Pull to Refresh & Horizontal Swipe Engine
    // ==============================================================
    const ptrIndicator = document.getElementById('pullToRefreshIndicator');
    const ptrLabel = document.getElementById('ptrLabel');
    let isPullingToRefresh = false;
    let pullEligible = false;
    let isRefreshing = false;

    let holdTimeout = null;
    let touchStartX = 0;
    let touchStartY = 0;
    let touchStartTime = 0;

    function onHoldStart() {
        holdTimeout = setTimeout(() => { isPaused = true; }, 200);
    }

    function onHoldEnd() {
        if (holdTimeout) clearTimeout(holdTimeout);
        isPaused = false;
    }

    if (container) {
        container.addEventListener('mousedown', onHoldStart);
        container.addEventListener('mouseup', onHoldEnd);

        container.addEventListener('touchstart', (e) => {
            if (isRefreshing || !e.touches || e.touches.length === 0) return;
            touchStartX = e.touches[0].clientX;
            touchStartY = e.touches[0].clientY;
            touchStartTime = Date.now();
            isSwiping = false;
            isPullingToRefresh = false;

            pullEligible = false;
            const isLastSlide = (currentIndex === slides.length - 1);
            if (!isLastSlide) {
                pullEligible = true;
            } else {
                const formScroll = document.querySelector('.rsvp-form-container');
                if (!formScroll || formScroll.scrollTop <= 5) {
                    pullEligible = true;
                }
            }

            onHoldStart();
        }, { passive: true });

        container.addEventListener('touchmove', (e) => {
            if (isRefreshing || !e.touches || e.touches.length === 0) return;
            const currentX = e.touches[0].clientX;
            const currentY = e.touches[0].clientY;
            const diffX = touchStartX - currentX;
            const diffY = currentY - touchStartY; // Positive when pulling DOWN

            if (Math.abs(diffX) > 10) {
                isSwiping = true;
                if (holdTimeout) clearTimeout(holdTimeout);
                isPaused = false;
            }

            // Pull to refresh downward gesture (diffY > 15 and downward dominant)
            if (pullEligible && diffY > 15 && diffY > Math.abs(diffX) * 0.8 && window.innerWidth < 768) {
                isPullingToRefresh = true;
                if (holdTimeout) clearTimeout(holdTimeout);
                isPaused = false;

                const pullOffset = Math.min(diffY * 0.55, 80);
                if (ptrIndicator) {
                    ptrIndicator.style.transform = `translate(-50%, ${pullOffset}px)`;
                    ptrIndicator.classList.add('visible');

                    if (diffY >= 50) {
                        ptrIndicator.classList.add('ready');
                        if (ptrLabel) ptrLabel.textContent = 'Soltá para recargar';
                    } else {
                        ptrIndicator.classList.remove('ready');
                        if (ptrLabel) ptrLabel.textContent = 'Deslizá para recargar';
                    }
                }
            }
        }, { passive: true });

        container.addEventListener('touchend', (e) => {
            onHoldEnd();
            if (isRefreshing || !e.changedTouches || e.changedTouches.length === 0) return;

            const touchEndX = e.changedTouches[0].clientX;
            const touchEndY = e.changedTouches[0].clientY;
            const diffX = touchStartX - touchEndX;
            const diffY = touchEndY - touchStartY;
            const duration = Date.now() - touchStartTime;

            // Handle Pull to Refresh on release
            if (isPullingToRefresh && ptrIndicator) {
                if (diffY >= 50) {
                    isRefreshing = true;
                    ptrIndicator.classList.remove('ready');
                    ptrIndicator.classList.add('refreshing');
                    if (ptrLabel) ptrLabel.textContent = 'Recargando...';
                    ptrIndicator.style.transform = 'translate(-50%, 60px)';
                    setTimeout(() => {
                        window.location.reload();
                    }, 350);
                    return;
                } else {
                    ptrIndicator.style.transform = 'translate(-50%, -120px)';
                    ptrIndicator.classList.remove('visible', 'ready');
                    isPullingToRefresh = false;
                }
            }

            // On RSVP slide, preserve vertical scrolling inside form
            const target = e.target;
            if (target && target.closest('.rsvp-form-container') && Math.abs(touchStartY - touchEndY) > Math.abs(diffX)) {
                setTimeout(() => { isSwiping = false; }, 50);
                return;
            }

            // Detect horizontal swipe: distance >= 25px, within 750ms
            const minSwipeDistance = 25;
            if (Math.abs(diffX) >= minSwipeDistance && Math.abs(diffX) > Math.abs(touchStartY - touchEndY) * 0.65 && duration < 750) {
                if (diffX > 0) {
                    nextSlide();
                } else {
                    prevSlide();
                }
            }

            setTimeout(() => { isSwiping = false; }, 60);
        }, { passive: true });

        container.addEventListener('touchcancel', () => {
            onHoldEnd();
            isSwiping = false;
            isPullingToRefresh = false;
            if (ptrIndicator) {
                ptrIndicator.style.transform = 'translate(-50%, -120px)';
                ptrIndicator.classList.remove('visible', 'ready');
            }
        });
    }

    // Keyboard navigation
    document.addEventListener('keydown', (e) => {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
        if (e.key === 'ArrowRight' || e.key === ' ') {
            e.preventDefault();
            nextSlide();
        } else if (e.key === 'ArrowLeft') {
            e.preventDefault();
            prevSlide();
        }
    });

    function showToast(msg) {
        if (!toast) return;
        toast.textContent = msg;
        toast.classList.add('show');
        setTimeout(() => { toast.classList.remove('show'); }, 2500);
    }

    // Live Countdown
    const TARGET_DATE = new Date('2027-03-19T18:00:00-03:00').getTime();
    function updateCountdown() {
        const now = new Date().getTime();
        const distance = TARGET_DATE - now;

        const elDays = document.getElementById('cdDays');
        const elHours = document.getElementById('cdHours');
        const elMins = document.getElementById('cdMins');
        const elSecs = document.getElementById('cdSecs');

        if (!elDays || !elHours || !elMins || !elSecs) return;

        if (distance <= 0) {
            elDays.textContent = '00';
            elHours.textContent = '00';
            elMins.textContent = '00';
            elSecs.textContent = '00';
            return;
        }

        const days = Math.floor(distance / (1000 * 60 * 60 * 24));
        const hours = Math.floor((distance % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
        const minutes = Math.floor((distance % (1000 * 60 * 60)) / (1000 * 60));
        const seconds = Math.floor((distance % (1000 * 60)) / 1000);

        elDays.textContent = String(days).padStart(2, '0');
        elHours.textContent = String(hours).padStart(2, '0');
        elMins.textContent = String(minutes).padStart(2, '0');
        elSecs.textContent = String(seconds).padStart(2, '0');
    }

    // ==============================================================
    // 💾 RSVP Auto-Save Engine
    // ==============================================================
    function getGuestPayload(card) {
        const guestId = card.getAttribute('data-guest-id');
        const radioChecked = card.querySelector(`input[name="asistencia_${guestId}"]:checked`);
        const asistencia = radioChecked ? radioChecked.value : '';
        const selectedMenu = card.querySelector(`input[name="menu_${guestId}"]:checked`)?.value || 'general';
        const isCeliac = card.querySelector(`input[name="celiaco_${guestId}"]`)?.checked;
        const inputNombre = card.querySelector(`input[name="nombre_${guestId}"]`);
        const inputApellido = card.querySelector(`input[name="apellido_${guestId}"]`);

        return {
            nombre: inputNombre ? inputNombre.value.trim() : '',
            apellido: inputApellido ? inputApellido.value.trim() : '',
            confirmacion: asistencia,
            pa_general: (asistencia === 'si' && selectedMenu === 'general') ? 'si' : '',
            pa_vegetariano: (asistencia === 'si' && selectedMenu === 'vegetariano') ? 'si' : '',
            pa_vegano: (asistencia === 'si' && selectedMenu === 'vegano') ? 'si' : '',
            pa_celiaco: (asistencia === 'si' && isCeliac) ? 'si' : ''
        };
    }

    function setAutoSaveState(state, text) {
        const autoSaveStatus = document.getElementById('autoSaveStatus');
        const autoSaveIcon = document.getElementById('autoSaveIcon');
        const autoSaveText = document.getElementById('autoSaveText');
        if (!autoSaveStatus) return;
        autoSaveStatus.className = 'autosave-status ' + state;
        if (state === 'saving') {
            if (autoSaveIcon) autoSaveIcon.textContent = '⏳';
            if (autoSaveText) autoSaveText.textContent = text || 'Guardando cambios...';
        } else if (state === 'saved') {
            if (autoSaveIcon) autoSaveIcon.textContent = '✓';
            if (autoSaveText) autoSaveText.textContent = text || 'Cambios guardados automáticamente';
        } else if (state === 'error') {
            if (autoSaveIcon) autoSaveIcon.textContent = '⚠️';
            if (autoSaveText) autoSaveText.textContent = text || 'Error al guardar. Reintentando...';
        }
    }

    async function flushAutoSave() {
        if (autoSaveTimer) {
            clearTimeout(autoSaveTimer);
            autoSaveTimer = null;
        }
        if (dirtyGuests.size === 0 || isSaving) return;

        const guestIdsToSave = Array.from(dirtyGuests);
        const actualToSave = [];
        for (const guestId of guestIdsToSave) {
            const card = document.querySelector(`.rsvp-card[data-guest-id="${guestId}"]`);
            if (!card) {
                dirtyGuests.delete(guestId);
                continue;
            }
            const payload = getGuestPayload(card);
            const currentStr = JSON.stringify(payload);
            if (currentStr === savedGuestStates[guestId]) {
                dirtyGuests.delete(guestId);
            } else {
                actualToSave.push({ guestId, card, payload, currentStr });
            }
        }

        if (actualToSave.length === 0) {
            if (dirtyGuests.size === 0) {
                setAutoSaveState('saved', 'Respuestas sincronizadas');
            }
            return;
        }

        isSaving = true;
        setAutoSaveState('saving', 'Guardando respuestas...');
        let allOk = true;

        for (const item of actualToSave) {
            try {
                const res = await fetch(`/invitados/${item.guestId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(item.payload)
                });
                if (res.ok) {
                    savedGuestStates[item.guestId] = item.currentStr;
                    dirtyGuests.delete(item.guestId);
                    const chipDot = document.querySelector(`[data-chip-guest-id="${item.guestId}"] .status-dot`);
                    if (chipDot) {
                        chipDot.className = 'status-dot ' + (item.payload.confirmacion || 'pending');
                    }
                } else {
                    allOk = false;
                }
            } catch (err) {
                allOk = false;
            }
        }

        isSaving = false;

        if (allOk) {
            setAutoSaveState('saved', 'Cambios guardados automáticamente');
        } else {
            setAutoSaveState('error', 'Error al guardar. Se reintentará...');
            setTimeout(flushAutoSave, AUTOSAVE_CONFIG.RETRY_DELAY_MS);
        }
    }

    function queueAutoSave(guestId, delay = AUTOSAVE_CONFIG.CLICK_DEBOUNCE_MS) {
        dirtyGuests.add(guestId);
        setAutoSaveState('saving', 'Guardando cambios...');
        if (autoSaveTimer) clearTimeout(autoSaveTimer);
        autoSaveTimer = setTimeout(flushAutoSave, delay);
    }

    function updateDietIcons(card) {
        const guestId = card.getAttribute('data-guest-id');
        const iconsContainer = card.querySelector(`#diet_icons_${guestId}`);
        if (!iconsContainer) return;

        const radioAsistencia = card.querySelector(`input[name="asistencia_${guestId}"]:checked`);
        const asistencia = radioAsistencia ? radioAsistencia.value : '';

        if (asistencia !== 'si') {
            iconsContainer.innerHTML = '';
            return;
        }

        const selectedMenu = card.querySelector(`input[name="menu_${guestId}"]:checked`)?.value || 'general';
        const isCeliac = card.querySelector(`input[name="celiaco_${guestId}"]`)?.checked;

        let html = '';
        if (selectedMenu === 'vegano') {
            html += '<span class="diet-mini-badge" title="Vegano">🌱 Vegano</span>';
        } else if (selectedMenu === 'vegetariano') {
            html += '<span class="diet-mini-badge" title="Vegetariano">🥗 Vegetariano</span>';
        } else {
            html += '<span class="diet-mini-badge" title="General">🍽️ General</span>';
        }

        if (isCeliac) {
            html += ' <span class="diet-mini-badge badge-celiac" title="Celíaco / Sin Gluten">🌾 Celíaco</span>';
        }

        iconsContainer.innerHTML = html;
    }

    function bindInteractiveEvents() {
        // Desktop miniature previews click to jump
        slides.forEach((s, idx) => {
            s.onclick = (e) => {
                if (idx !== currentIndex) {
                    e.stopPropagation();
                    goToSlide(idx);
                }
            };
        });

        // Copy Alias Handler
        const btnCopyAlias = document.getElementById('btnCopyAlias');
        if (btnCopyAlias) {
            btnCopyAlias.onclick = (e) => {
                e.stopPropagation();
                const aliasEl = document.getElementById('aliasText');
                const alias = aliasEl ? aliasEl.textContent.trim() : 'BODA.CELIA.ALFREDO';
                navigator.clipboard.writeText(alias).then(() => {
                    showToast('¡Alias copiado al portapapeles!');
                }).catch(() => {
                    showToast('Alias: ' + alias);
                });
            };
        }

        // Google Calendar Button
        const btnCal = document.getElementById('btnGoogleCalendar');
        if (btnCal) {
            const containerEl = document.getElementById("storyContainer");
            const inviteUrl = (containerEl && containerEl.dataset.inviteUrl) || window.location.href;
            const detailsText = `Casamiento Celia Muzaber y Alfredo Rezinovsky\n${inviteUrl}`;
            const calUrl = `https://calendar.google.com/calendar/render?action=TEMPLATE&text=Boda+Celia+y+Alfredo&dates=20270319T210000Z/20270320T070000Z&location=Luna+India%3A+Castro+Barros%2C+M5513%2C+Mendoza&details=${encodeURIComponent(detailsText)}`;
            btnCal.href = calUrl;
        }

        // Live Countdown
        updateCountdown();
        setInterval(updateCountdown, 1000);

        // RSVP Accordion Toggles
        document.querySelectorAll('.rsvp-card-header').forEach(header => {
            header.onclick = (e) => {
                e.preventDefault();
                e.stopPropagation();
                flushAutoSave();
                const card = header.closest('.rsvp-card');
                const isExpanded = card.classList.contains('expanded');
                document.querySelectorAll('.rsvp-card').forEach(c => c.classList.remove('expanded'));
                if (!isExpanded) {
                    card.classList.add('expanded');
                }
            };
        });

        // RSVP Form Initial Snapshots
        document.querySelectorAll('.rsvp-card[data-guest-id]').forEach(card => {
            const guestId = card.getAttribute('data-guest-id');
            savedGuestStates[guestId] = JSON.stringify(getGuestPayload(card));
        });

        // RSVP Form Radio & Input Bindings
        const cards = Array.from(document.querySelectorAll('.rsvp-card'));
        cards.forEach((card, cardIdx) => {
            const guestId = card.getAttribute('data-guest-id');
            const badge = card.querySelector('.guest-badge');
            const radios = card.querySelectorAll(`input[name="asistencia_${guestId}"]`);
            const dietSection = card.querySelector('.diet-section');
            const menuRadios = card.querySelectorAll(`input[name="menu_${guestId}"]`);
            const celiacCheck = card.querySelector(`input[name="celiaco_${guestId}"]`);

            radios.forEach(radio => {
                radio.onchange = () => {
                    card.querySelectorAll('.radio-label').forEach(lbl => {
                        lbl.classList.remove('selected-si', 'selected-no', 'selected-pending');
                    });
                    const parentLabel = radio.closest('.radio-label');
                    if (radio.value === 'si') {
                        parentLabel?.classList.add('selected-si');
                        if (dietSection) dietSection.style.display = 'block';
                        if (badge) {
                            badge.className = 'guest-badge badge-si';
                            badge.textContent = '✓ Asiste';
                        }
                        if (!card.querySelector(`input[name="menu_${guestId}"]:checked`)) {
                            const genRadio = card.querySelector(`input[name="menu_${guestId}"][value="general"]`);
                            if (genRadio) {
                                genRadio.checked = true;
                                genRadio.closest('.menu-radio-label')?.classList.add('selected');
                            }
                        }
                    } else if (radio.value === 'no') {
                        parentLabel?.classList.add('selected-no');
                        if (dietSection) dietSection.style.display = 'none';
                        if (badge) {
                            badge.className = 'guest-badge badge-no';
                            badge.textContent = '✗ No asiste';
                        }
                    } else {
                        parentLabel?.classList.add('selected-pending');
                        if (dietSection) dietSection.style.display = 'none';
                        if (badge) {
                            badge.className = 'guest-badge badge-pending';
                            badge.textContent = '⏳ Pendiente';
                        }
                    }

                    updateDietIcons(card);
                    queueAutoSave(guestId, AUTOSAVE_CONFIG.CLICK_DEBOUNCE_MS);

                    const nextCard = cards[cardIdx + 1];
                    if (nextCard && !nextCard.querySelector('input[type="radio"]:checked')) {
                        setTimeout(() => {
                            flushAutoSave();
                            cards.forEach(c => c.classList.remove('expanded'));
                            nextCard.classList.add('expanded');
                        }, 320);
                    }
                };
            });

            menuRadios.forEach(mRadio => {
                mRadio.onchange = () => {
                    card.querySelectorAll('.menu-radio-label').forEach(lbl => lbl.classList.remove('selected'));
                    mRadio.closest('.menu-radio-label')?.classList.add('selected');
                    updateDietIcons(card);
                    queueAutoSave(guestId, AUTOSAVE_CONFIG.CLICK_DEBOUNCE_MS);
                };
            });

            if (celiacCheck) {
                celiacCheck.onchange = () => {
                    const lbl = celiacCheck.closest('.celiac-checkbox-label');
                    if (celiacCheck.checked) {
                        lbl?.classList.add('selected');
                    } else {
                        lbl?.classList.remove('selected');
                    }
                    updateDietIcons(card);
                    queueAutoSave(guestId, AUTOSAVE_CONFIG.CLICK_DEBOUNCE_MS);
                };
            }

            const inputNombre = card.querySelector(`input[name="nombre_${guestId}"]`);
            const inputApellido = card.querySelector(`input[name="apellido_${guestId}"]`);
            const headerName = card.querySelector('.guest-header-name');
            const chipName = document.querySelector(`[data-chip-guest-id="${guestId}"] .chip-name-text`);

            function syncName() {
                const n = inputNombre ? inputNombre.value.trim() : '';
                const a = inputApellido ? inputApellido.value.trim() : '';
                const full = (n || a) ? `${n} ${a}`.trim() : 'Acompañante';
                if (headerName) headerName.textContent = full;
                if (chipName) chipName.textContent = full;
                queueAutoSave(guestId, AUTOSAVE_CONFIG.INPUT_DEBOUNCE_MS);
            }

            if (inputNombre) {
                inputNombre.addEventListener('input', syncName);
                inputNombre.addEventListener('blur', () => flushAutoSave());
            }
            if (inputApellido) {
                inputApellido.addEventListener('input', syncName);
                inputApellido.addEventListener('blur', () => flushAutoSave());
            }
        });
    }

    // Page exit listeners
    window.addEventListener('beforeunload', flushAutoSave);
    window.addEventListener('pagehide', flushAutoSave);
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'hidden') flushAutoSave();
    });

    // ==============================================================
    // 🚀 Instant Initialization (SSR) + Background Real-Time Sync
    // ==============================================================
    function initStory() {
        const slidesContainer = document.getElementById('storySlides');
        if (!slidesContainer) return;

        slides = Array.from(slidesContainer.querySelectorAll('.story-slide'));
        if (slides.length === 0) return;

        buildProgressBars();
        bindInteractiveEvents();

        const targetIdx = getTargetIndexFromHash();
        goToSlide(targetIdx);

        // Optional background check for dynamically toggled slides
        const token = (container && container.dataset.token) || (window.location.pathname.split('/i/')[1] || '').split('/')[0];
        if (token) {
            syncDynamicSlidesInBackground(token, slidesContainer);
        }
    }

    async function syncDynamicSlidesInBackground(token, slidesContainer) {
        try {
            const manifestRes = await fetch(`/i/${token}/slides`);
            if (!manifestRes.ok) return;
            const manifest = await manifestRes.json();
            if (!manifest.ok || !Array.isArray(manifest.slides)) return;

            const currentSlideIds = slides.map(s => s.getAttribute('data-slide-id')).filter(Boolean);
            const serverSlideIds = manifest.slides.map(s => s.id);

            const isMatch = (currentSlideIds.length === serverSlideIds.length) &&
                currentSlideIds.every((id, i) => id === serverSlideIds[i]);

            if (!isMatch) {
                const slideResults = await Promise.all(
                    manifest.slides.map(async (s) => {
                        const res = await fetch(s.url);
                        return res.ok ? await res.text() : null;
                    })
                );

                const validHtmls = slideResults.filter(Boolean);
                if (validHtmls.length > 0) {
                    slidesContainer.innerHTML = validHtmls.join('');
                    slides = Array.from(slidesContainer.querySelectorAll('.story-slide'));
                    buildProgressBars();
                    bindInteractiveEvents();
                    showSlide(currentIndex);
                }
            }
        } catch (e) {
            // Silently ignore background sync errors to preserve user experience
        }
    }

    // Launch instant initialization
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initStory);
    } else {
        initStory();
    }

})();
