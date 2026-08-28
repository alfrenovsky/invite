        const slides = Array.from(document.querySelectorAll('.story-slide'));
        const progressContainer = document.getElementById('progressBarsContainer');
        const tapLeft = document.getElementById('tapLeft');
        const tapRight = document.getElementById('tapRight');
        const btnDesktopPrev = document.getElementById('btnDesktopPrev');
        const btnDesktopNext = document.getElementById('btnDesktopNext');
        const btnPause = document.getElementById('btnPause');
        const btnCopyAlias = document.getElementById('btnCopyAlias');
        const toast = document.getElementById('toast');

        let currentIndex = 0;
        let isAutoplay = false; // Autoplay disabled by default
        let slideStartTime = 0;
        let elapsedTimeOnSlide = 0;
        let animationFrameId = null;

        // Build top progress bar slots
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

        const progressSlots = Array.from(progressContainer.children);

        function updateProgressDisplay(percent) {
            progressSlots.forEach((slot, idx) => {
                const fill = slot.querySelector('.progress-bar-fill');
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

        function showSlide(index) {
            const isDesktop = window.innerWidth >= 768;
            const isLastSlide = (index === slides.length - 1);

            // On the RSVP form slide, disable tap zones so user can freely click inputs and buttons
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
                    s.style.filter = 'blur(0px) brightness(1)';
                    s.style.zIndex = '300';
                    s.style.pointerEvents = 'none';
                    s.style.cursor = 'default';

                } else if (isDesktop) {

                    const sign = offset > 0 ? 1 : -1;
                    const absOffset = Math.abs(offset);
                    const shiftPx = sign * (300 + (absOffset - 1) * 185);

                    s.style.setProperty('--slide-transform', `translateX(${shiftPx}px) scale(0.40)`);
                    s.style.setProperty('--slide-hover-transform', `translateX(${shiftPx}px) scale(0.43)`);
                    s.style.transform = `translateX(${shiftPx}px) scale(0.40)`;
                    s.style.zIndex = (20 - absOffset).toString();
                    s.style.pointerEvents = 'auto';
                    s.style.cursor = 'pointer';



                    const opacityVal = Math.max(0.35, 0.85 - (absOffset - 1) * 0.15).toString();
                    s.style.opacity = opacityVal;

                    if (offset < 0) {
                        // Already seen (left side): ALWAYS FOCUSED
                        s.classList.add('side-slide', 'prev-slide');
                        s.style.filter = 'blur(0px) brightness(0.95)';
                    } else {
                        // Upcoming (right side): BLURRED / UNREADABLE
                        s.classList.add('side-slide', 'next-slide');
                        s.style.filter = 'blur(10px) brightness(0.55)';
                    }
                } else {
                    // Mobile Slide-Over Transitions
                    if (offset > 0) {
                        // Upcoming slides: positioned to the right, ready to slide in on top
                        s.classList.add('next-slide');
                        s.style.transform = 'translateX(100%)';
                        s.style.zIndex = (350 - offset).toString();
                        s.style.opacity = '1';
                        s.style.filter = 'none';
                    } else {
                        // Past slides: rest gently behind active slide
                        s.classList.add('prev-slide');
                        s.style.transform = 'translateX(-30%) scale(0.96)';
                        s.style.zIndex = (200 + offset).toString();
                        s.style.opacity = '0.35';
                        s.style.filter = 'none';
                    }
                    s.style.pointerEvents = 'none';
                    s.style.cursor = 'default';
                }
            });

        }

        // In PC / Desktop, clicking on any miniature preview slide navigates directly to that slide
        slides.forEach((s, idx) => {
            s.addEventListener('click', (e) => {
                if (idx !== currentIndex) {
                    e.stopPropagation();
                    goToSlide(idx);
                }
            });
        });



        window.addEventListener('resize', () => showSlide(currentIndex));



        function startSlideTimer() {
            clearTimers();
            const slide = slides[currentIndex];
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

                elapsedTimeOnSlide = now - slideStartTime;
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
            if (typeof flushAutoSave === 'function' && typeof dirtyGuests !== 'undefined' && dirtyGuests.size > 0) {
                flushAutoSave();
            }
            if (index < 0) index = 0;
            if (index >= slides.length) index = slides.length - 1;

            currentIndex = index;
            elapsedTimeOnSlide = 0;
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
            btnPause.textContent = isAutoplay ? '⏸' : '▶';
            btnPause.title = isAutoplay ? 'Pausar avance automático' : 'Iniciar avance automático';
            if (isAutoplay) {
                startSlideTimer();
            } else {
                clearTimers();
                updateProgressDisplay(100);
            }
        }

        // Tap & Navigation Handlers
        let isSwiping = false;
        tapLeft.addEventListener('click', (e) => {
            if (isSwiping) return;
            prevSlide();
        });
        tapRight.addEventListener('click', (e) => {
            if (isSwiping) return;
            nextSlide();
        });
        btnDesktopPrev.addEventListener('click', prevSlide);
        btnDesktopNext.addEventListener('click', nextSlide);
        btnPause.addEventListener('click', (e) => {
            e.stopPropagation();
            togglePlayPause();
        });


        // Pull to Refresh Elements & State
        const ptrIndicator = document.getElementById('pullToRefreshIndicator');
        const ptrLabel = document.getElementById('ptrLabel');
        let isPullingToRefresh = false;
        let pullEligible = false;
        let isRefreshing = false;

        // Press & Hold to pause + Mobile Touch Swipe & Pull-to-Refresh Navigation
        let holdTimeout = null;
        let touchStartX = 0;
        let touchStartY = 0;
        let touchStartTime = 0;
        const container = document.getElementById('storyContainer');

        function onHoldStart() {
            holdTimeout = setTimeout(() => { isPaused = true; }, 200);
        }

        function onHoldEnd() {
            if (holdTimeout) clearTimeout(holdTimeout);
            isPaused = false;
        }

        container.addEventListener('mousedown', onHoldStart);
        container.addEventListener('mouseup', onHoldEnd);

        container.addEventListener('touchstart', (e) => {
            if (isRefreshing || !e.touches || e.touches.length === 0) return;
            touchStartX = e.touches[0].clientX;
            touchStartY = e.touches[0].clientY;
            touchStartTime = Date.now();
            isSwiping = false;
            isPullingToRefresh = false;

            // Check if eligible for pull to refresh
            pullEligible = false;
            const isLastSlide = (currentIndex === slides.length - 1);
            if (!isLastSlide) {
                pullEligible = true;
            } else {
                const formScroll = document.querySelector('.rsvp-form-container');
                if (formScroll && formScroll.scrollTop <= 5) {
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

            // Horizontal swipe detection
            if (Math.abs(diffX) > 15 && Math.abs(diffX) > Math.abs(diffY)) {
                isSwiping = true;
                if (holdTimeout) clearTimeout(holdTimeout);
                isPaused = false;
            }

            // Pull to refresh downward gesture detection (mobile)
            if (pullEligible && diffY > 15 && diffY > Math.abs(diffX) * 1.2 && window.innerWidth < 768) {
                isPullingToRefresh = true;
                if (holdTimeout) clearTimeout(holdTimeout);
                isPaused = false;

                const pullOffset = Math.min(diffY * 0.42, 95);
                if (ptrIndicator) {
                    ptrIndicator.style.transform = `translate(-50%, ${pullOffset - 50}px)`;
                    ptrIndicator.classList.add('visible');

                    if (diffY >= 80) {
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
            const diffY = touchEndY - touchStartY; // Positive when pulled down
            const duration = Date.now() - touchStartTime;

            // Handle Pull to Refresh on release
            if (isPullingToRefresh && ptrIndicator) {
                if (diffY >= 80) {
                    isRefreshing = true;
                    ptrIndicator.classList.remove('ready');
                    ptrIndicator.classList.add('refreshing');
                    if (ptrLabel) ptrLabel.textContent = 'Recargando...';
                    ptrIndicator.style.transform = 'translate(-50%, 30px)';
                    setTimeout(() => {
                        window.location.reload();
                    }, 400);
                    return;
                } else {
                    ptrIndicator.style.transform = 'translate(-50%, -120px)';
                    ptrIndicator.classList.remove('visible', 'ready');
                    isPullingToRefresh = false;
                }
            }

            // On RSVP slide, allow vertical scrolling inside form without triggering horizontal slide change
            const target = e.target;
            if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.closest('.rsvp-form-container'))) {
                if (currentIndex === slides.length - 1 && Math.abs(touchStartY - touchEndY) > Math.abs(diffX)) {
                    setTimeout(() => { isSwiping = false; }, 50);
                    return;
                }
            }

            // Detect horizontal swipe: distance >= 40px, predominantly horizontal, within 800ms
            const minSwipeDistance = 40;
            if (Math.abs(diffX) >= minSwipeDistance && Math.abs(diffX) > Math.abs(touchStartY - touchEndY) * 1.2 && duration < 800) {
                if (diffX > 0) {
                    // Swiped Left -> Next Slide
                    nextSlide();
                } else {
                    // Swiped Right -> Previous Slide
                    prevSlide();
                }
            }

            // Reset isSwiping after short delay so clicks don't double fire
            setTimeout(() => { isSwiping = false; }, 80);
        }, { passive: true });



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

        // Copy Alias Handler
        if (btnCopyAlias) {
            btnCopyAlias.addEventListener('click', (e) => {
                e.stopPropagation();
                const alias = document.getElementById('aliasText').textContent.trim();
                navigator.clipboard.writeText(alias).then(() => {
                    showToast('¡Alias copiado al portapapeles!');
                }).catch(() => {
                    showToast('Alias: ' + alias);
                });
            });
        }

        // Ensure Google Calendar link always contains exact details and invitation URL
        const btnCal = document.getElementById('btnGoogleCalendar');
        if (btnCal) {
            const containerEl = document.getElementById("storyContainer");
        const inviteUrl = (containerEl && containerEl.dataset.inviteUrl) || window.location.href;
            const detailsText = `Casamiento Celia Muzaber y Alfredo Rezinovsky\n${inviteUrl}`;
            const calUrl = `https://calendar.google.com/calendar/render?action=TEMPLATE&text=Boda+Celia+y+Alfredo&dates=20270319T210000Z/20270320T070000Z&location=Luna+India%3A+Castro+Barros%2C+M5513%2C+Mendoza&details=${encodeURIComponent(detailsText)}`;
            btnCal.href = calUrl;
        }

        function showToast(msg) {

            toast.textContent = msg;
            toast.classList.add('show');
            setTimeout(() => { toast.classList.remove('show'); }, 2500);
        }

        // Live Countdown to March 19, 2027 18:00 (UTC-3)
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

        updateCountdown();
        setInterval(updateCountdown, 1000);

        // ==============================================================
        // ⚙️ Configuración de Tiempos de Auto-Guardado (Milisegundos)
        // ==============================================================
        const AUTOSAVE_CONFIG = {
            CLICK_DEBOUNCE_MS: 10000, // 10 segundos de inactividad tras clics (Asistencia, Menú, Celíaco)
            INPUT_DEBOUNCE_MS: 10000, // 10 segundos de inactividad tras teclear (Nombre, Apellido)
            RETRY_DELAY_MS: 4000      // Intervalo de reintento si falla la conexión
        };


        // Auto-Save Engine with Change-Detection & Throttling
        const dirtyGuests = new Set();

        const savedGuestStates = {};
        let autoSaveTimer = null;
        let isSaving = false;
        const autoSaveStatus = document.getElementById('autoSaveStatus');
        const autoSaveIcon = document.getElementById('autoSaveIcon');
        const autoSaveText = document.getElementById('autoSaveText');

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

        // Initialize snapshot of currently loaded states
        document.querySelectorAll('.rsvp-card[data-guest-id]').forEach(card => {
            const guestId = card.getAttribute('data-guest-id');
            savedGuestStates[guestId] = JSON.stringify(getGuestPayload(card));
        });

        function setAutoSaveState(state, text) {
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
            } else {
                if (autoSaveIcon) autoSaveIcon.textContent = '✓';
                if (autoSaveText) autoSaveText.textContent = text || 'Respuestas sincronizadas';
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
                        // Update Slide 1 dot status live
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

        // Direct Accordion Toggle Handler with auto-save flush
        function initAccordion() {
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
        }
        initAccordion();


        // Helper to dynamically update dietary badge/icon in guest header
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

        // Radio button dynamic styling & badge update binding
        function bindRadioEvents(scope) {
            const cards = Array.from(scope.querySelectorAll('.rsvp-card'));
            cards.forEach((card, cardIdx) => {
                const guestId = card.getAttribute('data-guest-id');
                const badge = card.querySelector('.guest-badge');
                const radios = card.querySelectorAll(`input[name="asistencia_${guestId}"]`);
                const dietSection = card.querySelector('.diet-section');
                const menuRadios = card.querySelectorAll(`input[name="menu_${guestId}"]`);
                const celiacCheck = card.querySelector(`input[name="celiaco_${guestId}"]`);

                // Attendance Radios
                radios.forEach(radio => {
                    radio.onchange = () => {
                        card.querySelectorAll('.radio-label').forEach(lbl => {
                            lbl.classList.remove('selected-si', 'selected-no', 'selected-pending');
                        });
                        const parentLabel = radio.closest('.radio-label');
                        if (radio.value === 'si') {
                            parentLabel.classList.add('selected-si');
                            if (dietSection) dietSection.style.display = 'block';
                            if (badge) {
                                badge.className = 'guest-badge badge-si';
                                badge.textContent = '✓ Asiste';
                            }
                            // Ensure a base menu is checked if none
                            if (!card.querySelector(`input[name="menu_${guestId}"]:checked`)) {
                                const genRadio = card.querySelector(`input[name="menu_${guestId}"][value="general"]`);
                                if (genRadio) {
                                    genRadio.checked = true;
                                    genRadio.closest('.menu-radio-label')?.classList.add('selected');
                                }
                            }
                        } else if (radio.value === 'no') {
                            parentLabel.classList.add('selected-no');
                            if (dietSection) dietSection.style.display = 'none';
                            if (badge) {
                                badge.className = 'guest-badge badge-no';
                                badge.textContent = '✗ No asiste';
                            }
                        } else {
                            parentLabel.classList.add('selected-pending');
                            if (dietSection) dietSection.style.display = 'none';
                            if (badge) {
                                badge.className = 'guest-badge badge-pending';
                                badge.textContent = '⏳ Pendiente';
                            }
                        }

                        updateDietIcons(card);
                        queueAutoSave(guestId, AUTOSAVE_CONFIG.CLICK_DEBOUNCE_MS);

                        // Auto-expand next pending guest card if any
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

                // Mutually Exclusive Menu Radios
                menuRadios.forEach(mRadio => {
                    mRadio.onchange = () => {
                        card.querySelectorAll('.menu-radio-label').forEach(lbl => lbl.classList.remove('selected'));
                        mRadio.closest('.menu-radio-label')?.classList.add('selected');
                        updateDietIcons(card);
                        queueAutoSave(guestId, AUTOSAVE_CONFIG.CLICK_DEBOUNCE_MS);
                    };
                });

                // Independent Celiac Checkbox
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

                // Editable Name live synchronization
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


        bindRadioEvents(document);

        // Page exit listeners to guarantee all pending changes are sent
        window.addEventListener('beforeunload', flushAutoSave);
        window.addEventListener('pagehide', flushAutoSave);
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'hidden') flushAutoSave();
        });



        // Initialize First Slide
        goToSlide(0);




    })();
    </script>

