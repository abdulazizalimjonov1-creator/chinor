/* ================================================================
           🔐  XAVFSIZ AUTH — BOT API ORQALI
           ================================================================
           Frontend bot'ning HTTPS API'siga POST /api/login yuboradi.
           Bot tekshirib, admin yoki klient ekanini qaytaradi → biz shu
           rolga mos panelni ochamiz.

           ⚙️ SOZLASH (deploy oldidan o'zgartiring):
               API_BASE_URL — bot'ning ochiq HTTPS manzili.
               Lokal botni ngrok bilan ochsangiz: https://abc123.ngrok.io
               VPS bo'lsa: https://yourdomain.com

           Yangi foydalanuvchi qo'shish: Telegram botda «🔑 Login/parol»
           menyusiga kirib yarating.
           ---------------------------------------------------------------- */

        // ⚙️ Bot HTTPS API manzilini SHU YERGA YOZING (`https://...`).
        //    URL parametri orqali ham o'zgartirish mumkin:
        //    https://fronted-bgq.pages.dev/?api=https://abc.ngrok.io
        const _urlParams = new URLSearchParams(location.search);
        const _STORAGE_API_KEY = 'chinor_api_url';
        const _DEFAULT_API = 'https://kassa.chinorpos.com';
        let API_BASE_URL = (
            _urlParams.get('api') ||
            (() => { try { return localStorage.getItem(_STORAGE_API_KEY); } catch(_){} return null; })() ||
            _DEFAULT_API
        ).replace(/\/+$/, '');
        // Eski ngrok manzili keshda (localStorage yoki URL) qolgan bo'lsa — uni
        // tashlab joriy domenga o'tamiz (ngrok endi butunlay ishlatilmaydi).
        if (/ngrok/i.test(API_BASE_URL)) {
            API_BASE_URL = _DEFAULT_API;
            try { localStorage.setItem(_STORAGE_API_KEY, _DEFAULT_API); } catch(_) {}
        }
        // URL parametr orqali kelgan bo'lsa (va ngrok bo'lmasa) — saqlab qo'yamiz
        if (_urlParams.get('api') && !/ngrok/i.test(_urlParams.get('api'))) {
            try { localStorage.setItem(_STORAGE_API_KEY, _urlParams.get('api')); } catch(_) {}
        }
        // URL parametr orqali o'zgartirilgan bo'lsa, eslab qolamiz
        try {
            const fromUrl = _urlParams.get('api');
            if (fromUrl) localStorage.setItem('chinor_api_url', fromUrl);
        } catch (_) {}

        // Session token — login'dan keyin saqlanadi, har so'rovga qo'shiladi
        let _sessionToken = '';
        try { _sessionToken = localStorage.getItem('chinor_session') || ''; } catch(_) {}

        // Lokal validatsiya — bot qoidalariga mos (8+, harf+son)
        const MIN_LEN = 8;
        const USERNAME_OK = /^[A-Za-z0-9._-]+$/;
        function _hasLetterDigit(s) {
            return /[A-Za-z]/.test(s) && /\d/.test(s);
        }
        function validateUsername(s) {
            if (!s) return "Login bo'sh.";
            if (s.length < MIN_LEN) return "Login kamida " + MIN_LEN + " ta belgi.";
            if (!USERNAME_OK.test(s)) return "Faqat lotin harflari, raqamlar va . _ -";
            if (!_hasLetterDigit(s)) return "Loginda harf VA son qatnashishi kerak.";
            return null;
        }
        function validatePasswordRule(s) {
            if (!s) return "Parol bo'sh.";
            if (s.length < MIN_LEN) return "Parol kamida " + MIN_LEN + " ta belgi.";
            if (!_hasLetterDigit(s)) return "Parolda harf VA son qatnashishi kerak.";
            return null;
        }

        /* ================================================================
           KONFIGURATSIYA TUGADI
           ================================================================ */

        // ----- Telegram -----
        const tg = window.Telegram?.WebApp;
        tg?.expand();

        // ----- DOM refs -----
        const $ = (id) => document.getElementById(id);

        const screenLogin    = $('screenLogin');
        const screenAdmin    = $('screenAdmin');
        const screenClient   = $('screenClient');
        const screenRegister = $('screenRegister');

        const loginForm      = $('loginForm');
        const loginInput     = $('loginInput');
        const passwordInput  = $('passwordInput');
        const loginWrap      = $('loginWrap');
        const passwordWrap   = $('passwordWrap');
        const loginBtn       = $('loginBtn');
        const errorMessage   = $('errorMessage');
        const togglePassBtn  = $('togglePassBtn');
        const rememberMe     = $('rememberMe');
        const strengthBars   = document.querySelectorAll('#strengthBars span');
        const strengthLabel  = $('strengthLabel');

        const regBackBtn     = $('regBackBtn');
        const registerForm   = $('registerForm');
        const regLogin       = $('regLogin');
        const regName        = $('regName');
        const regPhone       = $('regPhone');
        const regPassword    = $('regPassword');
        const regPasswordConfirm = $('regPasswordConfirm');
        const regBtn         = $('regBtn');
        const regErrorMessage = $('regErrorMessage');
        const regSuccess     = $('regSuccess');
        const regLoginWrap   = $('regLoginWrap');
        const regPassWrap    = $('regPassWrap');
        const regPassConfirmWrap = $('regPassConfirmWrap');
        const toggleRegPassBtn = $('toggleRegPassBtn');

        const logoutBtnAdmin   = $('logoutBtnAdmin');
        const logoutBtnClient  = $('logoutBtnClient');

        const adminAvatar = $('adminAvatar');
        const adminName   = $('adminName');
        const clientAvatar = $('clientAvatar');
        const clientName   = $('clientName');

        // ===== Screen switcher (barcha .screen larni boshqaradi) =====
        function showScreen(id) {
            document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
            const el = (typeof id === 'string') ? $(id) : id;
            if (el) el.classList.add('active');
        }

        // ===== Error helpers =====
        function showError(el, msg) {
            el.textContent = msg;
            el.classList.add('show');
        }
        function hideError(el) {
            el.classList.remove('show');
        }
        function clearFieldError(wrap) {
            wrap?.classList.remove('error');
        }

        // ===== Password toggle =====
        let passVisible = false;
        togglePassBtn.addEventListener('click', () => {
            passVisible = !passVisible;
            passwordInput.type = passVisible ? 'text' : 'password';
            togglePassBtn.textContent = passVisible ? '🙈' : '👁️';
        });

        let regPassVisible = false;
        toggleRegPassBtn.addEventListener('click', () => {
            regPassVisible = !regPassVisible;
            regPassword.type = regPassVisible ? 'text' : 'password';
            toggleRegPassBtn.textContent = regPassVisible ? '🙈' : '👁️';
        });

        // ===== Password strength =====
        function updateStrength(value) {
            const len = value.length;
            const hasUpper = /[A-Z]/.test(value);
            const hasLower = /[a-z]/.test(value);
            const hasDigit = /\d/.test(value);
            const hasSymbol = /[^a-zA-Z0-9]/.test(value);

            let score = 0;
            if (len >= 4)  score++;
            if (len >= 8)  score++;
            if (hasUpper && hasLower) score++;
            if (hasDigit)  score++;
            if (hasSymbol) score++;

            let level = 0, label = '', cls = '';
            if (value.length === 0) {
                level = 0; label = '';
            } else if (score <= 1) {
                level = 1; cls = 'weak';   label = 'Zaif';
            } else if (score <= 3) {
                level = 2; cls = 'medium'; label = 'O‘rtacha';
            } else {
                level = 3; cls = 'strong'; label = 'Kuchli';
            }

            strengthBars.forEach((bar, i) => {
                bar.className = '';
                if (i < level) bar.classList.add('active', cls);
            });
            strengthLabel.textContent = value.length > 0 ? `Parol kuchi: ${label}` : '';
        }
        passwordInput.addEventListener('input', () => updateStrength(passwordInput.value));

        // ===== Clear errors on input =====
        loginInput.addEventListener('input', () => { clearFieldError(loginWrap); hideError(errorMessage); });
        passwordInput.addEventListener('input', () => { clearFieldError(passwordWrap); hideError(errorMessage); });
        regLogin.addEventListener('input', () => { clearFieldError(regLoginWrap); hideError(regErrorMessage); });
        regPassword.addEventListener('input', () => { clearFieldError(regPassWrap); hideError(regErrorMessage); });
        regPasswordConfirm.addEventListener('input', () => { clearFieldError(regPassConfirmWrap); hideError(regErrorMessage); });

        // ================================================================
        // LOGIN SUBMIT — bot HTTPS API orqali, panel WebApp ichida ochiladi
        // ================================================================
        loginForm.addEventListener('submit', async function (e) {
            e.preventDefault();
            hideError(errorMessage);
            clearFieldError(loginWrap);
            clearFieldError(passwordWrap);

            const login = loginInput.value.trim();
            const password = passwordInput.value;

            // --- Lokal validatsiya ---
            const uErr = validateUsername(login);
            if (uErr) {
                loginWrap.classList.add('error');
                showError(errorMessage, uErr);
                loginInput.focus();
                return;
            }
            const pErr = validatePasswordRule(password);
            if (pErr) {
                passwordWrap.classList.add('error');
                showError(errorMessage, pErr);
                passwordInput.focus();
                return;
            }

            // --- Telegram WebApp ichida bo'lishimiz kerak (initData uchun) ---
            const initData = (tg && tg.initData) || 'LOCAL_TEST';
            // LOCAL TEST: initData tekshiruvi o'chirilgan

            // --- Eslab qolish ---
            try {
                if (rememberMe.checked) localStorage.setItem('chinor_remember', login);
                else localStorage.removeItem('chinor_remember');
            } catch (_) {}

            // --- Loading ---
            loginBtn.classList.add('loading');
            loginBtn.disabled = true;

            // --- API ga POST ---
            try {
                const resp = await fetch(`${API_BASE_URL}/api/login`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        // Ngrok bepul versiyasidagi "browser warning" sahifasini
                        // o'tkazib yuborish uchun (aks holda fetch buziladi).
                        'ngrok-skip-browser-warning': 'true'
                    },
                    body: JSON.stringify({
                        login: login,
                        password: password,
                        initData: initData
                    })
                });
                const data = await resp.json().catch(() => ({}));

                loginBtn.classList.remove('loading');
                loginBtn.disabled = false;

                if (!resp.ok || !data.ok) {
                    let msg = data.error || 'Login amalga oshmadi';
                    if (typeof data.remaining_attempts === 'number' &&
                        data.remaining_attempts > 0 && !data.just_locked) {
                        msg += ` (yana ${data.remaining_attempts} ta urinish qoldi)`;
                    }
                    passwordWrap.classList.add('error');
                    showError(errorMessage, msg);
                    passwordInput.focus();
                    return;
                }

                // --- Muvaffaqiyat: session token saqlash ---
                if (data.session_token) {
                    _sessionToken = data.session_token;
                    try { localStorage.setItem('chinor_session', _sessionToken); } catch(_) {}
                }

                openPanelFor(data);
            } catch (err) {
                loginBtn.classList.remove('loading');
                loginBtn.disabled = false;
                showError(errorMessage,
                    'Tarmoq xatosi. Bot API ulanmadi.\n' +
                    'API manzili: ' + API_BASE_URL + '\n' +
                    'Xato: ' + (err && err.message || err));
            }
        });

        // ================================================================
        // REGISTRATION — bu yerda emas, BOT orqali
        // ================================================================
        // Ro'yxatdan o'tish formasi endi shunchaki ko'rsatma beradi:
        // bot ichida «🔑 Login/parol» menyusi orqali yarating.
        if (registerForm) {
            registerForm.addEventListener('submit', function (e) {
                e.preventDefault();
                showError(regErrorMessage,
                    'Bu yerda ro\'yxatdan o\'tib bo\'lmaydi. Iltimos, ' +
                    'botda «🔑 Login/parol» tugmasini bosib login yarating.');
            });
        }

        // ================================================================
        // RESTORE REMEMBERED LOGIN
        // ================================================================
        try {
            const saved = localStorage.getItem('chinor_remember');
            if (saved) {
                loginInput.value = saved;
                rememberMe.checked = true;
            }
        } catch (_) {}

        // ================================================================
        // PANELNI OCHISH — login ham, avtomatik kirish ham shuni chaqiradi
        // ================================================================
        function openPanelFor(data) {
            if (data && data.session_token) {
                _sessionToken = data.session_token;
                try { localStorage.setItem('chinor_session', _sessionToken); } catch(_) {}
            }
            const u = (data && data.user) || {};
            const letter = (u.name || 'U').charAt(0).toUpperCase();
            if (data && data.role === 'admin') {
                adminAvatar.textContent = letter;
                adminName.textContent = `Xush kelibsiz, ${u.name}!`;
                showScreen(screenAdmin);
                loadDashboardStats();
            } else {
                clientAvatar.textContent = letter;
                clientName.textContent = u.name || 'Klient';
                showScreen(screenClient);
                initClientScreen();
            }
        }

        // ================================================================
        // AVTOMATIK KIRISH — Telegram initData orqali, parolsiz
        // ================================================================
        // Mini App Telegram ichida ochiladi, ya'ni Telegram uni bot tokeni
        // bilan imzolangan initData bilan ta'minlaydi. Bu imzo login+paroldan
        // ishonchliroq — soxtalashtirib bo'lmaydi va unutib ham qo'yilmaydi.
        // Shuning uchun login ekrani odatda umuman ko'rinmaydi; u faqat
        // zaxira sifatida qoladi (Telegramdan tashqarida ochilgan hollar va
        // ish stoli ilovalari uchun).
        function _authSplash(text) {
            // Login ekrani standart holatda ochiq turadi, avto-kirish esa
            // asinxron — shu sabab bir lahza login formasi ko'rinib ketardi.
            // Uning ustiga qatlam qo'yamiz, natija ma'lum bo'lgach olinadi.
            let el = document.getElementById('authSplash');
            if (!el) {
                el = document.createElement('div');
                el.id = 'authSplash';
                el.style.cssText = 'position:fixed;inset:0;z-index:9999;display:flex;' +
                    'align-items:center;justify-content:center;text-align:center;' +
                    'padding:24px;background:var(--bg,#fff);font-size:15px;line-height:1.6';
                document.body.appendChild(el);
            }
            el.innerHTML = text;
            return el;
        }

        function _authSplashOff() {
            const el = document.getElementById('authSplash');
            if (el) el.remove();
        }

        async function autoAuth() {
            _authSplash('⏳ Kirilmoqda…');
            const initData = (tg && tg.initData) || '';
            if (!initData) {
                // Telegram imzo bermadi. Odatda ilova Telegramdan tashqarida
                // ochilgan; lekin eski Telegram versiyalarida ham bo'ladi.
                // TASHXIS: nima bor, nima yo'q — taxmin qilmaslik uchun.
                const u = (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) || null;
                const diag =
                    'tg: ' + (tg ? 'bor' : 'YO\'Q') +
                    ' · versiya: ' + ((tg && tg.version) || '—') +
                    ' · platforma: ' + ((tg && tg.platform) || '—') +
                    '<br>initDataUnsafe.user: ' + (u ? ('bor, id=' + u.id) : 'yo\'q') +
                    '<br>hash uzunligi: ' + (location.hash || '').length +
                    '<br>tgWebAppData hash ichida: ' +
                    ((location.hash || '').indexOf('tgWebAppData') >= 0 ? 'HA' : 'yo\'q');
                _authSplash(
                    '🔒 <b>Telegram imzosi kelmadi</b><br><br>' +
                    '<div style="font-size:12px;opacity:.75;text-align:left;' +
                    'background:rgba(128,128,128,.12);padding:10px;border-radius:8px">' +
                    diag + '</div><br>' +
                    '<button onclick="document.getElementById(\'authSplash\').remove()" ' +
                    'style="padding:10px 18px;border:none;border-radius:10px;' +
                    'background:var(--primary,#2481cc);color:#fff;font-size:14px">' +
                    'Login/parol bilan kirish</button>');
                return false;
            }
            try {
                const resp = await fetch(`${API_BASE_URL}/api/auth/telegram`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Telegram-Init-Data': initData,
                    },
                    body: JSON.stringify({ initData: initData }),
                });
                const data = await resp.json().catch(() => ({}));
                if (resp.ok && data.ok) {
                    _authSplashOff();
                    openPanelFor(data);
                    return true;
                }
                // Nosozlik JIM qolmasin — sababini ko'rsatamiz.
                _authSplash(
                    '⚠️ <b>Avtomatik kirish bo\'lmadi</b><br><br>' +
                    ((data && data.error) || ('Server javobi: ' + resp.status)) +
                    '<br><br><button onclick="document.getElementById(\'authSplash\').remove()" ' +
                    'style="padding:10px 18px;border:none;border-radius:10px;' +
                    'background:var(--primary,#2481cc);color:#fff;font-size:14px">' +
                    'Login/parol bilan kirish</button>');
            } catch (e) {
                _authSplash(
                    '📡 <b>Serverga ulanmadi</b><br><br>' +
                    (API_BASE_URL) + '<br>' + ((e && e.message) || e) +
                    '<br><br><button onclick="document.getElementById(\'authSplash\').remove()" ' +
                    'style="padding:10px 18px;border:none;border-radius:10px;' +
                    'background:var(--primary,#2481cc);color:#fff;font-size:14px">' +
                    'Login/parol bilan kirish</button>');
            }
            return false;
        }

        // Ilova ochilishi bilan urinib ko'ramiz.
        autoAuth();

        // ================================================================
        // LOGOUT
        // ================================================================
        function logout() {
            _sessionToken = '';
            try { localStorage.removeItem('chinor_session'); } catch(_) {}
            loginInput.value = '';
            passwordInput.value = '';
            passwordInput.type = 'password';
            passVisible = false;
            togglePassBtn.textContent = '👁️';
            hideError(errorMessage);
            updateStrength('');
            showScreen(screenLogin);
            loginInput.focus();
        }

        logoutBtnAdmin.addEventListener('click', logout);
        logoutBtnClient.addEventListener('click', logout);

        // ================================================================
        // REGISTRATION BACK BUTTON
        // ================================================================
        regBackBtn.addEventListener('click', () => {
            showScreen(screenLogin);
        });

        // ================================================================
        // API YORDAMCHI — har so'rovga initData + ngrok header qo'shadi
        // ================================================================
        async function apiFetch(path, opts = {}) {
            const headers = Object.assign({
                'ngrok-skip-browser-warning': 'true',
                'X-Telegram-Init-Data': (tg && tg.initData) || '',
                'X-Session-Token': _sessionToken || ''
            }, opts.headers || {});
            const resp = await fetch(`${API_BASE_URL}${path}`, {
                ...opts, headers
            });
            let data = {};
            try { data = await resp.json(); } catch (_) {}
            return { ok: resp.ok, status: resp.status, data };
        }

        function fmtSum(n) {
            n = Number(n) || 0;
            return n.toLocaleString('ru-RU').replace(/,/g, ' ');
        }
        function fmtPrice(p) {
            // USD bo'lsa ham, so'mni ko'rsatamiz (do'kon asosan so'mda)
            if (p.price_sum > 0) return fmtSum(p.price_sum) + " so'm";
            if (p.price_usd > 0) return '$' + p.price_usd.toFixed(2);
            return '—';
        }
        function fmtQty(q) {
            q = Number(q) || 0;
            return Number.isInteger(q) ? String(q) : q.toFixed(2);
        }

        // ================================================================
        // MAHSULOTLAR EKRANI
        // ================================================================
        let _prodRole = 'client';
        let _prodPage = 0;
        let _prodQuery = '';
        let _prodLoading = false;
        let _prevScreen = null;
        let _searchTimer = null;

        const productsList   = $('productsList');
        const productsStatus = $('productsStatus');
        const productsMore   = $('productsMore');
        const productSearch  = $('productSearch');
        const productSearchClear = $('productSearchClear');

        window.openProducts = function (role) {
            _prodRole = role || 'client';
            _prevScreen = (role === 'admin') ? 'screenAdmin' : 'screenClient';
            _prodQuery = '';
            productSearch.value = '';
            productSearchClear.style.display = 'none';
            $('productsTitle').textContent =
                (role === 'admin') ? '📦 Mahsulotlar' : '📦 Mahsulotlar katalogi';
            // "➕ Yangi" tugmasi faqat adminga
            const addBtn = $('prodAddBtn');
            if (addBtn) addBtn.style.display = (role === 'admin') ? 'block' : 'none';
            showScreen('screenProducts');
            loadProducts(true);
        };

        // To'liq rasm URL (image_url nisbiy bo'lsa API_BASE_URL qo'shamiz)
        function fullImg(u) {
            if (!u) return '';
            return /^https?:/i.test(u) ? u : (API_BASE_URL + u);
        }

        // Rasmni <img> ga yuklash — brauzer kesh ishlatadi (tez!)
        function setImgSrc(imgEl, url) {
            if (!imgEl || !url) return;
            imgEl.src = fullImg(url);
            imgEl.style.display = 'block';
            imgEl.loading = 'lazy';
            imgEl.onerror = function () { this.style.display = 'none'; };
        }

        window.goBackFromList = function () {
            showScreen(_prevScreen || 'screenClient');
        };

        async function loadProducts(reset) {
            if (_prodLoading) return;
            _prodLoading = true;
            if (reset) {
                _prodPage = 0;
                productsList.innerHTML = '';
                productsMore.style.display = 'none';
            }
            productsStatus.textContent = 'Yuklanmoqda...';
            try {
                const qs = new URLSearchParams({
                    page: String(_prodPage),
                    q: _prodQuery
                }).toString();
                const { ok, status, data } = await apiFetch('/api/products?' + qs);
                if (!ok || !data.ok) {
                    productsStatus.textContent =
                        (status === 401)
                            ? '🔒 Avtorizatsiya muddati tugadi. Qaytadan kiring.'
                            : ('Xato: ' + (data.error || status));
                    _prodLoading = false;
                    return;
                }
                renderProducts(data.items || []);
                if (!productsList.children.length) {
                    productsStatus.textContent = _prodQuery
                        ? `«${_prodQuery}» bo'yicha hech narsa topilmadi.`
                        : 'Mahsulot yo\'q.';
                } else {
                    productsStatus.textContent =
                        `Jami: ${data.total} ta` + (_prodQuery ? ` («${_prodQuery}»)` : '');
                }
                productsMore.style.display = data.has_more ? 'block' : 'none';
            } catch (err) {
                productsStatus.textContent =
                    'Tarmoq xatosi. API: ' + API_BASE_URL + ' — ' + (err.message || err);
            }
            _prodLoading = false;
        }

        window.loadMoreProducts = function () {
            _prodPage += 1;
            loadProducts(false);
        };

        function renderProducts(items) {
            const frag = document.createDocumentFragment();
            items.forEach(p => {
                const card = document.createElement('div');
                card.className = 'prod-card';
                const low = p.qty <= 0;
                const qtyCls = low ? 'qty-low' : 'qty-ok';
                const qtyTxt = low ? '🔴 Tugagan'
                                   : `✅ ${fmtQty(p.qty)} ${p.unit}`;
                const bc = p.barcode
                    ? `<span class="prod-badge">#${p.barcode}</span>` : '';
                // Kichik rasm (bo'lsa) — chap tomonda
                const thumbId = 'th_' + p.id + '_' + Math.random().toString(36).slice(2,7);
                const thumb = p.image_url
                    ? `<img class="prod-thumb" id="${thumbId}" alt="" style="display:none">`
                    : `<div class="prod-thumb prod-thumb-empty">📦</div>`;
                card.innerHTML = `
                    ${thumb}
                    <div class="prod-info">
                        <div class="prod-name">${escapeHtml(p.name)}${bc}</div>
                        <div class="prod-meta">ID: ${p.id}</div>
                    </div>
                    <div class="prod-right">
                        <div class="prod-price">${fmtPrice(p)}</div>
                        <div class="prod-qty ${qtyCls}">${qtyTxt}</div>
                    </div>`;
                card.onclick = () => showProductDetail(p);
                frag.appendChild(card);
                if (p.image_url) {
                    // DOM ga qo'shilgach rasmni yuklaymiz
                    setTimeout(() => setImgSrc(document.getElementById(thumbId), p.image_url), 0);
                }
            });
            productsList.appendChild(frag);
        }

        let _curProduct = null;   // detal ekranida ko'rilayotgan mahsulot
        let _catsMap = null;      // {id: nom} — bo'lim nomini ko'rsatish uchun

        async function ensureCats() {
            if (_catsMap) return _catsMap;
            _catsMap = {};
            const { ok, data } = await apiFetch('/api/categories');
            if (ok && data.ok) (data.categories || []).forEach(c => _catsMap[c.id] = c.name);
            return _catsMap;
        }

        function showProductDetail(p) {
            _curProduct = p;
            const isAdmin = (_prodRole === 'admin');
            const low = p.qty <= 0;
            let html = '';
            if (p.image_url) {
                html += `<img class="detail-img" id="detailImg" alt="" style="display:none"
                              onclick="clZoomImg(this.src)">`;
                // Asl (foni kesilmagan) rasm ham bo'lsa — ko'rish tugmasi
                if (p.image_orig_url && p.image_orig_url !== p.image_url) {
                    html += `<button class="orig-img-btn"
                        onclick="clZoomImg('${fullImg(p.image_orig_url)}')">📷 Asl rasmni ko'rish</button>`;
                }
            }
            html += `<div class="stat-block">`;
            html += `<h3>${escapeHtml(p.name)}</h3>`;
            if (p.description)
                html += `<div class="prod-meta" style="margin-bottom:10px">${escapeHtml(p.description)}</div>`;
            html += `<div class="stat-row"><span class="lbl">💰 Narxi</span><span class="val val-green">${fmtPrice(p)}/${p.unit}</span></div>`;
            html += `<div class="stat-row"><span class="lbl">📦 Qoldiq</span><span class="val ${low?'val-red':''}">${low?'Tugagan':fmtQty(p.qty)+' '+p.unit}</span></div>`;
            if (p.barcode)
                html += `<div class="stat-row"><span class="lbl">🔖 Shtrix-kod</span><span class="val">${escapeHtml(p.barcode)}</span></div>`;
            html += `<div class="stat-row"><span class="lbl">🆔 ID</span><span class="val">${p.id}</span></div>`;
            html += `<div class="stat-row"><span class="lbl">🗂 Bo'lim</span>
                     <span class="val" id="detailCat">${p.category_id ? '…' : '—'}</span></div>`;
            if (isAdmin && p.cost_price_sum != null) {
                html += `<div class="stat-row"><span class="lbl">🏷 Tannarx</span><span class="val">${fmtSum(p.cost_price_sum)} so'm</span></div>`;
                if (p.wholesale_sum)
                    html += `<div class="stat-row"><span class="lbl">📦 Optom</span><span class="val">${fmtSum(p.wholesale_sum)} so'm</span></div>`;
            }
            html += `</div>`;
            if (isAdmin) {
                html += `<div class="detail-actions">
                    <button class="act-btn act-edit" onclick="openProductForm(${p.id})">✏️ Tahrirlash</button>
                    <button class="act-btn act-qty" onclick="prodQty(${p.id})">📦 Prixod</button>
                    <button class="act-btn act-del" onclick="prodDelete(${p.id})">🗑</button>
                </div>`;
                // AI amallari: bo'limni rasmga qarab aniqlash va rasmni qayta
                // brendlash (fonni kesish). Ikkalasi ham asl rasmni saqlab qoladi.
                html += `<div class="detail-actions" style="margin-top:8px">
                    <button class="act-btn act-qty" id="btnAiCat"
                        onclick="prodCategorize(${p.id})">🏷 Bo'limni aniqla (AI)</button>`;
                if (p.image_url)
                    html += `<button class="act-btn act-qty" id="btnRebrand"
                        onclick="prodRebrand(${p.id})">🖼 Rasmni tozalash</button>`;
                html += `</div>`;
            }
            $('prodDetailBody').innerHTML = html;
            if (p.image_url) setImgSrc($('detailImg'), p.image_url);
            if (p.category_id) ensureCats().then(m => {
                const el = $('detailCat');
                if (el) el.textContent = m[p.category_id] || '—';
            });
            showScreen('screenProdDetail');
        }

        // Bo'limni AI bilan aniqlash — rasm bo'lsa RASMGA qarab, bo'lmasa nomdan.
        window.prodCategorize = async function (id) {
            const btn = $('btnAiCat');
            if (btn) { btn.disabled = true; btn.textContent = '⏳ AI ko\'ryapti...'; }
            const { ok, data } = await apiFetch('/api/product/categorize', {
                method: 'POST', body: JSON.stringify({ id })
            });
            if (btn) { btn.disabled = false; btn.textContent = '🏷 Bo\'limni aniqla (AI)'; }
            if (!ok || !data.ok) { toast('❌ ' + ((data||{}).error || 'Xato')); return; }
            if (!data.category) { toast('🤔 AI aniq javob bermadi'); return; }
            _catsMap = null;                       // yangi bo'lim qo'shilgan bo'lishi mumkin
            const el = $('detailCat');
            if (el) el.textContent = data.category;
            toast((data.image_sent ? '📷 ' : '🏷 ') + data.category);
        };

        // Rasmni qayta brendlash — har safar ASL rasmdan boshlanadi.
        window.prodRebrand = async function (id) {
            const btn = $('btnRebrand');
            if (btn) { btn.disabled = true; btn.textContent = '⏳ Tozalanmoqda...'; }
            const { ok, data } = await apiFetch('/api/product/rebrand', {
                method: 'POST', body: JSON.stringify({ id })
            });
            if (btn) { btn.disabled = false; btn.textContent = '🖼 Rasmni tozalash'; }
            if (!ok || !data.ok) { toast('❌ ' + ((data||{}).error || 'Xato')); return; }
            if (_curProduct && _curProduct.id === id) {
                _curProduct.image_url = data.image_url;
                _curProduct.image_orig_url = data.image_orig_url;
            }
            setImgSrc($('detailImg'), data.image_url + '?t=' + Date.now());
            toast(data.bg_removed ? '✅ Fon kesildi' : '✅ Fon qo\'yildi (kesilmadi)');
        };

        // ====== Mahsulot formasi (qo'shish / tahrirlash) ======
        let _editingId = 0;
        let _pendingImageUrl = '';   // yangi mahsulotga rasm (saqlashdan oldin)

        window.openProductForm = async function (id) {
            _editingId = id || 0;
            _pendingImageUrl = '';
            $('prodFormTitle').textContent = _editingId ? '✏️ Tahrirlash' : '➕ Yangi mahsulot';
            $('pfStatus').textContent = '';
            // Maydonlarni tozalaymiz
            ['pfName','pfSell','pfCost','pfWhs','pfQty','pfBarcode','pfDesc'].forEach(f => $(f).value = '');
            $('pfUnit').value = 'dona';
            $('prodImgPreview').style.display = 'none';
            $('prodImgPreview').src = '';
            $('prodImgPlaceholder').style.display = 'block';

            if (_editingId) {
                // Mavjud mahsulotni yuklaymiz
                const { ok, data } = await apiFetch('/api/product/' + _editingId);
                if (ok && data.ok && data.product) {
                    const p = data.product;
                    $('pfName').value = p.name || '';
                    $('pfDisplayName').value = p.display_name || '';
                    $('pfSell').value = p.sell_price_sum || p.price_sum || '';
                    $('pfCost').value = p.cost_price_sum || '';
                    $('pfWhs').value  = p.wholesale_sum || '';
                    $('pfQty').value  = p.qty || 0;
                    $('pfUnit').value = p.unit || 'dona';
                    $('pfBarcode').value = p.barcode || '';
                    $('pfDesc').value = p.description || '';
                    if (p.image_url) {
                        setImgSrc($('prodImgPreview'), p.image_url);
                        $('prodImgPlaceholder').style.display = 'none';
                    }
                }
            }
            showScreen('screenProdForm');
        };

        window.prodFormBack = function () {
            showScreen(_editingId ? 'screenProdDetail' : 'screenProducts');
        };

        window.saveProduct = async function () {
            const name = $('pfName').value.trim();
            if (!name) { $('pfStatus').textContent = '⚠️ Nomi bo\'sh'; return; }
            const sell = parseFloat($('pfSell').value) || 0;
            if (sell <= 0) { $('pfStatus').textContent = '⚠️ Sotish narxini kiriting'; return; }
            const btn = $('pfSaveBtn');
            btn.disabled = true;
            $('pfStatus').textContent = '⏳ Saqlanmoqda...';
            const payload = {
                id: _editingId,
                name: name,
                display_name: ($('pfDisplayName') || {}).value || '',
                                sell_price_sum: sell,
                cost_price_sum: parseFloat($('pfCost').value) || 0,
                wholesale_sum: parseFloat($('pfWhs').value) || 0,
                qty: parseFloat($('pfQty').value) || 0,
                unit: $('pfUnit').value,
                barcode: $('pfBarcode').value.trim(),
                description: $('pfDesc').value.trim(),
            };
            try {
                const { ok, status, data } = await apiFetch('/api/product/save', {
                    method: 'POST', body: JSON.stringify(payload)
                });
                if (!ok || !data.ok) {
                    $('pfStatus').textContent = '❌ ' + (data.error || ('Xato '+status));
                    btn.disabled = false; return;
                }
                const newId = data.id;
                // Agar yangi mahsulotga rasm tanlangan bo'lsa — endi yuklaymiz
                if (_pendingImageFile) {
                    $('pfStatus').textContent = '⏳ Rasm yuklanmoqda...';
                    const r = await _uploadProductImage(newId, _pendingImageFile);
                    _pendingImageFile = null;
                    if (!r.ok) {
                        $('pfStatus').textContent =
                            '⚠️ Mahsulot saqlandi, lekin rasm yuklanmadi: ' + r.error;
                        btn.disabled = false;
                        return;
                    }
                }
                $('pfStatus').textContent = '✅ Saqlandi!';
                btn.disabled = false;
                if (tg && tg.HapticFeedback) { try{tg.HapticFeedback.notificationOccurred('success');}catch(_){} }
                setTimeout(() => {
                    _editingId = 0;
                    showScreen('screenProducts');
                    loadProducts(true);
                }, 800);
            } catch (err) {
                $('pfStatus').textContent = 'Tarmoq xatosi: ' + (err.message||err);
                btn.disabled = false;
            }
        };

        // ====== Rasm yuklash ======
        let _pendingImageFile = null;
        const prodImgInput = $('prodImgInput');   // galereya/fayl
        const prodImgCam   = $('prodImgCam');     // kamera (capture=environment)

        // mode: 'camera' → kamerani ochadi, aks holda galereya/fayl.
        //
        // Android + Telegram: file-input'dagi `capture="environment"` atributi
        // E'TIBORSIZ qoldiriladi — WebView baribir galereyani ochadi. Shuning
        // uchun kamera uchun o'z oynamizni ochamiz (skanerdagi kabi jonli
        // oqim). Kamera ochilmasa — eski usulga qaytamiz.
        window.pickProductImage = function (mode) {
            if (mode === 'camera') { openPhotoCam(); return; }
            if (prodImgInput) prodImgInput.click();
        };

        // ── O'z kameramiz ────────────────────────────────────────────────
        let _camStream = null, _camVideo = null, _camFacing = 'environment';

        window.openPhotoCam = async function () {
            const ov = $('camOverlay');
            if (!ov || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                if (prodImgCam) prodImgCam.click();     // eski usul
                return;
            }
            ov.classList.add('active');
            const box = $('camView');
            try { box.innerHTML = ''; } catch(_) {}
            try {
                _camStream = await navigator.mediaDevices.getUserMedia({
                    audio: false,
                    video: { facingMode: { ideal: _camFacing },
                             width: { ideal: 1920 }, height: { ideal: 1080 } }
                });
            } catch (e) {
                ov.classList.remove('active');
                // Ruxsat berilmagan yoki kamera yo'q — file-input bilan urinamiz
                if (prodImgCam) prodImgCam.click();
                return;
            }
            const v = document.createElement('video');
            v.setAttribute('playsinline', ''); v.muted = true;
            v.srcObject = _camStream;
            box.appendChild(v);
            _camVideo = v;
            try { await v.play(); } catch(_) {}
        };

        window.closePhotoCam = function () {
            const ov = $('camOverlay');
            if (_camStream) {
                try { _camStream.getTracks().forEach(t => t.stop()); } catch(_) {}
                _camStream = null;
            }
            if (_camVideo) {
                try { _camVideo.pause(); } catch(_) {}
                try { _camVideo.srcObject = null; } catch(_) {}
                _camVideo = null;
            }
            const box = $('camView');
            try { if (box) box.innerHTML = ''; } catch(_) {}
            if (ov) ov.classList.remove('active');
        };

        window.flipPhotoCam = async function () {
            _camFacing = (_camFacing === 'environment') ? 'user' : 'environment';
            closePhotoCam();
            await openPhotoCam();
        };

        window.takePhoto = function () {
            if (!_camVideo || _camVideo.readyState < 2) return;
            const w = _camVideo.videoWidth || 1280;
            const h = _camVideo.videoHeight || 720;
            const cv = document.createElement('canvas');
            cv.width = w; cv.height = h;
            cv.getContext('2d').drawImage(_camVideo, 0, 0, w, h);
            if (tg && tg.HapticFeedback) {
                try { tg.HapticFeedback.impactOccurred('medium'); } catch(_){}
            }
            cv.toBlob(function (blob) {
                if (!blob) { toast('❌ Surat olinmadi'); return; }
                const file = new File([blob], 'photo_' + Date.now() + '.jpg',
                                      { type: 'image/jpeg' });
                closePhotoCam();
                _handleProductImage(file);
            }, 'image/jpeg', 0.92);
        };

        // Fayl (galereyadan yoki kameradan) — bitta yo'l bilan qayta ishlanadi
        function _onProductImageChosen() {
            const input = this;
            const file = input.files && input.files[0];
            input.value = '';        // bir xil faylni qayta tanlash mumkin bo'lsin
            if (file) _handleProductImage(file);
        }

        async function _handleProductImage(file) {
            if (!file) return;
            // Darrov preview
            const reader = new FileReader();
            reader.onload = e => {
                $('prodImgPreview').src = e.target.result;
                $('prodImgPreview').style.display = 'block';
                $('prodImgPlaceholder').style.display = 'none';
            };
            reader.readAsDataURL(file);
            if (_editingId) {
                // Mavjud mahsulot — darrov serverga yuklaymiz
                $('pfStatus').textContent = '⏳ Rasm yuklanmoqda...';
                const r = await _uploadProductImage(_editingId, file);
                if (r.ok) {
                    // Server fonni kesib, brend fonga qo'ygan bo'lishi mumkin —
                    // shuni ko'rsatamiz, admin natijani darrov ko'rsin.
                    setImgSrc($('prodImgPreview'), r.url + '?t=' + Date.now());
                    $('prodImgPlaceholder').style.display = 'none';
                }
                $('pfStatus').textContent = r.ok
                    ? (r.bgRemoved ? '✅ Rasm yuklandi — foni kesildi'
                                   : '✅ Rasm yuklandi')
                    : ('❌ Rasm yuklanmadi: ' + r.error);
            } else {
                // Yangi mahsulot — saqlashdan keyin yuklash uchun saqlab qo'yamiz
                _pendingImageFile = file;
                $('pfStatus').textContent = '🖼 Rasm tanlandi (saqlashda yuklanadi)';
            }
        }

        if (prodImgInput) prodImgInput.addEventListener('change', _onProductImageChosen);
        if (prodImgCam)   prodImgCam.addEventListener('change', _onProductImageChosen);

        // Rasmni kichraytiradi: max 1024px, JPEG 0.78 sifat => ~150-300KB
        function _compressImage(file) {
            return new Promise(resolve => {
                const MAX = 1024;
                const img = new Image();
                const url = URL.createObjectURL(file);
                img.onload = () => {
                    URL.revokeObjectURL(url);
                    let w = img.width, h = img.height;
                    if (w > MAX || h > MAX) {
                        if (w > h) { h = Math.round(h * MAX / w); w = MAX; }
                        else { w = Math.round(w * MAX / h); h = MAX; }
                    }
                    const canvas = document.createElement('canvas');
                    canvas.width = w; canvas.height = h;
                    canvas.getContext('2d').drawImage(img, 0, 0, w, h);
                    canvas.toBlob(blob => resolve(blob || file), 'image/jpeg', 0.78);
                };
                img.onerror = () => { URL.revokeObjectURL(url); resolve(file); };
                img.src = url;
            });
        }

        async function _uploadProductImage(pid, file) {
            try {
                const compressed = await _compressImage(file);
                console.log('🔵 Rasm:', {original: file.size, compressed: compressed.size});
                const fd = new FormData();
                fd.append('id', String(pid));
                fd.append('photo', compressed, 'photo.jpg');
                const initData = (tg && tg.initData) || '';
                const resp = await fetch(API_BASE_URL + '/api/product/image', {
                    method: 'POST',
                    headers: {
                        'ngrok-skip-browser-warning': 'true',
                        'X-Telegram-Init-Data': initData,
                        'X-Session-Token': _sessionToken || ''
                    },
                    body: fd
                });
                console.log('🟢 API javob:', {status: resp.status, statusText: resp.statusText});
                const data = await resp.json().catch(() => ({}));
                console.log('📦 API ma\'lumot:', data);
                if (data && data.ok) return { ok: true, url: data.image_url,
                                              origUrl: data.image_orig_url || '',
                                              bgRemoved: !!data.bg_removed };
                return { ok: false, error: (data && data.error) || ('HTTP ' + resp.status) };
            } catch (e) {
                console.error('❌ Rasm yuklash xatosi:', e);
                return { ok: false, error: (e && e.message) || String(e) };
            }
        }

        // ====== Prixod (tovar qo'shish) ======
        window.prodQty = function (id) {
            const ask = (val) => {
                const delta = parseFloat(val);
                if (!delta || isNaN(delta)) return;
                _doProdQty(id, delta);
            };
            if (tg && tg.showPopup) {
                // Telegram popup'da input yo'q — oddiy prompt ishlatamiz
                const v = prompt("Nechta qo'shamiz? (ayirish uchun -5)");
                if (v !== null) ask(v);
            } else {
                const v = prompt("Nechta qo'shamiz?");
                if (v !== null) ask(v);
            }
        };
        async function _doProdQty(id, delta) {
            const { ok, data } = await apiFetch('/api/product/qty', {
                method: 'POST', body: JSON.stringify({ id, delta })
            });
            if (ok && data.ok) {
                toast(`✅ Yangi qoldiq: ${fmtQty(data.qty)}`);
                // Detalni yangilaymiz
                const r = await apiFetch('/api/product/' + id);
                if (r.ok && r.data.ok) showProductDetail(r.data.product);
            } else {
                toast('❌ ' + (data.error || 'Xato'));
            }
        }

        // ====== O'chirish ======
        window.prodDelete = function (id) {
            const go = async () => {
                const { ok, data } = await apiFetch('/api/product/delete', {
                    method: 'POST', body: JSON.stringify({ id })
                });
                if (ok && data.ok) {
                    toast('🗑 O\'chirildi');
                    showScreen('screenProducts');
                    loadProducts(true);
                } else {
                    toast('❌ ' + (data.error || 'Xato'));
                }
            };
            if (tg && tg.showConfirm) {
                tg.showConfirm("Mahsulotni o'chirasizmi?", (yes) => { if (yes) go(); });
            } else if (confirm("Mahsulotni o'chirasizmi?")) {
                go();
            }
        };

        function escapeHtml(s) {
            return String(s || '').replace(/[&<>"']/g, c => ({
                '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
            }[c]));
        }

        // Qidiruv (debounce)
        if (productSearch) {
            productSearch.addEventListener('input', () => {
                productSearchClear.style.display =
                    productSearch.value ? 'block' : 'none';
                clearTimeout(_searchTimer);
                _searchTimer = setTimeout(() => {
                    _prodQuery = productSearch.value.trim();
                    loadProducts(true);
                }, 450);
            });
            productSearchClear.addEventListener('click', () => {
                productSearch.value = '';
                productSearchClear.style.display = 'none';
                _prodQuery = '';
                loadProducts(true);
            });
        }

        // ================================================================
        // DASHBOARD QUICK STATS (admin panel yuqori qismi)
        // ================================================================
        async function loadDashboardStats() {
            // Parallel: stats + clients + orders
            const [sr, cr, or_] = await Promise.allSettled([
                apiFetch('/api/stats'),
                apiFetch('/api/clients'),
                apiFetch('/api/orders'),
            ]);
            const ds = $('dashClients'), do_ = $('dashOrders'), dr = $('dashRevenue');
            if (cr.status === 'fulfilled' && cr.value.ok && cr.value.data.ok) {
                if (ds) ds.textContent = cr.value.data.total || 0;
            }
            if (or_.status === 'fulfilled' && or_.value.ok && or_.value.data.ok) {
                if (do_) do_.textContent = or_.value.data.total || 0;
            }
            if (sr.status === 'fulfilled' && sr.value.ok && sr.value.data.ok) {
                const m = sr.value.data.month || {};
                const rev = m.revenue_sum || 0;
                if (dr) dr.textContent = rev >= 1_000_000
                    ? (rev / 1_000_000).toFixed(1) + 'M'
                    : fmtSum(rev);
            }
        }

        // ================================================================
        // STATISTIKA (admin)
        // ================================================================
        window.openStats = async function () {
            showScreen('screenStats');
            const body = $('statsBody');
            body.innerHTML = '<div class="list-status">Yuklanmoqda...</div>';
            const { ok, status, data } = await apiFetch('/api/stats');
            if (!ok || !data.ok) {
                body.innerHTML = '<div class="list-status">' +
                    (status === 403 ? 'Sizda statistika ruxsati yo\'q.' :
                     status === 401 ? '🔒 Qaytadan kiring.' :
                     ('Xato: ' + (data.error || status))) + '</div>';
                return;
            }
            const t = data.today, m = data.month;
            const row = (lbl, val, cls) =>
                `<div class="stat-row"><span class="lbl">${lbl}</span>` +
                `<span class="val ${cls||''}">${val}</span></div>`;
            let topHtml = '';
            (data.top_products || []).forEach((p, i) => {
                topHtml += `<div class="toprow"><span>${i+1}. ${escapeHtml(p.name)}</span>` +
                    `<span>${fmtQty(p.qty)} × · ${fmtSum(p.revenue_sum)} so'm</span></div>`;
            });
            if (!topHtml) topHtml = '<div class="list-status">Sotuv yo\'q</div>';
            // To'lov turlari bo'yicha qatorlar (faqat nol bo'lmaganlari)
            const payRows = (b) => {
                let h = '';
                if (b.cash_sum > 0)   h += row('&nbsp;&nbsp;💵 Naqd', fmtSum(b.cash_sum));
                if (b.card_sum > 0)   h += row('&nbsp;&nbsp;💳 Karta', fmtSum(b.card_sum));
                if (b.click_sum > 0)  h += row('&nbsp;&nbsp;📲 Click', fmtSum(b.click_sum));
                if (b.nasiya_sum > 0) h += row('&nbsp;&nbsp;🤝 Nasiya', fmtSum(b.nasiya_sum));
                return h;
            };
            const extraRows = (b) => {
                let h = '';
                if (b.return_count > 0)
                    h += row('↩️ Qaytarishlar', b.return_count + ' ta · ' + fmtSum(b.return_sum), 'val-red');
                if (b.expense_count > 0)
                    h += row('🔻 Chinor rasxodi', b.expense_count + ' ta · ' + fmtSum(b.expense_sum), 'val-red');
                return h;
            };
            body.innerHTML = `
                <div class="stat-block">
                    <h3>📅 Bugun</h3>
                    ${row('🧾 Sotuvlar', t.sale_count + ' ta')}
                    ${row('💰 Tushum', fmtSum(t.revenue_sum) + " so'm", 'val-green')}
                    ${payRows(t)}
                    ${extraRows(t)}
                    ${row('✅ Foyda', fmtSum(t.profit_sum) + " so'm", 'val-green')}
                </div>
                <div class="stat-block">
                    <h3>📆 Bu oy (${data.month_label})</h3>
                    ${row('🧾 Sotuvlar', m.sale_count + ' ta')}
                    ${row('🚚 Buyurtmalar', m.order_count + ' ta')}
                    ${row('💰 Tushum', fmtSum(m.revenue_sum) + " so'm", 'val-green')}
                    ${payRows(m)}
                    ${row('📦 Tannarx', fmtSum(m.cost_sum) + " so'm")}
                    ${extraRows(m)}
                    ${row('✅ Sof foyda', fmtSum(m.profit_sum) + " so'm", 'val-green')}
                </div>
                <div class="stat-block">
                    <h3>🏆 Top mahsulotlar (oy)</h3>
                    ${topHtml}
                </div>`;
        };

        // ================================================================
        // CHEKLAR (admin) — barcha manbalardan (kassa, bot, mini app)
        // ================================================================
        let _rcptDays = 0;   // 0 = bugun, 7/30 = oxirgi N kun

        function _dateNDaysAgo(n) {
            const d = new Date(Date.now() - n * 86400000 + 5 * 3600000);
            return d.toISOString().slice(0, 10);
        }
        const PAY_LABELS = {
            cash: '💵 Naqd', card: '💳 Karta', click: '📲 Click',
            split: '🔀 Aralash', qarz: '🤝 Qarz', rasxod: '🔻 Rasxod',
            qaytarish: '↩️ Qaytarish',
        };

        window.setRcptFilter = function (btn) {
            document.querySelectorAll('#rcptFilters .rcpt-fbtn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            _rcptDays = parseInt(btn.dataset.days, 10) || 0;
            loadReceipts();
        };

        window.openReceipts = function () {
            showScreen('screenReceipts');
            loadReceipts();
        };

        let _rcptCache = [];
        async function loadReceipts() {
            const body = $('receiptsBody'), sum = $('receiptsSummary');
            body.innerHTML = '<div class="list-status">Yuklanmoqda...</div>';
            sum.innerHTML = '';
            const since = _dateNDaysAgo(_rcptDays) + ' 00:00:00';
            const { ok, status, data } = await apiFetch(
                '/api/sync/recent-sales?since=' + encodeURIComponent(since) + '&limit=500');
            if (!ok || !data.ok) {
                body.innerHTML = '<div class="list-status">' +
                    (status === 401 ? '🔒 Qaytadan kiring.' : ('Xato: ' + (data.error || status))) + '</div>';
                return;
            }
            _rcptCache = data.sales || [];
            if (!_rcptCache.length) {
                body.innerHTML = '<div class="list-status">Bu davrda chek yo\'q</div>';
                return;
            }
            // Yig'indi: sotuvlar (rasxod/qaytarishsiz netto emas — alohida ko'rsatamiz)
            let saleTotal = 0, saleCount = 0, retTotal = 0, retCount = 0, expTotal = 0, expCount = 0;
            _rcptCache.forEach(s => {
                const v = Number(s.total_sum) || 0;
                if (s.is_return) { retCount++; retTotal += Math.abs(v); }
                else if (s.is_internal) { expCount++; expTotal += v; }
                else { saleCount++; saleTotal += v; }
            });
            sum.innerHTML = `<div class="rcpt-sum">
                <span>🧾 ${saleCount} ta — <b>${fmtSum(saleTotal)}</b> so'm</span>
                ${retCount ? `<span class="rs-red">↩️ ${retCount} ta — ${fmtSum(retTotal)}</span>` : ''}
                ${expCount ? `<span class="rs-red">🔻 ${expCount} ta — ${fmtSum(expTotal)}</span>` : ''}
            </div>`;
            let html = '';
            let lastDay = '';
            _rcptCache.forEach((s, i) => {
                const day = (s.created_at || '').slice(0, 10);
                if (day !== lastDay) {
                    lastDay = day;
                    html += `<div class="rcpt-day">${day.split('-').reverse().join('.')}</div>`;
                }
                const time = (s.created_at || '').slice(11, 16);
                const v = Number(s.total_sum) || 0;
                const neg = s.is_return || v < 0;
                html += `<div class="rcpt-row" onclick="openReceiptDetail(${i})">
                    <div class="rcpt-l">
                        <div class="rcpt-no">№ ${escapeHtml(s.receipt_no || ('#' + s.id))} · ${time}</div>
                        <div class="rcpt-meta">${PAY_LABELS[s.payment] || s.payment}${s.client_name ? ' · ' + escapeHtml(s.client_name) : ''}${s.cashier_name ? ' · ' + escapeHtml(s.cashier_name) : ''}</div>
                    </div>
                    <div class="rcpt-amt ${neg ? 'rs-red' : ''}">${fmtSum(v)}</div>
                </div>`;
            });
            body.innerHTML = html;
        }

        window.openReceiptDetail = function (i) {
            const s = _rcptCache[i];
            if (!s) return;
            const items = (s.items || []).map(it =>
                `<div class="stat-row"><span class="lbl">${escapeHtml(it.name)} × ${fmtQty(it.qty)}</span>` +
                `<span class="val">${fmtSum((Number(it.price_sum) || 0) * (Number(it.qty) || 0))}</span></div>`).join('');
            const payParts = [];
            if (Number(s.paid_cash) > 0) payParts.push(`💵 Naqd: ${fmtSum(s.paid_cash)}`);
            if (Number(s.paid_card) > 0) payParts.push(`💳 Karta: ${fmtSum(s.paid_card)}`);
            if (Number(s.paid_other) > 0) payParts.push(`📲 Click: ${fmtSum(s.paid_other)}`);
            if (Number(s.debt_part) > 0) payParts.push(`🤝 Qarzga: ${fmtSum(s.debt_part)}`);
            const ov = document.createElement('div');
            ov.className = 'cl-modal-overlay';
            ov.onclick = (e) => { if (e.target === ov) ov.remove(); };
            ov.innerHTML = `<div class="cl-modal" style="max-height:80vh;overflow:auto">
                <h3 style="margin:0 0 4px">Chek № ${escapeHtml(s.receipt_no || ('#' + s.id))}</h3>
                <div style="font-size:12px;color:var(--muted);margin-bottom:10px">
                    ${escapeHtml(s.created_at || '')}${s.cashier_name ? ' · ' + escapeHtml(s.cashier_name) : ''}${s.source ? ' · ' + escapeHtml(s.source) : ''}
                </div>
                <div class="stat-block" style="margin:0 0 10px">${items || '<div class="list-status">Tovar yo\'q</div>'}</div>
                ${Number(s.discount_sum) > 0 ? `<div class="stat-row"><span class="lbl">💸 Chegirma</span><span class="val">${fmtSum(s.discount_sum)}</span></div>` : ''}
                <div class="stat-row"><span class="lbl"><b>JAMI</b></span><span class="val"><b>${fmtSum(s.total_sum)} so'm</b></span></div>
                <div style="font-size:13px;margin-top:8px">${PAY_LABELS[s.payment] || s.payment}${payParts.length ? ' — ' + payParts.join(' · ') : ''}</div>
                ${s.orig_receipt_no ? `<div style="font-size:12px;color:var(--muted);margin-top:4px">Asl chek: № ${escapeHtml(s.orig_receipt_no)}</div>` : ''}
                <button style="width:100%;padding:13px;margin-top:14px;background:var(--primary);color:#fff;border:none;border-radius:14px;font-size:15px;font-weight:700;cursor:pointer" onclick="this.closest('.cl-modal-overlay').remove()">Yopish</button>
            </div>`;
            document.body.appendChild(ov);
        };

        // ================================================================
        // KASSA HARAKATI (admin) — naqd kirim / rasxod (cash_movements)
        // ================================================================
        let _cashDays = 0;

        window.setCashFilter = function (btn) {
            document.querySelectorAll('#cashFilters .rcpt-fbtn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            _cashDays = parseInt(btn.dataset.days, 10) || 0;
            loadCashMoves();
        };

        window.openCashMoves = function () {
            showScreen('screenCash');
            loadCashMoves();
        };

        async function loadCashMoves() {
            const body = $('cashBody'), sum = $('cashSummary');
            body.innerHTML = '<div class="list-status">Yuklanmoqda...</div>';
            sum.innerHTML = '';
            const from = _dateNDaysAgo(_cashDays);
            const { ok, status, data } = await apiFetch(
                '/api/cash-movements?from=' + from + '&limit=300');
            if (!ok || !data.ok) {
                body.innerHTML = '<div class="list-status">' +
                    (status === 401 ? '🔒 Qaytadan kiring.' : ('Xato: ' + (data.error || status))) + '</div>';
                return;
            }
            const items = data.items || [];
            const sm = data.summary || {};
            sum.innerHTML = `<div class="rcpt-sum">
                <span class="rs-green">＋ Kirim: <b>${fmtSum(sm.in_total || 0)}</b></span>
                <span class="rs-red">－ Chiqim: <b>${fmtSum(sm.out_total || 0)}</b></span>
            </div>`;
            if (!items.length) {
                body.innerHTML = '<div class="list-status">Bu davrda harakat yo\'q.<br>' +
                    '<small>Kassadagi kirim/chiqim serverga kassa 1.7.5+ versiyasidan sinxron bo\'ladi.</small></div>';
                return;
            }
            let html = '', lastDay = '';
            items.forEach(m => {
                const day = (m.created_at || '').slice(0, 10);
                if (day !== lastDay) {
                    lastDay = day;
                    html += `<div class="rcpt-day">${day.split('-').reverse().join('.')}</div>`;
                }
                const time = (m.created_at || '').slice(11, 16);
                const out = m.direction === 'out';
                const meta = [m.category, m.note, m.recipient, m.cashier_name, m.source]
                    .filter(Boolean).map(escapeHtml).join(' · ');
                html += `<div class="rcpt-row">
                    <div class="rcpt-l">
                        <div class="rcpt-no">${out ? '－ Chiqim' : '＋ Kirim'} · ${time}</div>
                        <div class="rcpt-meta">${meta || '—'}</div>
                    </div>
                    <div class="rcpt-amt ${out ? 'rs-red' : 'rs-green'}">${out ? '−' : '+'}${fmtSum(m.amount)}</div>
                </div>`;
            });
            body.innerHTML = html;
        }

        // ================================================================
        // MENING HISOBIM (mijoz)
        // ================================================================
        // ══════════════════════════════════════════════════
        //  CLIENT PANEL
        // ══════════════════════════════════════════════════
        let _clCatalogPage = 0;
        let _clCatalogQuery = '';

        function _fmt_sum_cl(v) {
            return Number(v||0).toLocaleString('uz-UZ') + " so'm";
        }

        function _cl_img(p) {
            const url = p.image_url ? API_BASE_URL + p.image_url : '';
            return url
                ? `<img class="cl-card-img" src="${url}" loading="lazy" onerror="this.outerHTML='<div class=cl-card-img-ph>📦</div>'">`
                : `<div class="cl-card-img-ph">📦</div>`;
        }
        function _cl_img_row(p) {
            const url = p.image_url ? API_BASE_URL + p.image_url : '';
            return url
                ? `<img class="cl-prod-thumb" src="${url}" loading="lazy" onerror="this.outerHTML='<div class=cl-prod-thumb-ph>📦</div>'">`
                : `<div class="cl-prod-thumb-ph">📦</div>`;
        }

        window.clientTab = function(btn, tab) {
            document.querySelectorAll('.cl-tab').forEach(t => t.classList.remove('active'));
            if (btn) btn.classList.add('active');
            ['home','catalog','ai','account'].forEach(t => {
                const el = $('clTab' + t.charAt(0).toUpperCase() + t.slice(1));
                if (el) el.style.display = (t === tab) ? '' : 'none';
            });
            if (tab === 'catalog') { loadClientCategories(); loadClientCatalog(); }
            if (tab === 'home') { _clRenderRecent(); _clLoadFeed(); }
            if (tab === 'account') loadClientAccount();
        };

        window.openClientSearch = function() {
            clientTab(document.querySelectorAll('.cl-tab')[1], 'catalog');
            setTimeout(() => { const el = $('clCatalogSearch'); if(el) el.focus(); }, 100);
        };

        async function loadClientTop() {
            const { ok, data } = await apiFetch('/api/client/top');
            if (!ok) return;
            const topEl = $('clTopList');
            const newEl = $('clNewList');
            _clOrdersOn = data.orders_enabled !== false;
            if (data.shop) _clShop = data.shop;
            (data.top||[]).concat(data.new||[]).forEach(_clRemember);
            // Ma'lumot bo'lmasa bo'lim butunlay yashiriladi — "ko'p
            // sotilayotgan" deb tasodifiy tovar ko'rsatish yolg'on bo'ladi.
            const topSec = $('clTopSec'), newSec = $('clNewSec');
            if (topSec) topSec.style.display = (data.top||[]).length ? '' : 'none';
            if (newSec) newSec.style.display = (data.new||[]).length ? '' : 'none';
            if (topEl) topEl.innerHTML = (data.top||[]).map(p => _clCardHtml(p, true)).join('');
            if (newEl) newEl.innerHTML = (data.new||[]).map(p => _clCardHtml(p, false)).join('');
            _clRenderRecent();
            _clRenderBar();
        }

        // ── Kategoriya tugmalari (mijoz katalogi) ────────────────────────
        let _clCat = 0;          // 0 = hammasi
        let _clScanOpen = false; // skanerdan keyin bitta tovar topilsa ochiladi

        async function loadClientCategories() {
            const box = $('clCatCips');
            if (!box) return;
            const { ok, data } = await apiFetch('/api/categories');
            if (!ok || !data.ok) { box.innerHTML = ''; return; }
            const cats = data.categories || [];
            if (!cats.length) { box.innerHTML = ''; return; }
            let html = `<button class="cl-cat-chip${_clCat ? '' : ' active'}"
                          onclick="pickClientCat(0)">Hammasi</button>`;
            cats.forEach(c => {
                html += `<button class="cl-cat-chip${_clCat === c.id ? ' active' : ''}"
                           onclick="pickClientCat(${c.id})">${escapeHtml(c.name)}` +
                        `<span class="n">${c.count}</span></button>`;
            });
            box.innerHTML = html;
        }

        window.pickClientCat = function (id) {
            _clCat = id;
            loadClientCategories();
            loadClientCatalog();
        };

        window.loadClientCatalog = function() {
            _clCatalogPage = 0;
            _clCatalogQuery = ($('clCatalogSearch')||{value:''}).value.trim();
            const cl = $('clCatalogClear');
            if (cl) cl.style.display = _clCatalogQuery ? '' : 'none';
            _doLoadClientCatalog(true);
        };
        window.loadClientCatalogMore = function() { _doLoadClientCatalog(false); };

        async function _doLoadClientCatalog(reset) {
            if (reset) { _clCatalogPage = 0; $('clCatalogList').innerHTML = '<div class="cl-loading">Yuklanmoqda...</div>'; }
            const { ok, data } = await apiFetch(`/api/products?page=${_clCatalogPage}`
                + `&q=${encodeURIComponent(_clCatalogQuery)}`
                + (_clCat ? `&category=${_clCat}` : ''));
            const el = $('clCatalogList');
            const more = $('clCatalogMore');
            if (!ok || !data.ok) { if(el) el.innerHTML = '<div class="cl-loading">Xato</div>'; return; }
            const items = data.items || data.products || [];
            items.forEach(_clRemember);
            const html = items.map(p => `<div class="cl-prod-row" onclick="clOpenProduct(${p.id})">
                ${_cl_img_row(p)}
                <div class="cl-prod-info">
                    <div class="cl-prod-name">${escapeHtml(p.name)}</div>
                    ${p.description ? `<div class="cl-prod-desc">${escapeHtml(p.description)}</div>` : ''}
                    <div class="cl-prod-price">${_fmt_sum_cl(_clPrice(p))}</div>
                    ${_clStockTag(p)}
                </div>
                <div class="cl-row-buy" data-pid="${p.id}" onclick="event.stopPropagation()">${_clBuyCtl(p)}</div>
            </div>`).join('');
            if (reset) el.innerHTML = html || '<div class="cl-loading">Mahsulot topilmadi</div>';
            else el.innerHTML += html;
            _clCatalogPage++;
            if (more) more.style.display = (data.has_more) ? '' : 'none';

            // Skanerdan keyin aynan bitta tovar topilsa — kartochkasini
            // darrov ochamiz. Mijoz kodni o'qitganda ro'yxatni emas, o'sha
            // tovarning narxi va ma'lumotini ko'rmoqchi bo'ladi.
            if (_clScanOpen) {
                _clScanOpen = false;
                if (items.length === 1) clOpenProduct(items[0].id);
            }
        }

        // Rasmni to'liq ekranda ochish (kartochkadagi rasm kichik — yorliqdagi
        // yozuvni o'qish uchun kattalashtirish kerak bo'ladi).
        window.clZoomImg = function(src) {
            if (!src) return;
            const z = document.createElement('div');
            z.className = 'cl-zoom';
            z.innerHTML = `<img src="${src}">`;
            z.addEventListener('click', () => z.remove());
            document.body.appendChild(z);
        };

        // Mahsulot oynasi BITTA: o'xshash tovar bosilsa shu oynaning o'zida
        // ochiladi (Instagram'dagi kabi ichkariga kirib boriladi), "←" esa
        // oldingisiga qaytaradi.
        const _clStack = [];

        window.clOpenProduct = async function(pid, fromStack) {
            const { ok, data } = await apiFetch(`/api/product/${pid}`);
            if (!ok || !data.ok) return;
            const prevSheet = $('clProdSheet');
            if (!fromStack) {
                if (prevSheet && prevSheet.dataset.pid) _clStack.push(+prevSheet.dataset.pid);
                else _clStack.length = 0;
            }
            const p = data.product;
            if (_clProd[p.id] && _clProd[p.id].sold) p.sold = _clProd[p.id].sold;
            _clRemember(p);
            _clTrackView(p.id);
            const url = p.image_url ? API_BASE_URL + p.image_url : '';
            // Asl rasm — foni kesilmagani. Ikkalasi bor bo'lsa o'tkazgich chiqadi:
            // mijoz tovar haqiqatda qanday ekanini ko'ra olsin.
            const origUrl = (p.image_orig_url && p.image_orig_url !== p.image_url)
                ? API_BASE_URL + p.image_orig_url : '';
            document.querySelectorAll('.cl-modal-overlay').forEach(o => { if (o.id !== 'clProdSheet') o.remove(); });
            let overlay = prevSheet;
            if (!overlay) {
                overlay = document.createElement('div');
                overlay.className = 'cl-modal-overlay';
                overlay.id = 'clProdSheet';
                overlay.addEventListener('click', e => { if (e.target === overlay) clCloseProduct(); });
                document.body.appendChild(overlay);
            }
            overlay.dataset.pid = p.id;
            overlay.innerHTML = `<div class="cl-modal" id="clProdModal" style="position:relative">
                ${_clStack.length ? `<button class="cl-modal-back" onclick="clProdBack()">←</button>` : ''}
                <button class="cl-modal-close" onclick="clCloseProduct()">✕</button>
                ${url ? `<img class="cl-modal-img" id="clModalImg" src="${url}"
                              onclick="clZoomImg(this.src)">`
                      : '<div style="height:60px"></div>'}
                ${(url && origUrl) ? `<div class="cl-img-tabs">
                    <button class="cl-img-tab active" onclick="clSwitchImg(this,'${url}')">🖼 Katalog rasmi</button>
                    <button class="cl-img-tab" onclick="clSwitchImg(this,'${origUrl}')">📷 Asl rasm</button>
                </div>` : ''}
                ${p.category_name ? `<div style="display:inline-block;background:var(--primary-light);color:var(--primary-dark);font-size:11px;font-weight:700;padding:4px 10px;border-radius:20px;margin-bottom:8px">🗂 ${escapeHtml(p.category_name)}</div>` : ''}
                ${(p.usage_tags && p.usage_tags.length) ? `<div class="cl-usage">
                    <span class="cl-usage-lbl">🔧 Qayerda ishlatiladi</span>
                    <span>${p.usage_tags.map(t => `<b>${escapeHtml(t)}</b>`).join(' · ')}</span>
                </div>` : ''}
                <div class="cl-modal-name">${escapeHtml(p.name)}</div>
                <div class="cl-modal-price">${_fmt_sum_cl(_clPrice(p))}<span class="cl-modal-unit"> / ${escapeHtml(p.unit||'dona')}</span></div>
                ${p.sold > 0 ? `<div class="cl-sold-line">🔥 ${_clNum(p.sold)} ${escapeHtml(p.unit||'dona')} sotilgan — xaridorlar tanlovi</div>` : ''}
                ${p.description ? `<div class="cl-modal-desc">${p.description}</div>` : ''}
                ${p.usage_info ? `<details class="cl-usage-info">
                    <summary>📖 Qanday ishlatiladi / Как использовать</summary>
                    <div>${escapeHtml(p.usage_info)}</div>
                </details>` : ''}
                ${_clStockTag(p, true)}
                <div class="cl-buybox" data-pid="${p.id}">${_clModalBuy(p)}</div>
                <div class="cl-trust">
                    <span>💵 To'lov — tovarni olganingizda</span>
                    <span>📞 Savol bo'lsa: <a href="tel:${(_clShop.phone||'').replace(/\s/g,'')}">${escapeHtml(_clShop.phone||'')}</a></span>
                </div>
                <div class="cl-feed-head">🔎 O'xshash tovarlar</div>
                <div class="cl-grid" id="clSimGrid"></div>
                <div class="cl-feed-more" id="clSimMore">Yuklanmoqda...</div>
            </div>`;
            const modal = $('clProdModal');
            modal.scrollTop = 0;
            // O'xshashlar tugagach server davomini "sizga yoqishi mumkin"
            // tovarlar bilan beradi — lenta pastga cheksiz davom etadi.
            _clInfinite($('clSimGrid'), $('clSimMore'), modal,
                page => `/api/client/similar?pid=${p.id}&page=${page}`,
                d => {
                    if (d.page === 0 && !d.similar_count) {
                        const h = modal.querySelector('.cl-feed-head');
                        if (h) h.textContent = '✨ Sizga yoqishi mumkin';
                    }
                });
        };

        window.clProdBack = function () {
            const prev = _clStack.pop();
            if (prev) clOpenProduct(prev, true);
        };
        window.clCloseProduct = function () {
            _clStack.length = 0;
            const s = $('clProdSheet');
            if (s) s.remove();
            const home = $('clTabHome');
            if (home && home.style.display !== 'none') { _clRenderRecent(); _clLoadFeed(); }
        };

        window.clSwitchImg = function(btn, src) {
            const img = $('clModalImg');
            if (img) img.src = src;
            btn.parentNode.querySelectorAll('.cl-img-tab')
               .forEach(b => b.classList.toggle('active', b === btn));
        };

        // ── Mijoz savati va buyurtma ─────────────────────────────────────
        // Mijoz katalogdan tovarni savatga soladi va Mini App ichida buyurtma
        // beradi (POST /api/client/order). Savat brauzerda saqlanadi —
        // Mini App yopilib qayta ochilsa ham yo'qolmaydi. Narx va qoldiq
        // buyurtma paytida serverda qayta tekshiriladi.
        //
        // Jalb qilish faqat HAQIQIY ma'lumot bilan: real qoldiq ("Faqat 3 ta
        // qoldi"), real sotilgan soni, bir bosishda qayta buyurtma. Soxta
        // taymer yoki o'ylab topilgan chegirma yo'q — ishonch yo'qolsa,
        // mijoz qaytib kelmaydi.
        let _clOrdersOn = true;
        let _clShop = {};
        const _clProd = {};                 // id → oxirgi ko'rilgan tovar
        const _CL_CART_KEY = 'chinor_cl_cart_v1';
        const _CL_FORM_KEY = 'chinor_cl_form_v1';
        const _CL_LOW = 3;                  // shundan kam qolsa ogohlantiramiz (5 da deyarli hamma tovar "oz qoldi" bo'lardi)

        function _clLoad(key, def) {
            try { const v = JSON.parse(localStorage.getItem(key) || 'null'); return v || def; }
            catch (_) { return def; }
        }
        function _clSave(key, v) {
            try { localStorage.setItem(key, JSON.stringify(v)); } catch (_) {}
        }
        let _clCart = _clLoad(_CL_CART_KEY, {});   // id → {id,name,price,unit,image_url,stock,qty}
        const _clForm = Object.assign(
            { delivery: 'delivery', address: '', phone: '', payment: 'cash', comment: '' },
            _clLoad(_CL_FORM_KEY, {}));

        function _clPrice(p) {
            const v = p.price_sum != null ? p.price_sum : (p.price != null ? p.price : p.sell_price);
            return Number(v) || 0;
        }
        function _clNum(v) { return Number(v || 0).toLocaleString('uz-UZ', { maximumFractionDigits: 2 }); }
        function _clStep(p) { return /^(kg|l|m)$/.test(p.unit || '') ? 0.5 : 1; }

        function _clRemember(p) {
            if (!p || !p.id) return;
            const prev = _clProd[p.id] || {};
            _clProd[p.id] = Object.assign({}, prev, p, { sold: p.sold != null ? p.sold : prev.sold });
            // Savatdagi nusxani ham yangilaymiz (narx/qoldiq o'zgargan bo'lishi mumkin)
            const c = _clCart[p.id];
            if (c) { c.price = _clPrice(p); c.stock = Number(p.qty) || 0; c.name = p.name; }
        }

        function _clStockTag(p, big) {
            const q = Number(p.qty) || 0;
            const cls = big ? 'cl-stock big' : 'cl-stock';
            if (q <= 0) return `<div class="${cls} out">❌ Hozir qolmagan</div>`;
            if (q <= _CL_LOW) return `<div class="${cls} low">⚡ Faqat ${_clNum(q)} ${escapeHtml(p.unit||'dona')} qoldi</div>`;
            return big ? `<div class="${cls} ok">✅ Omborda bor</div>` : '';
        }

        // Kichik boshqaruv: "+" yoki "− 2 +" (kartochka va qatorlarda)
        function _clBuyCtl(p) {
            if (!_clOrdersOn || (Number(p.qty) || 0) <= 0) return '';
            const c = _clCart[p.id];
            if (!c) return `<button class="cl-add" onclick="clAdd(${p.id})" aria-label="Savatga">＋</button>`;
            return `<div class="cl-stepper">
                <button onclick="clInc(${p.id},-1)">−</button>
                <span>${_clNum(c.qty)}</span>
                <button onclick="clInc(${p.id},1)">＋</button>
            </div>`;
        }

        function _clCardHtml(p, showSold) {
            return `<div class="cl-card" onclick="clOpenProduct(${p.id})">
                <div class="cl-card-media">
                    ${_cl_img(p)}
                    ${showSold && p.sold > 0 ? `<span class="cl-badge">🔥 ${_clNum(p.sold)} sotilgan</span>` : ''}
                    ${(Number(p.qty)||0) > 0 && (Number(p.qty)||0) <= _CL_LOW ? `<span class="cl-badge low">Oz qoldi</span>` : ''}
                </div>
                <div class="cl-card-body">
                    <div class="cl-card-name">${escapeHtml(p.name)}</div>
                    <div class="cl-card-foot">
                        <div class="cl-card-price">${_fmt_sum_cl(_clPrice(p))}</div>
                        <div class="cl-card-buy" data-pid="${p.id}" onclick="event.stopPropagation()">${_clBuyCtl(p)}</div>
                    </div>
                </div></div>`;
        }

        // Mahsulot oynasidagi katta tugma
        function _clModalBuy(p) {
            const tel = (_clShop.phone || '').replace(/\s/g, '');
            if (!_clOrdersOn) {
                return `<a class="cl-cta" href="tel:${tel}">📞 Buyurtma uchun qo'ng'iroq qiling</a>`;
            }
            if ((Number(p.qty) || 0) <= 0) {
                return `<a class="cl-cta ghost" href="tel:${tel}">📞 Qachon kelishini so'rash</a>`;
            }
            const c = _clCart[p.id];
            if (!c) {
                return `<button class="cl-cta" onclick="clAdd(${p.id})">🛒 Savatga qo'shish · ${_fmt_sum_cl(_clPrice(p))}</button>`;
            }
            return `<div class="cl-modal-buy">
                <div class="cl-stepper big">
                    <button onclick="clInc(${p.id},-1)">−</button>
                    <span>${_clNum(c.qty)} ${escapeHtml(p.unit||'dona')}</span>
                    <button onclick="clInc(${p.id},1)">＋</button>
                </div>
                <button class="cl-cta" onclick="clOpenCart()">Rasmiylashtirish · ${_fmt_sum_cl(c.qty * c.price)} →</button>
            </div>`;
        }

        function _clHaptic(kind) {
            try {
                if (!tg || !tg.HapticFeedback) return;
                if (kind === 'ok') tg.HapticFeedback.notificationOccurred('success');
                else if (kind === 'err') tg.HapticFeedback.notificationOccurred('error');
                else tg.HapticFeedback.impactOccurred('light');
            } catch (_) {}
        }

        function _clToast(msg) {
            let t = $('clToast');
            if (!t) { t = document.createElement('div'); t.id = 'clToast'; t.className = 'cl-toast'; document.body.appendChild(t); }
            t.textContent = msg;
            t.classList.add('show');
            clearTimeout(t._h);
            t._h = setTimeout(() => t.classList.remove('show'), 2200);
        }

        function _clSet(p, qty) {
            const stock = Number(p.qty) || 0;
            qty = Math.max(0, Math.min(Number(qty) || 0, stock));
            if (qty <= 0) delete _clCart[p.id];
            else _clCart[p.id] = { id: p.id, name: p.name, price: _clPrice(p), unit: p.unit || 'dona',
                                   image_url: p.image_url || '', stock, qty };
            _clSave(_CL_CART_KEY, _clCart);
            _clRefresh(p.id);
        }

        // Savat o'zgarganda — sahifadagi barcha shu tovar tugmalarini yangilaymiz
        function _clRefresh(pid) {
            const p = _clProd[pid] || _clCart[pid];
            if (p) {
                document.querySelectorAll(`.cl-card-buy[data-pid="${pid}"], .cl-row-buy[data-pid="${pid}"]`)
                    .forEach(el => el.innerHTML = _clBuyCtl(p));
                document.querySelectorAll(`.cl-buybox[data-pid="${pid}"]`)
                    .forEach(el => el.innerHTML = _clModalBuy(p));
            }
            _clRenderBar(true);
            if ($('clCartSheet')) _clRenderCart();
        }

        window.clAdd = function (pid) {
            const p = _clProd[pid];
            if (!p) return;
            if (_clCart[pid]) return clInc(pid, 1);
            _clSet(p, _clStep(p) === 1 ? 1 : Math.min(1, Number(p.qty)));
            _clTrackView(pid);               // savatga solish — eng kuchli qiziqish
            _clHaptic();
            _clToast(`✅ ${p.name} savatga qo'shildi`);
        };

        window.clInc = function (pid, dir) {
            const p = _clProd[pid] || _clCart[pid] && Object.assign({ qty: _clCart[pid].stock }, _clCart[pid]);
            const c = _clCart[pid];
            if (!p || !c) return;
            const next = +(c.qty + dir * _clStep(p)).toFixed(2);
            if (dir > 0 && next > (Number(p.qty) || c.stock)) {
                _clHaptic('err');
                _clToast(`Omborda faqat ${_clNum(p.qty || c.stock)} ${p.unit || 'dona'} bor`);
                return;
            }
            _clHaptic();
            _clSet(Object.assign({}, p, { qty: Number(p.qty) || c.stock }), next);
        };

        function _clTotals() {
            const items = Object.values(_clCart);
            return {
                items,
                count: items.length,
                sum: items.reduce((a, c) => a + c.qty * c.price, 0),
            };
        }

        function _clRenderBar(bump) {
            const bar = $('clCartBar');
            if (!bar) return;
            const t = _clTotals();
            const scr = $('screenClient');
            if (!t.count || !_clOrdersOn) {
                bar.style.display = 'none';
                if (scr) scr.classList.remove('has-cartbar');
                return;
            }
            bar.style.display = '';
            if (scr) scr.classList.add('has-cartbar');
            $('clCartBarCount').textContent = `🛒 ${t.count} xil tovar`;
            $('clCartBarTotal').textContent = _fmt_sum_cl(t.sum);
            if (bump) { bar.classList.remove('bump'); void bar.offsetWidth; bar.classList.add('bump'); }
        }

        window.clOpenCart = function () {
            document.querySelectorAll('.cl-modal-overlay').forEach(o => o.remove());
            const ov = document.createElement('div');
            ov.className = 'cl-modal-overlay';
            ov.id = 'clCartSheet';
            ov.innerHTML = `<div class="cl-modal" id="clCartBody" style="position:relative"></div>`;
            ov.addEventListener('click', e => { if (e.target === ov) ov.remove(); });
            document.body.appendChild(ov);
            _clRenderCart();
            // Telefonni mijoz hisobidan oldindan to'ldiramiz — bitta maydon kam
            if (!_clForm.phone) {
                apiFetch('/api/my-account').then(({ ok, data }) => {
                    if (ok && data.ok && data.phone && !_clForm.phone) {
                        _clForm.phone = data.phone;
                        const el = $('clPhone');
                        if (el && !el.value) el.value = data.phone;
                    }
                });
            }
        };

        function _clRenderCart() {
            const body = $('clCartBody');
            if (!body) return;
            const t = _clTotals();
            if (!t.count) {
                body.innerHTML = `<button class="cl-modal-close" onclick="this.closest('.cl-modal-overlay').remove()">✕</button>
                    <div class="cl-empty">
                        <div style="font-size:44px">🛒</div>
                        <div>Savat bo'sh</div>
                        <button class="cl-cta" onclick="this.closest('.cl-modal-overlay').remove();clientTab(document.querySelectorAll('.cl-tab')[1],'catalog')">Tovar tanlash →</button>
                    </div>`;
                return;
            }
            const lines = t.items.map(c => {
                const img = c.image_url ? `<img src="${API_BASE_URL + c.image_url}" onerror="this.outerHTML='<div class=ph>📦</div>'">` : '<div class="ph">📦</div>';
                const low = c.stock > 0 && c.stock <= _CL_LOW ? `<div class="cl-cline-low">⚡ ${_clNum(c.stock)} ta qoldi</div>` : '';
                return `<div class="cl-cline">
                    ${img}
                    <div class="cl-cline-info">
                        <div class="cl-cline-name">${escapeHtml(c.name)}</div>
                        <div class="cl-cline-price">${_fmt_sum_cl(c.price)} × ${_clNum(c.qty)} = <b>${_fmt_sum_cl(c.price * c.qty)}</b></div>
                        ${low}
                    </div>
                    <div class="cl-stepper">
                        <button onclick="clInc(${c.id},-1)">−</button>
                        <span>${_clNum(c.qty)}</span>
                        <button onclick="clInc(${c.id},1)">＋</button>
                    </div>
                </div>`;
            }).join('');
            const f = _clForm;
            const opt = (name, val, label) =>
                `<button class="cl-opt${f[name] === val ? ' on' : ''}" onclick="clFormSet('${name}','${val}')">${label}</button>`;
            body.innerHTML = `
                <button class="cl-modal-close" onclick="this.closest('.cl-modal-overlay').remove()">✕</button>
                <div class="cl-sheet-title">🛒 Savatingiz</div>
                <div class="cl-clines">${lines}</div>

                <div class="cl-sheet-sub">Qanday olasiz?</div>
                <div class="cl-opts">
                    ${opt('delivery', 'delivery', '🚚 Yetkazib berish')}
                    ${opt('delivery', 'pickup', '🏪 O\'zim olib ketaman')}
                </div>
                ${f.delivery === 'delivery'
                    ? `<textarea id="clAddr" class="cl-input" rows="2" placeholder="Manzil: tuman, ko'cha, uy, mo'ljal" oninput="clFormSet('address',this.value,true)">${escapeHtml(f.address)}</textarea>`
                    : `<div class="cl-note">📍 ${escapeHtml(_clShop.location || "Do'kon")}. Tayyor bo'lganda xabar beramiz.</div>`}
                <input id="clPhone" class="cl-input" type="tel" placeholder="Telefon: +998 ..." value="${escapeHtml(f.phone)}" oninput="clFormSet('phone',this.value,true)">

                <div class="cl-sheet-sub">To'lov</div>
                <div class="cl-opts">
                    ${opt('payment', 'cash', '💵 Naqd')}
                    ${opt('payment', 'card', '💳 Karta')}
                    ${opt('payment', 'click', '📲 Click')}
                </div>
                <input class="cl-input" placeholder="Izoh (ixtiyoriy)" value="${escapeHtml(f.comment)}" oninput="clFormSet('comment',this.value,true)">

                <div class="cl-sum-row"><span>Jami (${t.count} xil)</span><b>${_fmt_sum_cl(t.sum)}</b></div>
                <div id="clCartErr" class="cl-err"></div>
                <button class="cl-cta big" id="clOrderBtn" onclick="clCheckout()">✅ Buyurtma berish · ${_fmt_sum_cl(t.sum)}</button>
                <div class="cl-trust center">
                    <span>💵 To'lov tovarni olganingizda</span>
                    <span>📞 Operator buyurtmani tasdiqlash uchun qo'ng'iroq qiladi</span>
                </div>`;
        }

        // `quiet` — matn yozilayotganda butun savatni qayta chizmaymiz
        // (aks holda kursor/klaviatura yo'qoladi)
        window.clFormSet = function (name, val, quiet) {
            _clForm[name] = val;
            _clSave(_CL_FORM_KEY, { delivery: _clForm.delivery, address: _clForm.address,
                                    phone: _clForm.phone, payment: _clForm.payment });
            if (!quiet) _clRenderCart();
        };

        window.clCheckout = async function () {
            const t = _clTotals();
            const err = $('clCartErr');
            const btn = $('clOrderBtn');
            const f = _clForm;
            const fail = (msg) => { if (err) err.textContent = msg; _clHaptic('err'); };
            if (!t.count) return;
            if (f.delivery === 'delivery' && !f.address.trim()) { fail('📍 Yetkazish manzilini yozing'); $('clAddr') && $('clAddr').focus(); return; }
            if (f.phone.replace(/\D/g, '').length < 9) { fail('📱 Telefon raqamingizni to\'liq yozing'); $('clPhone') && $('clPhone').focus(); return; }
            if (err) err.textContent = '';
            btn.disabled = true;
            btn.textContent = '⏳ Yuborilmoqda...';
            const { ok, status, data } = await apiFetch('/api/client/order', {
                method: 'POST',
                body: JSON.stringify({
                    items: t.items.map(c => ({ product_id: c.id, qty: c.qty })),
                    delivery: f.delivery, address: f.address, phone: f.phone,
                    payment: f.payment, comment: f.comment,
                }),
            });
            if (!ok || !data.ok) {
                // Qoldiq o'zgargan bo'lsa — savatdagi raqamni yangilab, mijozga ko'rsatamiz
                (data && data.problems || []).forEach(pr => {
                    const c = _clCart[pr.product_id];
                    if (c && pr.available != null) c.stock = Number(pr.available);
                });
                btn.disabled = false;
                btn.textContent = `✅ Buyurtma berish · ${_fmt_sum_cl(t.sum)}`;
                fail('⚠️ ' + ((data && data.error) || ('Xato: ' + status)));
                return;
            }
            _clHaptic('ok');
            _clCart = {};
            _clSave(_CL_CART_KEY, _clCart);
            f.comment = '';
            Object.keys(_clProd).forEach(id => _clRefresh(+id));
            _clRenderBar();
            const tel = (data.contact_phone || _clShop.phone || '').replace(/\s/g, '');
            $('clCartBody').innerHTML = `
                <div class="cl-success">
                    <div class="cl-success-ic">🎉</div>
                    <div class="cl-sheet-title">Buyurtma #${data.order_id} qabul qilindi!</div>
                    <div class="cl-success-sum">${_fmt_sum_cl(data.total)}</div>
                    <div class="cl-steps">
                        <div class="on">✅ Qabul qilindi</div>
                        <div>📞 Operator qo'ng'iroq qiladi</div>
                        <div>${f.delivery === 'pickup' ? '🏪 Tayyor — olib ketasiz' : '🚚 Yetkazib beriladi'}</div>
                    </div>
                    <div class="cl-note">Tasdiq Telegram chatingizga ham yuborildi.</div>
                    <button class="cl-cta" onclick="this.closest('.cl-modal-overlay').remove();clientTab(document.querySelectorAll('.cl-tab')[3],'account')">📋 Buyurtmalarim</button>
                    <a class="cl-cta ghost" href="tel:${tel}">📞 Qo'ng'iroq qilish</a>
                </div>`;
        };

        // ── Qiziqish va cheksiz lenta ────────────────────────────────────
        // Mijoz qaysi tovarlarni ochgani / savatga solgani brauzerda saqlanadi
        // (serverga yozilmaydi) va «Siz uchun» lentasiga urug' bo'lib boradi:
        // server shu tovarlarga o'xshashlarni yuqoriga chiqaradi. Tavsiyalar
        // daromadning katta qismini beradi, lekin faqat RELEVANT bo'lsa —
        // tasodifiy tovar ko'rsatilsa mijoz e'tibor bermaydi (Baymard).
        const _CL_VIEWS_KEY = 'chinor_cl_views_v1';
        const _CL_SEEN_KEY = 'chinor_cl_seen_v1';
        let _clViews = _clLoad(_CL_VIEWS_KEY, []);
        let _clSeen = _clLoad(_CL_SEEN_KEY, {});
        let _clFeedSeed = '';             // lenta qaysi urug' bilan yuklangan

        function _clTrackView(pid) {
            pid = +pid;
            if (!pid) return;
            _clViews = _clViews.filter(x => x !== pid);
            _clViews.push(pid);
            if (_clViews.length > 40) _clViews = _clViews.slice(-40);
            _clSave(_CL_VIEWS_KEY, _clViews);
            const p = _clProd[pid];
            if (p) {
                _clSeen[pid] = { id: p.id, name: p.name, price_sum: _clPrice(p),
                                 image_url: p.image_url || '', qty: p.qty, unit: p.unit || 'dona' };
                const keep = _clViews.slice(-20);
                Object.keys(_clSeen).forEach(k => { if (!keep.includes(+k)) delete _clSeen[k]; });
                _clSave(_CL_SEEN_KEY, _clSeen);
            }
        }

        function _clGridCard(p) {
            const q = Number(p.qty) || 0;
            return `<div class="cl-gcard" onclick="clOpenProduct(${p.id})">
                <div class="cl-gcard-media">
                    ${_cl_img(p)}
                    ${p.sold > 0 ? `<span class="cl-badge">🔥 ${_clNum(p.sold)} sotilgan</span>` : ''}
                    ${q > 0 && q <= _CL_LOW ? `<span class="cl-badge low">Oz qoldi</span>` : ''}
                </div>
                <div class="cl-card-body">
                    <div class="cl-card-name">${escapeHtml(p.name)}</div>
                    <div class="cl-card-foot">
                        <div class="cl-card-price">${_fmt_sum_cl(_clPrice(p))}</div>
                        <div class="cl-card-buy" data-pid="${p.id}" onclick="event.stopPropagation()">${_clBuyCtl(p)}</div>
                    </div>
                </div>
            </div>`;
        }

        // Umumiy cheksiz yuklovchi: `more` elementi ko'rinishga kelganda
        // keyingi sahifa olinadi. Har chaqiruvda eski kuzatuvchi o'chiriladi.
        function _clInfinite(grid, more, root, urlFn, onPage) {
            if (!grid || !more) return;
            if (grid._io) grid._io.disconnect();
            if (grid._onScroll) document.removeEventListener('scroll', grid._onScroll, true);
            grid.innerHTML = '';
            const st = { page: 0, busy: false, done: false, token: {} };
            grid._st = st;
            more.style.display = '';
            more.textContent = 'Yuklanmoqda...';
            const load = async () => {
                if (st.busy || st.done) return;
                st.busy = true;
                const my = st.token;
                const { ok, data } = await apiFetch(urlFn(st.page));
                if (grid._st !== st || my !== st.token) return;   // boshqa lenta boshlangan
                st.busy = false;
                if (!ok || !data.ok) { more.textContent = 'Yuklanmadi — pastga torting'; return; }
                (data.items || []).forEach(_clRemember);
                grid.insertAdjacentHTML('beforeend', (data.items || []).map(_clGridCard).join(''));
                if (onPage) onPage(data, grid);
                st.page++;
                if (!data.has_more) {
                    st.done = true;
                    more.textContent = grid.children.length ? '— hammasi shu —' : '';
                    if (grid._io) grid._io.disconnect();
                    document.removeEventListener('scroll', check, true);
                    return;
                }
                // Kuzatuvchi faqat holat O'ZGARGANDA xabar beradi: "Yuklanmoqda"
                // yozuvi hali ham ko'rinib tursa, yangi xabar kelmaydi va lenta
                // to'xtab qoladi. Qayta ulash darhol joriy holatni yuboradi.
                if (grid._io) { grid._io.unobserve(more); grid._io.observe(more); }
                setTimeout(check, 0);
            };
            // Zaxira: ba'zi (eski) webview'larda IntersectionObserver
            // ishonchsiz — oddiy scroll hodisasida ham joyini tekshiramiz.
            // `offsetParent === null` → bo'lim yashirin (boshqa tab ochiq).
            const check = () => {
                if (st.done || st.busy || !more.isConnected || more.offsetParent === null) return;
                const r = more.getBoundingClientRect();
                if (r.top < window.innerHeight + 400) load();
            };
            grid._onScroll = check;
            document.addEventListener('scroll', check, { capture: true, passive: true });
            grid._io = new IntersectionObserver(es => { if (es.some(e => e.isIntersecting)) load(); },
                                                { root: null, rootMargin: '400px 0px' });
            grid._io.observe(more);
            load();
        }

        function _clRenderRecent() {
            const sec = $('clRecentSec'), box = $('clRecentList');
            if (!sec || !box) return;
            const ids = _clViews.slice().reverse().filter(id => _clSeen[id]).slice(0, 12);
            if (ids.length < 2) { sec.style.display = 'none'; return; }
            sec.style.display = '';
            box.innerHTML = ids.map(id => {
                const p = _clProd[id] || _clSeen[id];
                if (!_clProd[id]) _clProd[id] = Object.assign({}, p);
                return _clCardHtml(p, false);
            }).join('');
        }

        function _clLoadFeed(force) {
            const seed = _clViews.slice(-20).join(',');
            if (!force && seed === _clFeedSeed && $('clFeedGrid') && $('clFeedGrid').children.length) return;
            _clFeedSeed = seed;
            const title = $('clFeedTitle');
            if (title) title.textContent = seed ? '✨ Siz uchun — qiziqishingizga qarab' : '✨ Siz uchun';
            _clInfinite($('clFeedGrid'), $('clFeedMore'), null,
                page => `/api/client/feed?seed=${encodeURIComponent(seed)}&page=${page}`);
        }

        // AI Chat
        window.clSendHint = function(btn) {
            const inp = $('clChatInput');
            if (inp) inp.value = btn.textContent;
            clSendMsg();
        };
        window.clSendMsg = async function() {
            const inp = $('clChatInput');
            const q = (inp ? inp.value : '').trim();
            if (!q) return;
            if (inp) inp.value = '';
            const box = $('clChatBox');
            const intro = box ? box.querySelector('.cl-chat-intro') : null;
            if (intro) intro.remove();
            if (box) box.innerHTML += `<div class="cl-msg-user">${q}</div>`;
            if (box) { box.innerHTML += `<div class="cl-typing" id="clTyping">⏳ AI o'ylayapti...</div>`; box.scrollTop = box.scrollHeight; }
            const { ok, data } = await apiFetch('/api/client/consult', { method:'POST', body: JSON.stringify({question: q}) });
            const typing = $('clTyping');
            if (typing) typing.remove();
            if (!ok || !data.ok) {
                if (box) box.innerHTML += `<div class="cl-msg-ai">⚠️ Xato yuz berdi. Qayta urinib ko'ring.</div>`;
                return;
            }
            if (box) {
                box.innerHTML += `<div class="cl-msg-ai">${data.answer || '...'}</div>`;
                const _pl = data.items || data.products || [];
                if (_pl.length) {
                    _pl.forEach(_clRemember);
                    const cards = _pl.map(p => _clCardHtml(p, false)).join('');
                    box.innerHTML += `<div class="cl-msg-ai-products">${cards}</div>`;
                }
                box.scrollTop = box.scrollHeight;
            }
        };

        const _CL_STATUS = {
            accepted:  ['✅ Qabul qilindi', 'st-acc'],
            on_way:    ['🚚 Yo\'lda',        'st-way'],
            delivered: ['📦 Yetkazildi',     'st-done'],
            confirmed: ['🎉 Qabul qildingiz', 'st-done'],
            cancelled: ['❌ Bekor qilindi',  'st-cancel'],
        };
        let _clOrders = [];

        async function loadClientAccount() {
            const el = $('clAccountBody');
            if (!el) return;
            const { ok, data } = await apiFetch('/api/my-account');
            if (!ok || !data.ok) { el.innerHTML = '<div class="cl-loading">Yuklanmadi</div>'; return; }
            const debt = Number(data.debt_sum || 0);
            if (data.phone && !_clForm.phone) _clForm.phone = data.phone;
            _clOrders = data.orders || [];
            const orders = _clOrders.map(o => {
                const st = _CL_STATUS[o.status] || [o.status || '—', ''];
                const names = (o.items||[]).map(i => escapeHtml(i.name)).join(', ');
                return `<div class="cl-order">
                    <div class="cl-order-top">
                        <b>#${o.id}</b>
                        <span class="cl-order-st ${st[1]}">${st[0]}</span>
                    </div>
                    <div class="cl-order-items">${names || '—'}</div>
                    <div class="cl-order-bottom">
                        <span class="cl-order-date">${escapeHtml(o.created_at||'')}</span>
                        <b>${_fmt_sum_cl(o.total)}</b>
                    </div>
                    ${(o.items||[]).length && _clOrdersOn ? `<button class="cl-reorder" onclick="clReorder(${o.id})">🔁 Yana shuni buyurtma qilish</button>` : ''}
                </div>`;
            }).join('');
            el.innerHTML = `
                <div class="stat-block" style="margin-bottom:12px">
                    <div class="stat-row"><span class="lbl">👤 Ism</span><span class="val">${escapeHtml(data.name||'—')}</span></div>
                    <div class="stat-row"><span class="lbl">📱 Telefon</span><span class="val">${escapeHtml(data.phone||'—')}</span></div>
                    <div class="stat-row"><span class="lbl">💳 Qarz</span><span class="val" style="color:${debt>0?'#e57373':'var(--primary)'}">${debt>0?_fmt_sum_cl(debt):'✅ Qarzsiz'}</span></div>
                </div>
                <div class="cl-section-head" style="margin-top:4px"><span>📋 Buyurtmalarim</span></div>
                ${orders || `<div class="cl-empty">
                    <div style="font-size:40px">🛍</div>
                    <div>Hali buyurtma yo'q</div>
                    <button class="cl-cta" onclick="clientTab(document.querySelectorAll('.cl-tab')[1],'catalog')">Katalogni ko'rish →</button>
                </div>`}`;
        }

        window.clReorder = async function (oid) {
            const o = _clOrders.find(x => x.id === oid);
            if (!o) return;
            let added = 0, missing = 0;
            // Har bir tovarning HOZIRGI narxi va qoldig'ini serverdan olamiz
            await Promise.all((o.items || []).map(async it => {
                const r = await apiFetch('/api/product/' + it.product_id);
                const p = (r.ok && r.data.ok) ? r.data.product : null;
                if (p && Number(p.qty) > 0) {
                    _clRemember(p);
                    _clSet(p, Math.min(Number(it.qty) || 1, Number(p.qty)));
                    added++;
                } else missing++;
            }));
            if (!added) { _clToast('Bu tovarlar hozir qolmagan 😔'); return; }
            if (missing > 0) _clToast(`${missing} ta tovar hozir qolmagan`);
            clOpenCart();
        };

        // Client screen init
        function initClientScreen() {
            const h = new Date().getHours();
            const gr = h < 12 ? 'Xayrli tong' : h < 17 ? 'Xayrli kun' : 'Xayrli kech';
            const grEl = $('clientGreeting');
            if (grEl) grEl.textContent = gr + '!';
            loadClientTop();
            _clLoadFeed(true);
        }

        window.openAccount = async function () {
            showScreen('screenAccount');
            const body = $('accountBody');
            body.innerHTML = '<div class="list-status">Yuklanmoqda...</div>';
            const { ok, status, data } = await apiFetch('/api/my-account');
            if (!ok || !data.ok) {
                body.innerHTML = '<div class="list-status">' +
                    (status === 401 ? '🔒 Qaytadan kiring.' :
                     ('Xato: ' + (data.error || status))) + '</div>';
                return;
            }
            const debtTxt = data.debt_sum > 0
                ? `<span class="val-red">${fmtSum(data.debt_sum)} so'm</span>`
                : `<span class="val-green">Qarzsiz ✅</span>`;
            const typeTxt = data.client_type === 'optom' ? '📦 Optomchi' : '🛍️ Donachi';
            let ordHtml = '';
            (data.orders || []).forEach(o => {
                ordHtml += `<div class="toprow"><span>#${o.id} · ${o.created_at}</span>` +
                    `<span>${fmtSum(o.total)} so'm</span></div>`;
            });
            if (!ordHtml) ordHtml = '<div class="list-status">Buyurtmalar yo\'q</div>';
            body.innerHTML = `
                <div class="stat-block">
                    <div class="big-label">💳 Joriy qarzingiz</div>
                    <div class="big-num">${data.debt_sum > 0 ? fmtSum(data.debt_sum)+" so'm" : "0 so'm"}</div>
                    <div class="stat-row"><span class="lbl">👤 Ism</span><span class="val">${escapeHtml(data.name)}</span></div>
                    <div class="stat-row"><span class="lbl">📱 Telefon</span><span class="val">${escapeHtml(data.phone||'—')}</span></div>
                    <div class="stat-row"><span class="lbl">🏷️ Turi</span><span class="val">${typeTxt}</span></div>
                </div>
                <div class="stat-block">
                    <h3>📊 Bu oy</h3>
                    <div class="stat-row"><span class="lbl">🛒 Zakaz</span><span class="val">${fmtSum(data.month_ordered)} so'm</span></div>
                    <div class="stat-row"><span class="lbl">💳 To'langan</span><span class="val val-green">${fmtSum(data.month_paid)} so'm</span></div>
                </div>
                <div class="stat-block">
                    <h3>📋 Oxirgi buyurtmalar</h3>
                    ${ordHtml}
                </div>
                <div class="stat-block" id="credBlock">
                    <h3>🔑 Kassa uchun login/parol</h3>
                    <div class="list-status">Yuklanmoqda...</div>
                </div>`;
            renderCredentials();
        };

        // ================================================================
        // BOT MENYUSIDAN KO'CHIRILGAN 5 TA BO'LIM
        // ================================================================
        // Kategoriyalar, Yetkazib beruvchilar, Past qoldiq, Eksport va
        // AI Analitika ilgari faqat botda edi. Beshalasi ham bitta umumiy
        // ekranga (#screenTool) chiziladi — har biriga alohida ekran
        // yasamaymiz, chunki tuzilishi bir xil: sarlavha + ro'yxat.
        function openTool(title, html) {
            const t = $('toolTitle'), b = $('toolBody');
            if (t) t.textContent = title;
            if (b) b.innerHTML = html || '<div class="list-status">Yuklanmoqda...</div>';
            showScreen('screenTool');
        }

        function toolError(status, data) {
            return '<div class="list-status">' +
                (status === 401 ? '🔒 Qaytadan kiring.'
                 : status === 403 ? '⛔ Bu bo\'lim uchun ruxsatingiz yo\'q.'
                 : ('Xato: ' + ((data && data.error) || status))) + '</div>';
        }

        // ── Kategoriyalar ────────────────────────────────────────────────
        window.openCategories = async function () {
            openTool('🗂 Kategoriyalar');
            const { ok, status, data } = await apiFetch('/api/categories');
            if (!ok || !data.ok) return openTool('🗂 Kategoriyalar', toolError(status, data));
            let rows = '';
            (data.categories || []).forEach(c => {
                rows += `<div class="toprow"><span>${escapeHtml(c.name)}</span>` +
                    `<span><button class="more-btn" style="padding:4px 10px"
                      onclick="deleteCategory(${c.id}, '${escapeHtml(c.name).replace(/'/g, "\\'")}')">🗑</button></span></div>`;
            });
            if (!rows) rows = '<div class="list-status">Kategoriya yo\'q</div>';
            openTool('🗂 Kategoriyalar', `
                <div class="stat-block">
                    <input type="text" id="catName" placeholder="Yangi kategoriya nomi"
                           style="width:100%;padding:10px;border-radius:8px;margin-bottom:8px;
                                  border:1px solid var(--border,#ccc)">
                    <button class="more-btn" style="width:100%" onclick="saveCategory()">➕ Qo'shish</button>
                    <div id="catMsg" class="list-status"></div>
                </div>
                <div class="stat-block">${rows}</div>`);
        };

        window.saveCategory = async function () {
            const name = (($('catName') || {}).value || '').trim();
            const msg = $('catMsg');
            if (!name) { if (msg) msg.textContent = 'Nom kiriting'; return; }
            const { ok, data } = await apiFetch('/api/category/save', {
                method: 'POST', body: JSON.stringify({ name })
            });
            if (ok && data.ok) openCategories();
            else if (msg) msg.innerHTML = '<span class="val-red">' +
                escapeHtml((data && data.error) || 'Saqlanmadi') + '</span>';
        };

        window.deleteCategory = async function (id, name) {
            if (!confirm(`"${name}" kategoriyasi o'chirilsinmi?`)) return;
            await apiFetch('/api/category/delete', {
                method: 'POST', body: JSON.stringify({ id })
            });
            openCategories();
        };

        // ── Yetkazib beruvchilar ─────────────────────────────────────────
        window.openSuppliers = async function () {
            openTool('🚚 Yetkazib beruvchilar');
            const { ok, status, data } = await apiFetch('/api/suppliers');
            if (!ok || !data.ok) return openTool('🚚 Yetkazib beruvchilar', toolError(status, data));
            let rows = '';
            (data.suppliers || []).forEach(x => {
                rows += `<div class="toprow"><span>${escapeHtml(x.name)}</span>` +
                    `<span class="val">${escapeHtml(x.phone || '—')}</span></div>`;
            });
            if (!rows) rows = '<div class="list-status">Ro\'yxat bo\'sh</div>';
            openTool('🚚 Yetkazib beruvchilar', `
                <div class="stat-block">
                    <input type="text" id="supName" placeholder="Nomi"
                           style="width:100%;padding:10px;border-radius:8px;margin-bottom:6px;
                                  border:1px solid var(--border,#ccc)">
                    <input type="text" id="supPhone" placeholder="Telefon (ixtiyoriy)"
                           style="width:100%;padding:10px;border-radius:8px;margin-bottom:8px;
                                  border:1px solid var(--border,#ccc)">
                    <button class="more-btn" style="width:100%" onclick="saveSupplier()">➕ Qo'shish</button>
                    <div id="supMsg" class="list-status"></div>
                </div>
                <div class="stat-block">${rows}</div>`);
        };

        window.saveSupplier = async function () {
            const name = (($('supName') || {}).value || '').trim();
            const phone = (($('supPhone') || {}).value || '').trim();
            const msg = $('supMsg');
            if (!name) { if (msg) msg.textContent = 'Nom kiriting'; return; }
            const { ok, data } = await apiFetch('/api/supplier/save', {
                method: 'POST', body: JSON.stringify({ name, phone })
            });
            if (ok && data.ok) openSuppliers();
            else if (msg) msg.innerHTML = '<span class="val-red">' +
                escapeHtml((data && data.error) || 'Saqlanmadi') + '</span>';
        };

        // ── Buyurtma ro'yxati ────────────────────────────────────────────
        // Kimdan nimani, qanchadan olish kerak. Miqdor ketish tezligidan
        // hisoblanadi (server: marketplace/reorder.py) — bu yerda faqat
        // ko'rsatish. Ikki qadam: kompaniyalar → bitta kompaniya ro'yxati.
        function _rmoney(v) { return Number(v || 0).toLocaleString('uz-UZ'); }

        // Tahlil davri — ketish tezligi QAYSI kunlardan hisoblanishi.
        // Qoldiq har doim hozirgi: davr faqat savdo tezligiga taalluqli.
        let _reoWin = { days: 30, from: '', to: '' };

        function _reoQuery() {
            return _reoWin.from || _reoWin.to
                ? `days=${_reoWin.days}&from=${_reoWin.from}&to=${_reoWin.to}`
                : `days=${_reoWin.days}`;
        }

        function _reoLabel() {
            return (_reoWin.from || _reoWin.to)
                ? `${_reoWin.from || '…'} — ${_reoWin.to || 'bugun'}`
                : `oxirgi ${_reoWin.days} kun`;
        }

        function _reoPeriodBox(sid) {
            const btn = (d, t) => `<button class="rcpt-fbtn${
                (!_reoWin.from && !_reoWin.to && _reoWin.days === d) ? ' active' : ''
            }" onclick="setReoPreset(${d}, ${sid})">${t}</button>`;
            return `<div class="stat-block">
                <div class="toprow"><span>📅 Savdo davri</span>
                     <span class="val">${escapeHtml(_reoLabel())}</span></div>
                <div style="display:flex;gap:6px;flex-wrap:wrap;margin:8px 0">
                    ${btn(7, '7 kun')}${btn(20, '20 kun')}${btn(30, '30 kun')}${btn(90, '90 kun')}
                    <button class="rcpt-fbtn${(_reoWin.from || _reoWin.to) ? ' active' : ''}"
                            onclick="toggleReoDates()">📅 Sana tanlash</button>
                </div>
                <div id="reoDates" style="display:${(_reoWin.from || _reoWin.to) ? 'block' : 'none'}">
                    <div style="display:flex;gap:6px;align-items:center;margin-bottom:6px">
                        <input type="date" id="reoFrom" value="${_reoWin.from}"
                               max="${new Date().toISOString().slice(0, 10)}"
                               style="flex:1;padding:8px;border-radius:8px;
                                      border:1px solid var(--border,#ccc)">
                        <span>—</span>
                        <input type="date" id="reoTo" value="${_reoWin.to}"
                               max="${new Date().toISOString().slice(0, 10)}"
                               style="flex:1;padding:8px;border-radius:8px;
                                      border:1px solid var(--border,#ccc)">
                    </div>
                    <button class="more-btn" style="width:100%"
                            onclick="applyReoDates(${sid})">Qo'llash</button>
                </div></div>`;
        }

        window.setReoPreset = function (days, sid) {
            _reoWin = { days: days, from: '', to: '' };
            sid ? openReorderList(sid) : openReorder();
        };

        window.toggleReoDates = function () {
            const box = $('reoDates');
            if (box) box.style.display = box.style.display === 'none' ? 'block' : 'none';
        };

        window.applyReoDates = function (sid) {
            const f = (($('reoFrom') || {}).value || '').trim();
            const t = (($('reoTo') || {}).value || '').trim();
            if (!f && !t) { alert('Kamida bitta sana tanlang'); return; }
            if (f && t && f > t) { alert('Boshlanish sanasi tugashdan keyin bo\'lmasin'); return; }
            _reoWin = { days: _reoWin.days, from: f, to: t };
            sid ? openReorderList(sid) : openReorder();
        };

        window.openReorder = async function () {
            openTool('📋 Buyurtma ro\'yxati');
            const { ok, status, data } = await apiFetch('/api/reorder/suppliers?' + _reoQuery());
            if (!ok || !data.ok) return openTool('📋 Buyurtma ro\'yxati', toolError(status, data));

            let rows = '';
            (data.suppliers || []).forEach(s => {
                const urgent = s.urgent
                    ? `<span class="val-red">🔴 ${s.urgent} ta tugagan</span>` : '';
                const line = s.to_order
                    ? `${s.to_order} pozitsiya · ${_rmoney(s.amount)} so'm`
                    : '<span class="val-green">✅ Buyurtma kerak emas</span>';
                const zeroTxt = s.zero_stock
                    ? `<br><small>🔵 ${s.zero_stock} ta tovar qoldig'i 0</small>` : '';
                rows += `<div class="toprow" style="cursor:pointer;align-items:flex-start"
                              onclick="openReorderList(${s.id})">
                    <span><b>${escapeHtml(s.name)}</b><br>
                      <small style="opacity:.75">${line}</small>
                      ${urgent ? '<br><small>' + urgent + '</small>' : ''}${zeroTxt}</span>
                    <span class="val">›</span></div>`;
            });
            if (!rows) rows = '<div class="list-status">Yetkazib beruvchi biriktirilmagan</div>';

            const warn = data.warning
                ? `<div class="list-status" style="text-align:left">⚠️ ${escapeHtml(data.warning)}</div>` : '';
            const rest = data.unassigned
                ? `<div class="list-status" style="text-align:left">
                     ${data.unassigned} ta harakatdagi tovarga yetkazib beruvchi
                     biriktirilmagan — ular hech qaysi ro'yxatga tushmaydi.</div>` : '';
            openTool('📋 Buyurtma ro\'yxati',
                `${warn}${_reoPeriodBox(0)}<div class="stat-block">${rows}</div>${rest}`);
        };

        window.openReorderList = async function (sid) {
            openTool('📋 Buyurtma');
            const { ok, status, data } = await apiFetch(
                '/api/reorder?supplier_id=' + sid + '&' + _reoQuery());
            if (!ok || !data.ok) return openTool('📋 Buyurtma', toolError(status, data));

            const all = data.items || [];
            const need = all.filter(i => i.need > 0);
            // Qoldiq 0 ga tushgan, lekin buyurtma hisoblanmagan tovarlar —
            // ko'k belgi bilan ALOHIDA. Ular tahlilga tushmagan (savdo tarixi
            // yo'q yoki faqat yirik savdo bo'lgan), shuning uchun odam ko'zdan
            // kechirishi kerak: kerakmi yoki umuman olib qo'yiladimi.
            const zero = all.filter(i => i.need <= 0 && i.stock <= 0);
            const rest = all.filter(i => i.need <= 0 && i.stock > 0);

            const row = (i) => {
                const mark = i.status === 'SHOSHILINCH' ? '🔴'
                           : i.status === 'BUYURTMA' ? '🟡'
                           : i.stock <= 0 ? '🔵' : '⚪';
                const left = (i.days_left === null) ? '—' : (i.days_left + ' kun');
                const qty = i.need > 0 ? `<b>${i.need}</b>` : '—';
                // Yirik (bir martalik) savdo miqdordan chiqarilgan — buni
                // yashirmaymiz: yana shunday xarid kutilsa odam o'zi qo'shadi.
                const bulk = i.bulk_count
                    ? `<br><small class="val-red">⚠ ${escapeHtml(i.bulk_note)} —
                       hisobga olinmadi</small>` : '';
                return `<div class="toprow" style="align-items:flex-start">
                    <span>${mark} ${escapeHtml(i.name)}<br>
                      <small style="opacity:.7">kuniga ${i.rate} · qoldiq ${i.stock} ·
                      yetadi ${left}</small>${bulk}</span>
                    <span class="val">${qty}</span></div>`;
            };

            const warn = data.warning
                ? `<div class="list-status" style="text-align:left">⚠️ ${
                    escapeHtml(data.warning)}</div>` : '';
            let html = warn + _reoPeriodBox(sid) + `<div class="stat-block">
                <button class="more-btn" style="width:100%"
                        onclick="sendReorderTelegram(${sid})">📤 Excel'ni Telegramga yuborish</button>
                <button class="more-btn" style="width:100%;margin-top:6px;opacity:.85"
                        onclick="downloadReorder(${sid})">📥 Brauzerda yuklab olish</button>
                <div id="reoMsg" class="list-status"></div>
                <div class="toprow"><span>Buyurtmaga</span>
                     <span class="val">${need.length} pozitsiya</span></div>
                <div class="toprow"><span>Taxminiy summa</span>
                     <span class="val">${_rmoney(data.total)} so'm</span></div>`;
            if (data.lost_per_day > 0) {
                html += `<div class="toprow"><span>Tugagani uchun kunlik zarar</span>
                     <span class="val val-red">${_rmoney(data.lost_per_day)} so'm</span></div>`;
            }
            html += `<div class="toprow"><span>Muddat</span>
                     <span class="val">${data.lead}+${data.cycle} kun</span></div></div>`;

            html += '<div class="stat-block">' +
                (need.length ? need.map(row).join('')
                             : '<div class="list-status">✅ Hozircha kerak emas</div>') +
                '</div>';
            if (zero.length) {
                html += `<div class="stat-block">
                    <h3>🔵 Qoldiq 0 — ko'rib chiqing (${zero.length})</h3>
                    <div class="list-status" style="text-align:left">
                      Tugagan, lekin savdo ma'lumoti yetarli emas — buyurtma
                      miqdorini o'zingiz belgilaysiz.</div>` +
                    zero.map(row).join('') + '</div>';
            }
            if (rest.length) {
                html += `<div class="stat-block"><h3 style="opacity:.7">⚪ Yetarli (${rest.length})</h3>` +
                    rest.map(row).join('') + '</div>';
            }
            html += `<div class="stat-block"><button class="more-btn" style="width:100%"
                        onclick="openReorder()">← Kompaniyalar</button></div>`;
            openTool('📋 ' + escapeHtml(data.name || 'Buyurtma'), html);
        };

        window.sendReorderTelegram = async function (sid) {
            const msg = $('reoMsg');
            if (msg) msg.textContent = 'Tayyorlanmoqda...';
            const { ok, data } = await apiFetch('/api/reorder/send', {
                method: 'POST',
                body: JSON.stringify({
                    supplier_id: sid, days: _reoWin.days,
                    from: _reoWin.from, to: _reoWin.to
                })
            });
            if (ok && data && data.ok) {
                if (msg) msg.innerHTML = '<span class="val-green">✅ Telegramga yuborildi — ' +
                    'bot suhbatidan oching</span>';
            } else if (msg) {
                msg.innerHTML = '<span class="val-red">' +
                    escapeHtml((data && data.error) || 'Yuborilmadi') + '</span>';
            }
        };

        window.downloadReorder = function (sid) {
            const msg = $('reoMsg');
            if (msg) msg.textContent = 'Tayyorlanmoqda...';
            // Eksport bilan bir xil yo'l: <a download> ga header qo'shib
            // bo'lmaydi, shuning uchun imzo query orqali ketadi.
            const url = `${API_BASE_URL}/api/reorder/export?supplier_id=${sid}` +
                        `&${_reoQuery()}` +
                        `&initData=${encodeURIComponent((tg && tg.initData) || '')}`;
            try {
                if (tg && tg.openLink) tg.openLink(url);
                else window.open(url, '_blank');
                if (msg) msg.innerHTML = '<span class="val-green">✅ Yuklanmoqda…</span>';
            } catch (e) {
                if (msg) msg.innerHTML = '<span class="val-red">Ochilmadi</span>';
            }
        };

        // ── Savdo tarixi (YesPOS cheklari) ───────────────────────────────
        // Manba: YesPOS boti Telegramga yuborgan cheklar. Eski «Cheklar»
        // ekrani Chinor kassasiniki — u tegilmagan, bu yonida turadi.
        let _salesDays = 1;
        let _turnDays = 30;

        window.openSalesHistory = async function () {
            openTool('🧾 Savdo tarixi');
            const { ok, status, data } = await apiFetch('/api/sales/history?days=' + _salesDays);
            if (!ok || !data.ok) return openTool('🧾 Savdo tarixi', toolError(status, data));

            const btn = (d, t) => `<button class="rcpt-fbtn${_salesDays === d ? ' active' : ''}"
                onclick="setSalesDays(${d})">${t}</button>`;

            let head = `<div class="stat-block">
                <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px">
                    ${btn(1, 'Bugun')}${btn(7, '7 kun')}${btn(30, '30 kun')}
                </div>`;
            (data.daily || []).slice(0, 7).forEach(d => {
                head += `<div class="toprow"><span>${d.day}</span>
                    <span class="val">${d.count} chek · ${_rmoney(d.sum)} so'm</span></div>`;
            });
            head += '</div>';

            let rows = '';
            (data.items || []).forEach(r => {
                const mark = r.kind === 'qaytarish' ? '↩️'
                           : r.kind === 'nasiya' ? '🤝' : '🧾';
                const who = r.client ? ' · ' + escapeHtml(r.client) : '';
                rows += `<div class="toprow" style="cursor:pointer;align-items:flex-start"
                              onclick="openSaleReceipt(${r.id})">
                    <span>${mark} №${escapeHtml(r.doc_no)} <small style="opacity:.7">
                      ${escapeHtml(r.at.slice(5, 16))}${who}</small><br>
                      <small style="opacity:.7">${r.lines} tovar ·
                      ${escapeHtml(r.cashier)}</small></span>
                    <span class="val">${_rmoney(r.payable)}</span></div>`;
            });
            if (!rows) rows = '<div class="list-status">Bu davrda chek yo\'q</div>';

            openTool('🧾 Savdo tarixi', head + `<div class="stat-block">${rows}</div>`);
        };

        window.setSalesDays = function (d) { _salesDays = d; openSalesHistory(); };

        window.openSaleReceipt = async function (id) {
            openTool('🧾 Chek');
            const { ok, status, data } = await apiFetch('/api/sales/receipt?id=' + id);
            if (!ok || !data.ok) return openTool('🧾 Chek', toolError(status, data));

            let rows = '';
            (data.items || []).forEach(i => {
                // Katalogga bog'lanmagan qator tahlilga tushmaydi — belgilaymiz.
                const warn = i.linked ? '' :
                    ' <small class="val-red">(katalogda topilmadi)</small>';
                rows += `<div class="toprow" style="align-items:flex-start">
                    <span>${escapeHtml(i.name)}${warn}<br>
                      <small style="opacity:.7">${i.qty} × ${_rmoney(i.price)}</small></span>
                    <span class="val">${_rmoney(i.amount)}</span></div>`;
            });

            openTool('🧾 №' + escapeHtml(data.doc_no), `
                <div class="stat-block">
                    <div class="toprow"><span>Sana</span>
                         <span class="val">${escapeHtml(data.at)}</span></div>
                    <div class="toprow"><span>Turi</span>
                         <span class="val">${escapeHtml(data.kind)}</span></div>
                    <div class="toprow"><span>Sotuvchi</span>
                         <span class="val">${escapeHtml(data.cashier)}</span></div>
                    ${data.client ? `<div class="toprow"><span>Mijoz</span>
                         <span class="val">${escapeHtml(data.client)}</span></div>` : ''}
                </div>
                <div class="stat-block">${rows}</div>
                <div class="stat-block">
                    <div class="toprow"><span>Jami</span>
                         <span class="val">${_rmoney(data.total)} so'm</span></div>
                    ${data.discount ? `<div class="toprow"><span>Chegirma</span>
                         <span class="val">${_rmoney(data.discount)} so'm</span></div>` : ''}
                    <div class="toprow"><span><b>To'landi</b></span>
                         <span class="val"><b>${_rmoney(data.payable)} so'm</b></span></div>
                    <button class="more-btn" style="width:100%;margin-top:8px"
                            onclick="openSalesHistory()">← Savdo tarixi</button>
                </div>`);
        };

        // ── Past qoldiq ──────────────────────────────────────────────────
        window.openLowStock = async function () {
            openTool('⚠️ Past qoldiq');
            const { ok, status, data } = await apiFetch('/api/low-stock');
            if (!ok || !data.ok) return openTool('⚠️ Past qoldiq', toolError(status, data));
            let rows = '';
            (data.items || data.products || []).forEach(p => {
                const cls = p.qty <= 0 ? 'val-red' : '';
                rows += `<div class="toprow"><span>${escapeHtml(p.name)}</span>` +
                    `<span class="val ${cls}">${p.qty} ${escapeHtml(p.unit || '')}</span></div>`;
            });
            if (!rows) rows = '<div class="list-status">✅ Hammasi yetarli</div>';
            openTool('⚠️ Past qoldiq',
                `<div class="stat-block"><h3>${data.count} ta tovar</h3>${rows}</div>`);
        };

        // ── Eksport ──────────────────────────────────────────────────────
        window.openExport = function () {
            openTool('📥 Eksport', `
                <div class="stat-block">
                    <div class="list-status" style="text-align:left">
                        Excel fayl yuklab olinadi.
                    </div>
                    <button class="more-btn" style="width:100%;margin-top:8px"
                            onclick="downloadExport('sales')">🧾 Sotuvlar</button>
                    <button class="more-btn" style="width:100%;margin-top:6px"
                            onclick="downloadExport('clients')">👤 Mijozlar</button>
                    <button class="more-btn" style="width:100%;margin-top:6px"
                            onclick="downloadExport('debtors')">💳 Qarzdorlar</button>
                    <div id="expMsg" class="list-status"></div>
                </div>`);
        };

        window.downloadExport = function (kind) {
            const msg = $('expMsg');
            if (msg) msg.textContent = 'Tayyorlanmoqda...';
            // Faylni yangi oynada ochamiz; sessiya tokeni query orqali ketadi,
            // chunki <a download> ga maxsus header qo'shib bo'lmaydi.
            const url = `${API_BASE_URL}/api/export?type=${encodeURIComponent(kind)}` +
                        `&initData=${encodeURIComponent((tg && tg.initData) || '')}`;
            try {
                if (tg && tg.openLink) tg.openLink(url);
                else window.open(url, '_blank');
                if (msg) msg.innerHTML = '<span class="val-green">✅ Yuklanmoqda…</span>';
            } catch (e) {
                if (msg) msg.innerHTML = '<span class="val-red">Ochilmadi</span>';
            }
        };

        // ── AI Analitika ─────────────────────────────────────────────────
        window.openAiAnalytics = function () {
            openTool('🤖 AI Analitika', `
                <div class="stat-block">
                    <div class="list-status" style="text-align:left">
                        Do'kon ma'lumotlari asosida tahlil. Tayyor savol tanlang
                        yoki o'zingiznikini yozing.
                    </div>
                    <button class="more-btn" style="width:100%;margin-top:8px"
                            onclick="askAi('top')">🔥 Nima yaxshi ketyapti?</button>
                    <button class="more-btn" style="width:100%;margin-top:6px"
                            onclick="askAi('misses')">🔍 Qidirilgan-u yo'q tovarlar</button>
                    <input type="text" id="aiQ" placeholder="O'z savolingiz..."
                           style="width:100%;padding:10px;border-radius:8px;margin-top:10px;
                                  border:1px solid var(--border,#ccc)">
                    <button class="more-btn" style="width:100%;margin-top:6px"
                            onclick="askAi('')">💬 So'rash</button>
                </div>
                <div id="aiOut" class="stat-block" style="display:none"></div>`);
        };

        window.askAi = async function (preset) {
            const out = $('aiOut');
            const question = (($('aiQ') || {}).value || '').trim();
            if (!preset && !question) return;
            if (out) {
                out.style.display = '';
                out.innerHTML = '<div class="list-status">⏳ AI o\'ylayapti…</div>';
            }
            const { ok, status, data } = await apiFetch('/api/ai/analyze', {
                method: 'POST', body: JSON.stringify({ preset, question })
            });
            if (!out) return;
            if (ok && data.ok) out.innerHTML = data.answer || '—';
            else out.innerHTML = toolError(status, data);
        };

        // ── Reyting (top mijozlar) ───────────────────────────────────────
        window.openTopClients = async function () {
            openTool('🏆 Reyting');
            const { ok, status, data } = await apiFetch('/api/clients/top');
            if (!ok || !data.ok) return openTool('🏆 Reyting', toolError(status, data));
            const list = data.clients || data.top || [];
            let rows = '';
            list.forEach((c, i) => {
                const medal = i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : `${i + 1}.`;
                const sum = c.total_sum != null ? c.total_sum : (c.total || 0);
                rows += `<div class="toprow"><span>${medal} ${escapeHtml(c.shop_name || c.name || '—')}</span>` +
                        `<span class="val">${fmtSum(sum)} so'm</span></div>`;
            });
            if (!rows) rows = '<div class="list-status">Ma\'lumot yo\'q</div>';
            openTool('🏆 Reyting', `<div class="stat-block"><h3>Eng ko'p xarid qilganlar</h3>${rows}</div>`);
        };

        // ── Umumiy foyda ─────────────────────────────────────────────────
        // Aylanma va foyda — YesPOS CHEKLARIDAN (haqiqiy savdo). Chinor
        // bazasidagi `sales` jadvalida bir nechta yozuv bor, xolos: savdo
        // YesPOS kassasida bo'ladi.
        window.openTurnover = async function (days) {
            _turnDays = days || _turnDays;
            openTool('💰 Aylanma va foyda');
            const { ok, status, data } = await apiFetch('/api/sales/summary?days=' + _turnDays);
            if (!ok || !data.ok) return openTool('💰 Aylanma va foyda', toolError(status, data));

            const btn = (d, t) => `<button class="rcpt-fbtn${_turnDays === d ? ' active' : ''}"
                onclick="openTurnover(${d})">${t}</button>`;
            const t = data.today || {}, p = data.period || {};

            let html = `<div class="stat-block">
                <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px">
                    ${btn(7, '7 kun')}${btn(30, '30 kun')}${btn(90, '90 kun')}
                </div>
                <h3>Bugun (${escapeHtml(t.day || '')})</h3>
                <div class="toprow"><span>Aylanma</span>
                     <span class="val">${_rmoney(t.revenue)} so'm</span></div>
                <div class="toprow"><span>Foyda</span>
                     <span class="val val-green">${_rmoney(t.profit)} so'm</span></div>
                <div class="toprow"><span>Cheklar</span>
                     <span class="val">${t.count}</span></div>
            </div>
            <div class="stat-block">
                <h3>${_turnDays} kunda</h3>
                <div class="toprow"><span>Aylanma</span>
                     <span class="val">${_rmoney(p.revenue)} so'm</span></div>
                <div class="toprow"><span>Foyda</span>
                     <span class="val val-green">${_rmoney(p.profit)} so'm</span></div>
                <div class="toprow"><span>Cheklar</span>
                     <span class="val">${p.count}</span></div>
                <div class="toprow"><span>O'rtacha chek</span>
                     <span class="val">${_rmoney(p.avg_check)} so'm</span></div>`;
            if (data.profit_coverage < 100) {
                html += `<div class="list-status" style="text-align:left">
                    ⚠️ Foyda savdoning ${data.profit_coverage}% i bo'yicha hisoblandi —
                    qolganida tannarx yoki katalog bog'lanishi yo'q.</div>`;
            }
            html += '</div>';

            let rows = '';
            (data.daily || []).slice(0, 14).forEach(d => {
                rows += `<div class="toprow"><span>${d.day} <small style="opacity:.6">
                    (${d.count})</small></span><span class="val">${_rmoney(d.sum)}
                    <small class="val-green">+${_rmoney(d.profit)}</small></span></div>`;
            });
            html += `<div class="stat-block"><h3>Kunlik aylanma</h3>${rows}</div>`;

            let tops = '';
            (data.top_products || []).forEach(x => {
                tops += `<div class="toprow"><span>${escapeHtml(x.name)}</span>
                    <span class="val">${x.qty} · ${_rmoney(x.sum)}</span></div>`;
            });
            html += `<div class="stat-block"><h3>Top tovarlar</h3>${tops}</div>`;

            let cash = '';
            (data.cashiers || []).forEach(x => {
                cash += `<div class="toprow"><span>${escapeHtml(x.name)}</span>
                    <span class="val">${x.count} chek · ${_rmoney(x.sum)}</span></div>`;
            });
            if (cash) html += `<div class="stat-block"><h3>Sotuvchilar</h3>${cash}</div>`;

            openTool('💰 Aylanma va foyda', html);
        };

        window.openProfit = async function () {
            openTool('💰 Umumiy foyda');
            const { ok, status, data } = await apiFetch('/api/stats');
            if (!ok || !data.ok) return openTool('💰 Umumiy foyda', toolError(status, data));
            const rev = data.revenue_sum || 0, prof = data.profit_sum || 0;
            const margin = rev > 0 ? ((prof / rev) * 100).toFixed(1) : '0';
            openTool('💰 Umumiy foyda', `
                <div class="stat-block">
                    <div class="big-label">💰 Sof foyda</div>
                    <div class="big-num">${fmtSum(prof)} so'm</div>
                    <div class="stat-row"><span class="lbl">💵 Tushum</span><span class="val">${fmtSum(rev)} so'm</span></div>
                    <div class="stat-row"><span class="lbl">📈 Foyda ulushi</span><span class="val val-green">${margin}%</span></div>
                    <div class="stat-row"><span class="lbl">🧾 Sotuvlar</span><span class="val">${data.sale_count || 0}</span></div>
                    <div class="stat-row"><span class="lbl">📋 Buyurtmalar</span><span class="val">${data.order_count || 0}</span></div>
                </div>`);
        };

        // ================================================================
        // KASSA/ADMIN ILOVASI UCHUN LOGIN-PAROL
        // ================================================================
        // Mini App Telegram imzosi bilan kiradi — bu yerda parol kerak emas.
        // Lekin kassa va admin ilovalari Telegramdan TASHQARIDA ishlaydi,
        // ularda initData yo'q, shuning uchun ular uchun login+parol
        // o'rnatiladi. Ilgari buni botdagi «🔑 Login/parol» tugmasi qilardi.
        async function renderCredentials() {
            const box = $('credBlock');
            if (!box) return;
            const { ok, data } = await apiFetch('/api/credentials');
            const cur = (ok && data.ok && data.username) ? data.username : '';
            const has = ok && data.ok && data.has_credentials;
            box.innerHTML = `
                <h3>🔑 Kassa uchun login/parol</h3>
                <div class="list-status" style="text-align:left">
                    Bu login faqat <b>kassa</b> va <b>admin</b> dasturlariga
                    kirish uchun. Mini App'ga kirishda kerak emas.
                </div>
                ${has ? `<div class="stat-row"><span class="lbl">Joriy login</span>
                         <span class="val">${escapeHtml(cur)}</span></div>` : ''}
                <input type="text" id="credUser" placeholder="Login"
                       autocomplete="off" value="${escapeHtml(cur)}"
                       style="width:100%;margin:8px 0;padding:10px;border-radius:8px;
                              border:1px solid var(--border,#ccc)">
                <input type="password" id="credPass" placeholder="Yangi parol"
                       autocomplete="new-password"
                       style="width:100%;margin:0 0 8px;padding:10px;border-radius:8px;
                              border:1px solid var(--border,#ccc)">
                <button class="more-btn" style="width:100%"
                        onclick="saveCredentials()">${has ? 'Yangilash' : 'Saqlash'}</button>
                <div id="credMsg" class="list-status"></div>`;
        }

        window.saveCredentials = async function () {
            const u = ($('credUser') || {}).value || '';
            const pw = ($('credPass') || {}).value || '';
            const msg = $('credMsg');
            if (msg) msg.textContent = 'Saqlanmoqda...';
            const { ok, data } = await apiFetch('/api/credentials', {
                method: 'POST',
                body: JSON.stringify({ username: u.trim(), password: pw.trim() }),
            });
            if (!msg) return;
            if (ok && data.ok) {
                msg.innerHTML = '<span class="val-green">✅ Saqlandi. Endi kassa ' +
                                'dasturiga shu login/parol bilan kiring.</span>';
                const pf = $('credPass'); if (pf) pf.value = '';
            } else {
                msg.innerHTML = '<span class="val-red">⚠️ ' +
                    escapeHtml((data && data.error) || 'Saqlanmadi') + '</span>';
            }
        };

        // ================================================================
        // POS SOTUV (kassa) — savat in-memory
        // ================================================================
        let cart = {};            // { pid: {id,name,price_sum,qty,unit,max} }
        let posClient = null;     // tanlangan mijoz {id,name,allow_credit,is_internal}
        let _clientsCache = [];   // /api/clients keshi (mijoz tanlash modali uchun)
        let _posPage = 0, _posQuery = '', _posLoading = false, _posSearchTimer = null;

        const posList = $('posList'), posStatus = $('posStatus'), posMore = $('posMore');
        const posSearch = $('posSearch'), posSearchClear = $('posSearchClear');

        window.openPOS = function () {
            _posQuery = ''; posSearch.value = ''; posSearchClear.style.display = 'none';
            posClient = null; updateCartClientUI();
            showScreen('screenPOS');
            loadPOS(true);
            updateCartBar();
        };
        window.goBackFromPOS = function () { showScreen('screenAdmin'); };

        async function loadPOS(reset) {
            if (_posLoading) return;
            _posLoading = true;
            if (reset) { _posPage = 0; posList.innerHTML = ''; posMore.style.display='none'; }
            posStatus.textContent = 'Yuklanmoqda...';
            try {
                const qs = new URLSearchParams({page:String(_posPage), q:_posQuery}).toString();
                const { ok, status, data } = await apiFetch('/api/products?' + qs);
                if (!ok || !data.ok) {
                    posStatus.textContent = (status===401)?'🔒 Qaytadan kiring.':('Xato: '+(data.error||status));
                    _posLoading = false; return;
                }
                renderPOS(data.items || []);
                posStatus.textContent = posList.children.length ? '' :
                    (_posQuery ? 'Topilmadi.' : 'Mahsulot yo\'q.');
                posMore.style.display = data.has_more ? 'block' : 'none';
            } catch (err) {
                posStatus.textContent = 'Tarmoq xatosi: ' + (err.message||err);
            }
            _posLoading = false;
        }
        window.loadMorePOS = function () { _posPage += 1; loadPOS(false); };

        function renderPOS(items) {
            const frag = document.createDocumentFragment();
            items.forEach(p => {
                if (p.qty <= 0) return;  // tugaganlarni kassada ko'rsatmaymiz
                const card = document.createElement('div');
                card.className = 'prod-card';
                const inCart = cart[p.id];
                const badge = inCart ? `<span class="in-cart-badge">${fmtQty(inCart.qty)}</span>` : '';
                card.innerHTML = `
                    <div class="prod-info">
                        <div class="prod-name">${escapeHtml(p.name)} ${badge}</div>
                        <div class="prod-meta">Qoldiq: ${fmtQty(p.qty)} ${p.unit}</div>
                    </div>
                    <div class="prod-right">
                        <div class="prod-price">${fmtPrice(p)}</div>
                    </div>`;
                card.onclick = () => addToCart(p);
                frag.appendChild(card);
            });
            posList.appendChild(frag);
        }

        function addToCart(p) {
            const cur = cart[p.id];
            const have = cur ? cur.qty : 0;
            if (have + 1 > p.qty) {
                toast(`Faqat ${fmtQty(p.qty)} ${p.unit} bor`); return;
            }
            if (cur) cur.qty += 1;
            else cart[p.id] = { id:p.id, name:p.name, price_sum:p.price_sum,
                                qty:1, unit:p.unit, max:p.qty };
            updateCartBar();
            // Kartadagi badge yangilash uchun ro'yxatni qayta chizamiz (joriy sahifa)
            refreshPOSBadges();
            toast(`✅ ${p.name}`);
        }

        function refreshPOSBadges() {
            // faqat badge'larni yangilaymiz (yengil) — qayta yuklamasdan
            [...posList.children].forEach(card => {
                // name ichidagi badge ni topish murakkab; oddiy yo'l: hech narsa
            });
            // Soddalik uchun: agar qidiruv yo'q bo'lsa ham, badge faqat keyingi
            // loadPOS da yangilanadi. Bu yetarli.
        }

        function updateCartBar() {
            const ids = Object.keys(cart);
            const count = ids.reduce((s,k)=>s+cart[k].qty,0);
            const total = ids.reduce((s,k)=>s+cart[k].qty*cart[k].price_sum,0);
            const bar = $('cartBar');
            if (count > 0) {
                bar.style.display = 'flex';
                $('cartBarCount').textContent = `🛒 ${fmtQty(count)} dona`;
                $('cartBarTotal').textContent = fmtSum(total) + " so'm";
            } else {
                bar.style.display = 'none';
            }
        }

        window.openCart = function () {
            renderCart();
            updateCartClientUI();
            showScreen('screenCart');
        };

        function renderCart() {
            const box = $('cartItems');
            box.innerHTML = '';
            const ids = Object.keys(cart);
            if (!ids.length) {
                box.innerHTML = '<div class="list-status">Savat bo\'sh</div>';
            }
            ids.forEach(k => {
                const it = cart[k];
                const line = document.createElement('div');
                line.className = 'cart-line';
                line.innerHTML = `
                    <div class="ci-info">
                        <div class="ci-name">${escapeHtml(it.name)}</div>
                        <div class="ci-price">${fmtSum(it.price_sum)} × ${fmtQty(it.qty)} = ${fmtSum(it.price_sum*it.qty)} so'm</div>
                    </div>
                    <div class="qty-ctrl">
                        <button class="qty-btn" onclick="cartDec(${it.id})">−</button>
                        <span class="qty-val">${fmtQty(it.qty)}</span>
                        <button class="qty-btn" onclick="cartInc(${it.id})">+</button>
                    </div>
                    <button class="ci-remove" onclick="cartRemove(${it.id})">🗑</button>`;
                box.appendChild(line);
            });
            updateCartTotals();
        }

        window.cartInc = function (id) {
            const it = cart[id]; if (!it) return;
            if (it.qty + 1 > it.max) { toast(`Faqat ${fmtQty(it.max)} bor`); return; }
            it.qty += 1; renderCart(); updateCartBar();
        };
        window.cartDec = function (id) {
            const it = cart[id]; if (!it) return;
            it.qty -= 1; if (it.qty <= 0) delete cart[id];
            renderCart(); updateCartBar();
        };
        window.cartRemove = function (id) { delete cart[id]; renderCart(); updateCartBar(); };
        window.clearCart = function () { cart = {}; posClient = null; updateCartClientUI(); renderCart(); updateCartBar(); showScreen('screenPOS'); };

        // ── Mijoz tanlash (POS) ──────────────────────────────────────────────
        function updateCartClientUI() {
            const nameEl = $('cartClientName');
            const cashBtn = $('payCashBtn'), cardBtn = $('payCardBtn');
            const nasiyaBtn = $('payNasiyaBtn'), internalBtn = $('payInternalBtn');
            const payRow = $('cartPayRow');
            if (!nameEl) return;
            if (posClient) {
                nameEl.textContent = (posClient.is_internal ? '🏠 ' : '👤 ') + posClient.name + ' ✕';
            } else {
                nameEl.textContent = 'Tanlash ›';
            }
            const internal = posClient && posClient.is_internal;
            const credit = posClient && posClient.allow_credit && !internal;
            // «Chinor» — faqat rasxod tugmasi; aks holda naqd/karta
            if (payRow) payRow.style.display = internal ? 'none' : 'flex';
            if (internalBtn) internalBtn.style.display = internal ? 'block' : 'none';
            if (nasiyaBtn) nasiyaBtn.style.display = credit ? 'block' : 'none';
        }

        window.openClientPick = async function () {
            // Mijoz allaqachon tanlangan bo'lsa — tanlovni bekor qilamiz (✕)
            if (posClient) { posClient = null; updateCartClientUI(); return; }
            const modal = $('clientPickModal');
            const listEl = $('clientPickList');
            $('clientPickSearch').value = '';
            listEl.innerHTML = '<div class="list-status">Yuklanmoqda...</div>';
            modal.style.display = 'block';
            const { ok, data } = await apiFetch('/api/clients');
            _clientsCache = (ok && data.ok) ? (data.items || []) : [];
            renderClientPick();
        };
        window.closeClientPick = function () { $('clientPickModal').style.display = 'none'; };

        window.renderClientPick = function () {
            const listEl = $('clientPickList');
            const q = ($('clientPickSearch').value || '').toLowerCase().trim();
            const items = _clientsCache.filter(c =>
                !q || (c.shop_name || '').toLowerCase().includes(q) ||
                (c.phone || '').includes(q));
            // «Chinor» (ichki) doim tepada
            items.sort((a, b) => (b.is_internal ? 1 : 0) - (a.is_internal ? 1 : 0));
            let html = '';
            items.forEach(c => {
                const badge = c.is_internal ? '<span style="color:#7c3aed;font-weight:700;">🏠 rasxod</span>'
                    : (c.allow_credit ? '<span style="color:#f59e0b;font-weight:700;">🤝 qarzga</span>'
                    : (c.debt_sum > 0 ? `<span style="color:#dc2626;">Qarz: ${fmtSum(c.debt_sum)}</span>` : ''));
                html += `<div onclick="pickPosClient(${c.id})" style="padding:11px 8px;border-bottom:1px solid #f1f5f9;cursor:pointer;display:flex;justify-content:space-between;gap:8px;align-items:center;">
                    <span style="font-weight:600;">${escapeHtml(c.shop_name || ('Mijoz #' + c.id))}<br><span style="font-size:12px;color:#94a3b8;font-weight:400;">${escapeHtml(c.phone || '—')}</span></span>
                    <span style="font-size:12px;text-align:right;">${badge}</span>
                </div>`;
            });
            if (!items.length) html = '<div class="list-status">Mijoz topilmadi</div>';
            listEl.innerHTML = html;
        };

        window.pickPosClient = function (id) {
            const c = _clientsCache.find(x => x.id === id);
            if (!c) return;
            posClient = {
                id: c.id, name: c.shop_name || ('Mijoz #' + c.id),
                allow_credit: !!c.allow_credit, is_internal: !!c.is_internal,
            };
            updateCartClientUI();
            closeClientPick();
            toast((posClient.is_internal ? '🏠 ' : '👤 ') + posClient.name);
        };

        function cartSubtotal() {
            return Object.keys(cart).reduce((s,k)=>s+cart[k].qty*cart[k].price_sum,0);
        }
        window.updateCartTotals = function () {
            const sub = cartSubtotal();
            let disc = parseFloat($('cartDiscount').value) || 0;
            if (disc < 0) disc = 0;
            if (disc > sub) disc = sub;
            $('cartSubtotal').textContent = fmtSum(sub) + " so'm";
            $('cartTotal').textContent = fmtSum(sub - disc) + " so'm";
        };

        let _saleInProgress = false;
        function _setPayBtnsDisabled(v) {
            ['payCashBtn','payCardBtn','payClickBtn','paySplitBtn','payNasiyaBtn','payInternalBtn']
                .forEach(id => { const b = $(id); if (b) b.disabled = v; });
        }

        // Aralash to'lov modali: jami summani naqd/karta/click/qarz ga bo'lish
        window.openSplitPay = function () {
            if (!Object.keys(cart).length) { toast('Savat bo\'sh'); return; }
            const subtotal = Object.values(cart).reduce((a,c)=>a + c.price*c.qty, 0);
            const disc = parseFloat($('cartDiscount').value) || 0;
            const total = Math.max(0, subtotal - disc);
            const canDebt = posClient && posClient.allow_credit && !posClient.is_internal;
            const ov = document.createElement('div');
            ov.className = 'cl-modal-overlay';
            ov.onclick = (e) => { if (e.target === ov) ov.remove(); };
            const inp = (id, lbl) => `<div class="stat-row"><span class="lbl">${lbl}</span>` +
                `<input type="number" id="${id}" placeholder="0" inputmode="numeric" ` +
                `style="width:130px;text-align:right;border:1px solid #e5e7eb;border-radius:10px;padding:8px 9px;font-size:15px;"></div>`;
            ov.innerHTML = `<div class="cl-modal">
                <h3 style="margin:0 0 4px">🔀 Aralash to'lov</h3>
                <div style="font-size:13px;color:var(--muted);margin-bottom:10px">
                    Jami: <b>${fmtSum(total)} so'm</b> — qismlarga bo'ling</div>
                ${inp('spCash','💵 Naqd')}
                ${inp('spCard','💳 Karta')}
                ${inp('spClick','📲 Click')}
                ${canDebt ? inp('spDebt','🤝 Qarzga') :
                    '<div style="font-size:12px;color:var(--muted);margin:6px 0">Qarz qismi uchun nasiya ruxsatli mijoz tanlang</div>'}
                <div class="stat-row"><span class="lbl">Kiritildi</span><span class="val" id="spSum">0 so'm</span></div>
                <div class="stat-row"><span class="lbl">Qoldi</span><span class="val" id="spLeft">${fmtSum(total)} so'm</span></div>
                <button id="spOk" disabled style="width:100%;padding:13px;margin-top:12px;background:var(--primary);color:#fff;border:none;border-radius:14px;font-size:15px;font-weight:700;cursor:pointer;opacity:.5">To'lash</button>
            </div>`;
            document.body.appendChild(ov);
            const val = (id) => { const e = ov.querySelector('#'+id); return e ? (parseFloat(e.value)||0) : 0; };
            const recalc = () => {
                const s = val('spCash')+val('spCard')+val('spClick')+val('spDebt');
                ov.querySelector('#spSum').textContent = fmtSum(s) + " so'm";
                const left = total - s;
                const leftEl = ov.querySelector('#spLeft');
                leftEl.textContent = fmtSum(left) + " so'm";
                leftEl.style.color = Math.abs(left) < 1 ? '#16a34a' : '#dc2626';
                const ok = ov.querySelector('#spOk');
                ok.disabled = Math.abs(left) >= 1;
                ok.style.opacity = ok.disabled ? '.5' : '1';
            };
            ov.querySelectorAll('input').forEach(e => e.addEventListener('input', recalc));
            ov.querySelector('#spOk').addEventListener('click', () => {
                const split = { cash: val('spCash'), card: val('spCard'),
                                click: val('spClick'), debt: val('spDebt') };
                ov.remove();
                finishSale('split', split);
            });
        };

        // mode: 'cash' | 'card' | 'click' | 'split' | 'nasiya' | 'internal'
        window.finishSale = async function (mode, split) {
            if (_saleInProgress) return;
            const ids = Object.keys(cart);
            if (!ids.length) { toast('Savat bo\'sh'); return; }
            if (mode === 'nasiya' && !posClient) { toast('Avval mijoz tanlang'); return; }
            if (mode === 'split' && split && split.debt > 0 && !posClient) { toast('Qarz qismi uchun mijoz tanlang'); return; }
            _saleInProgress = true;
            _setPayBtnsDisabled(true);
            $('cartStatus').textContent = '⏳ Sotuv saqlanmoqda...';
            const items = ids.map(k => ({ product_id: cart[k].id, qty: cart[k].qty }));
            let disc = parseFloat($('cartDiscount').value) || 0;
            const body = {
                initData: (tg && tg.initData) || '',
                items, discount_sum: disc,
            };
            if (mode === 'internal') {
                body.is_internal = true;
                if (posClient) body.client_id = posClient.id;
            } else if (mode === 'nasiya') {
                body.is_nasiya = true;
                body.client_id = posClient.id;
            } else if (mode === 'split' && split) {
                body.payment = 'split';
                body.paid_cash = split.cash; body.paid_card = split.card;
                body.paid_other = split.click; body.debt_sum = split.debt;
                if (posClient) body.client_id = posClient.id;
            } else {
                body.payment = mode;
                if (posClient) body.client_id = posClient.id;
            }
            try {
                const { ok, status, data } = await apiFetch('/api/sale', {
                    method: 'POST',
                    body: JSON.stringify(body)
                });
                if (!ok || !data.ok) {
                    $('cartStatus').textContent = '❌ ' + (data.error || ('Xato '+status));
                } else {
                    cart = {};
                    $('cartDiscount').value = '';
                    posClient = null; updateCartClientUI();
                    renderCart(); updateCartBar();
                    const kind = data.is_internal ? 'Rasxod' : (data.is_nasiya ? 'Nasiya' : 'Sotuv');
                    const rno = data.receipt_no || ('#' + data.sale_id);
                    $('cartStatus').textContent =
                        `✅ ${kind} ${rno} yakunlandi! Jami: ${fmtSum(data.total_sum)} so'm`;
                    if (tg && tg.showPopup) {
                        tg.showPopup({ title:`✅ ${kind} yakunlandi`,
                            message:`Chek № ${rno}\nJami: ${fmtSum(data.total_sum)} so'm` +
                                    (data.is_nasiya?`\n🤝 Qarzga yozildi`:'') +
                                    (data.is_internal?`\n🔻 Ichki rasxod (tannarxda)`:'') +
                                    (data.change_sum>0?`\nQaytim: ${fmtSum(data.change_sum)} so'm`:''),
                            buttons:[{type:'close'}] });
                    }
                    setTimeout(()=>{ showScreen('screenPOS'); loadPOS(true); }, 1400);
                }
            } catch (err) {
                $('cartStatus').textContent = 'Tarmoq xatosi: ' + (err.message||err);
            }
            _setPayBtnsDisabled(false);
            _saleInProgress = false;
        };

        // POS qidiruv
        if (posSearch) {
            posSearch.addEventListener('input', () => {
                posSearchClear.style.display = posSearch.value ? 'block':'none';
                clearTimeout(_posSearchTimer);
                _posSearchTimer = setTimeout(()=>{ _posQuery = posSearch.value.trim(); loadPOS(true); }, 450);
            });
            posSearchClear.addEventListener('click', ()=>{
                posSearch.value=''; posSearchClear.style.display='none'; _posQuery=''; loadPOS(true);
            });
        }

        // ================================================================
        // KAMERA SKANER — html5-qrcode (QR + 1D shtrix-kod)
        //   Telegram native skaneri faqat QR o'qiydi; bu kutubxona EAN-13,
        //   UPC, Code-128 va boshqalarni ham o'qiydi (iPhone'da ham).
        // ================================================================
        let _html5qr = null;
        let _scanTarget = 'product';
        let _scanBusy = false;
        let _starting = false;   // ikki marta ochilishdan saqlaydi

        window.scanInto = async function (target) {
            _scanTarget = target || 'product';
            if (_starting) return;   // allaqachon ishga tushmoqda
            // 1) QURILMANING O'Z skaneri (BarcodeDetector) — eng tezi.
            //    U tizim darajasida ishlaydi, kodni deyarli bir zumda o'qiydi
            //    va nazorat raqamini (checksum) o'zi tekshiradi. Shuning uchun
            //    JS kutubxonalaridagidek "ikki marta bir xil o'qilsin" degan
            //    kutish ham kerak emas.
            if (typeof window.BarcodeDetector === 'function') {
                if (await startNativeScan()) return;
            }
            if (window.Quagga) {
                startQuaggaScan();           // zaxira: 1D (web-worker)
            } else if (window.Html5Qrcode) {
                startCameraScan();           // zaxira: html5-qrcode
            } else if (tg && tg.showScanQrPopup) {
                tgQrFallback();              // oxirgi zaxira: Telegram QR
            } else {
                toast('Skaner mavjud emas. Internetni tekshiring.');
            }
        };

        // Kod o'qilgach — hamma skanerlar uchun BITTA yakuniy yo'l.
        function _acceptScan(code) {
            if (_scanBusy) return;
            _scanBusy = true;
            if (tg && tg.HapticFeedback) {
                try { tg.HapticFeedback.impactOccurred('medium'); } catch(_){}
            }
            $('scanHint').textContent = '✅ O\'qildi: ' + code;
            _applyScan(code);
            stopScan();
        }

        // ================================================================
        // BarcodeDetector — brauzerga o'rnatilgan skaner (Android/Chrome)
        // ================================================================
        let _natStream = null, _natTimer = null, _natVideo = null;

        // Qaysi formatlarni o'qiymiz. Do'kondagi tovarlarda asosan EAN-13
        // bo'ladi, lekin ichki kodlar Code-128/ITF bo'lishi mumkin.
        const _NAT_FORMATS = ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128',
                              'code_39', 'code_93', 'itf', 'codabar', 'qr_code'];

        async function startNativeScan() {
            _starting = true;
            _scanBusy = false;
            let detector;
            try {
                let formats = _NAT_FORMATS;
                if (window.BarcodeDetector.getSupportedFormats) {
                    const have = await BarcodeDetector.getSupportedFormats();
                    formats = _NAT_FORMATS.filter(f => have.indexOf(f) >= 0);
                    if (!formats.length) throw new Error('format qo\'llanmaydi');
                }
                detector = new BarcodeDetector({ formats: formats });
            } catch (_) {
                _starting = false;
                return false;            // zaxira usulga o'tiladi
            }

            const overlay = $('scanOverlay');
            overlay.classList.add('active');
            $('scanHint').textContent = 'Kamera ishga tushmoqda...';
            const reader = document.getElementById('reader');
            try { if (reader) reader.innerHTML = ''; } catch(_) {}

            try {
                _natStream = await navigator.mediaDevices.getUserMedia({
                    audio: false,
                    video: {
                        facingMode: { ideal: 'environment' },
                        width:  { ideal: 1280 },
                        height: { ideal: 720 }
                    }
                });
            } catch (e) {
                _starting = false;
                _stopNative();
                // Kameraga ruxsat berilmagan bo'lsa boshqa skaner ham
                // ochilmaydi — sababni darrov aytamiz.
                if (/permission|NotAllowed/i.test((e && e.name) + (e && e.message))) {
                    $('scanHint').textContent = '⚠️ ' + _scanErrMsg(e);
                    return true;
                }
                overlay.classList.remove('active');
                return false;
            }

            const video = document.createElement('video');
            video.setAttribute('playsinline', '');   // iOS to'liq ekranga o'tmasin
            video.setAttribute('muted', '');
            video.muted = true;
            video.srcObject = _natStream;
            reader.appendChild(video);
            _natVideo = video;
            try { await video.play(); } catch(_) {}
            _applyTrackTuning();                     // uzluksiz fokus
            _starting = false;
            $('scanHint').textContent = 'Kodni ramka ichiga joylang';

            // 100 ms — sekundiga ~10 marta o'qish urinishi. Bundan tez-tez
            // chaqirish foyda bermaydi (kamera kadri baribir shuncha keladi),
            // lekin batareyani yeydi.
            _natTimer = setInterval(async () => {
                if (_scanBusy || !_natVideo || _natVideo.readyState < 2) return;
                try {
                    const found = await detector.detect(_natVideo);
                    if (found && found.length) {
                        const code = (found[0].rawValue || '').trim();
                        if (code) _acceptScan(code);
                    }
                } catch (_) { /* kadr o'qilmadi — keyingisida urinamiz */ }
            }, 100);
            return true;
        }

        function _stopNative() {
            if (_natTimer) { clearInterval(_natTimer); _natTimer = null; }
            if (_natVideo) {
                try { _natVideo.pause(); } catch(_) {}
                try { _natVideo.srcObject = null; } catch(_) {}
                _natVideo = null;
            }
            if (_natStream) {
                try { _natStream.getTracks().forEach(t => t.stop()); } catch(_) {}
                _natStream = null;
            }
        }

        // ================================================================
        // QUAGGA2 — tezkor 1D shtrix-kod skaneri (ko'p-yadroli web-worker)
        // ================================================================
        let _quaggaOn = false;
        let _lastCode = null, _lastCount = 0;

        // Do'konda ikki xil kod ishlatiladi:
        //   • zavod kodi — 2071 tasining HAMMASI 13 xonali (EAN-13)
        //   • o'z yorlig'i — 5 xonali SKU (= tovar id'si; 6902/6904 tovar)
        // Qolgan 6 xil o'qigich har kadrda bekorga ishlab, tahlilni
        // sekinlashtirardi. Endi ikkitasi qoladi. Agar kod 6 soniyada
        // o'qilmasa (boshqa turdagi yorliq bo'lishi mumkin) — skaner o'zi
        // to'liq ro'yxatga o'tadi, ya'ni hech qanday kod "o'qilmay qolmaydi".
        // Code-39 ham shu yerda: yorliq turini ko'z bilan 100% ajratib
        // bo'lmaydi (Code-128 ga juda o'xshaydi). Uchtasi bilan ham tahlil
        // 8 tasidagidan ancha yengil, lekin "umuman o'qimadi" holati bo'lmaydi.
        //
        // DIQQAT: ro'yxat SKANER ISHLAYOTGANDA o'zgartirilmaydi. Ilgari
        // "4 soniyada o'qilmasa qolgan turlarni ham qo'shamiz" degan qoida
        // bor edi va u kutubxonani QAYTA ishga tushirardi — ekran bir zumga
        // qorayib, kamera o'zidan o'zi qayta ochilardi. Foydasidan ko'ra
        // xalaqiti ko'p bo'ldi (2026-08-07, foydalanuvchi shikoyati).
        const _FAST_READERS = ["ean_reader", "code_128_reader", "code_39_reader"];
        let _widenTimer = null;   // endi faqat MASLAHAT matni uchun

        // Skaner har kadrda kanvasdan piksellarni o'qiydi (`getImageData`).
        // Brauzer kanvasni odatda videokartada saqlaydi va har o'qishda uni
        // xotiraga qaytarib olish KATTA sekinlik beradi (ayniqsa Safari —
        // iPhone). `willReadFrequently` bayrog'i brauzerga "bu kanvas tez-tez
        // o'qiladi" deb aytadi va u xotirada saqlanadi.
        //
        // Kutubxona ichidagi kanvaslarga bayroqni bevosita qo'ya olmaymiz,
        // shuning uchun `getContext` ni bir marta o'raymiz.
        let _canvasPatched = false;
        function _speedUpCanvasReads() {
            if (_canvasPatched) return;
            _canvasPatched = true;
            try {
                const orig = HTMLCanvasElement.prototype.getContext;
                HTMLCanvasElement.prototype.getContext = function (type, attrs) {
                    if (type === '2d' && (attrs === undefined || attrs === null)) {
                        return orig.call(this, type, { willReadFrequently: true });
                    }
                    return orig.call(this, type, attrs);
                };
            } catch (_) {}
        }

        function startQuaggaScan() {
            _speedUpCanvasReads();
            if (_starting) return;
            _starting = true;
            _scanBusy = false;
            _lastCode = null; _lastCount = 0;
            const overlay = $('scanOverlay');
            overlay.classList.add('active');
            $('scanHint').textContent = 'Kamera ishga tushmoqda...';
            const reader = document.getElementById('reader');
            try { if (reader) reader.innerHTML = ''; } catch(_) {}

            Quagga.init({
                inputStream: {
                    name: "Live",
                    type: "LiveStream",
                    target: reader,
                    constraints: {
                        facingMode: "environment",
                        width:  { ideal: 1280 },
                        height: { ideal: 720 }
                    },
                    // Faqat markaziy tasma skanerlanadi — tezroq va aniqroq.
                    // Tasma past bo'lsa kamroq piksel tahlil qilinadi; EAN-13
                    // uchun balandlik shart emas, kenglik muhim.
                    area: { top: "30%", right: "6%", left: "6%", bottom: "30%" }
                },
                locator: { patchSize: "medium", halfSample: true },
                numOfWorkers: Math.max(2, (navigator.hardwareConcurrency || 4)),
                // Sekundiga necha marta tahlil. 10 — kutubxonaning standarti;
                // bitta o'qigich bilan telefon 25 tasini ham bemalol tortadi,
                // ya'ni kod kadrga tushishi bilan o'qiladi.
                frequency: 25,
                decoder: { readers: _FAST_READERS },
                locate: true
            }, function (err) {
                if (err) {
                    _starting = false;
                    // Quagga ishlamasa — html5-qrcode'ga o'tamiz
                    if (window.Html5Qrcode) { startCameraScan(); return; }
                    $('scanHint').textContent = '⚠️ ' + _scanErrMsg(err);
                    return;
                }
                Quagga.start();
                _quaggaOn = true;
                _starting = false;
                $('scanHint').textContent = 'Kodni ramka ichiga joylang';
                // FOKUS: buni qo'ymasak kamera yaqindagi kodni xira olib
                // turaveradi va u HECH QACHON o'qilmaydi — "skaner sekin"
                // degan tuyg'uning asosiy sababi shu edi.
                setTimeout(_applyTrackTuning, 350);
                setTimeout(_applyTrackTuning, 1200);   // ba'zi qurilmalar kech tayyor
            });

            Quagga.offDetected(_onQuaggaDetected);
            Quagga.onDetected(_onQuaggaDetected);

            // Uzoq o'qilmasa — kamerani QAYTA ISHGA TUSHIRMAYMIZ, faqat
            // maslahat beramiz. Amalda kod o'qilmasligining sababi deyarli
            // doim masofa yoki yorug'lik bo'ladi.
            clearTimeout(_widenTimer);
            _widenTimer = setTimeout(function () {
                if (_scanBusy || !_quaggaOn) return;
                $('scanHint').textContent =
                    '💡 Kodni 10-15 sm masofada, yorug\'roq joyda tuting';
            }, 5000);
        }

        // EAN-13 / EAN-8 / UPC-A nazorat raqami (mod-10). To'g'ri chiqsa kod
        // deyarli aniq to'g'ri o'qilgan — tasodifiy xato o'qishda bu raqam
        // mos kelmaydi.
        //
        // Nima uchun kerak: iPhone'da qurilmaning o'z skaneri (BarcodeDetector)
        // YO'Q, shuning uchun Quagga ishlaydi. Uning xato o'qishidan saqlanish
        // uchun "bir xil kod ikki marta o'qilsin" deb kutilardi — aynan shu
        // sekinlik sezilardi. Nazorat raqami to'g'ri bo'lsa kutish shart emas.
        function _checksumOk(code) {
            if (!/^\d+$/.test(code)) return false;
            if (code.length !== 13 && code.length !== 8 && code.length !== 12)
                return false;
            const d = code.split('').map(Number);
            const check = d.pop();
            let sum = 0;
            // Oxiridan boshlab: 3, 1, 3, 1 ... koeffitsiyentlar
            for (let i = d.length - 1, k = 3; i >= 0; i--, k = (k === 3 ? 1 : 3)) {
                sum += d[i] * k;
            }
            return ((10 - (sum % 10)) % 10) === check;
        }

        function _onQuaggaDetected(result) {
            if (_scanBusy) return;
            const code = result && result.codeResult && result.codeResult.code;
            if (!code) return;
            // Darrov qabul qilinadigan holatlar (kutmasdan):
            //   • nazorat raqami to'g'ri EAN/UPC
            //   • do'konning O'Z yorlig'i — 5 xonali SKU (tovar id'si).
            //     Code-128 ning o'z nazorat belgisi bor va uni kutubxona
            //     allaqachon tekshirib bergan, shuning uchun ikkinchi
            //     o'qishni kutish shart emas.
            if (_checksumOk(code) || /^\d{5}$/.test(code)) {
                _acceptScan(code); return;
            }
            // Aks holda (Code-128, ITF va h.k.) eskicha: bir xil kod 2 marta
            // ketma-ket o'qilsa qabul qilinadi.
            if (code === _lastCode) {
                _lastCount += 1;
            } else {
                _lastCode = code; _lastCount = 1;
            }
            if (_lastCount < 2) return;
            _acceptScan(code);
        }

        function _applyScan(code) {
            const c = (code || '').trim();
            if (!c) return;
            // URL/qo'shimcha bo'lsa — oxirgi qismni olamiz
            const cleaned = c.replace(/^.*[\/=]/, '').trim() || c;
            if (typeof _scanTarget === 'string' && _scanTarget.startsWith('field:')) {
                // Forma maydoniga yozamiz (masalan shtrix-kod)
                const fid = _scanTarget.slice(6);
                const el = $(fid);
                if (el) el.value = cleaned;
            } else if (_scanTarget === 'client') {
                // Mijoz katalogi — kodni qidiruvga qo'yib, ro'yxatni yangilaymiz.
                const inp = $('clCatalogSearch');
                if (inp) inp.value = cleaned;
                // Kategoriya filtri O'CHIRILADI. Aks holda mijoz biror bo'limni
                // tanlab qo'ygan bo'lsa, boshqa bo'limdagi tovar skanerlanganda
                // ro'yxat BO'SH chiqadi: server tovarni topadi, lekin filtr uni
                // olib tashlaydi (2026-08-07 da aynan shunday bo'ldi — "Oyna
                // shvabra" topilgan, ammo ekranda ko'rinmagan).
                _clCat = 0;
                if (typeof loadClientCategories === 'function') loadClientCategories();
                _clScanOpen = true;      // bitta tovar topilsa — kartochkani ochamiz
                if (typeof loadClientCatalog === 'function') loadClientCatalog();
            } else if (_scanTarget === 'pos') {
                posSearch.value = cleaned;
                posSearchClear.style.display = 'block';
                _posQuery = cleaned; loadPOS(true);
            } else {
                productSearch.value = cleaned;
                productSearchClear.style.display = 'block';
                _prodQuery = cleaned; loadProducts(true);
            }
            if (tg && tg.HapticFeedback) {
                try { tg.HapticFeedback.notificationOccurred('success'); } catch(_){}
            }
        }

        // Skanerni forma maydoniga yo'naltirish (masalan shtrix-kod)
        window.scanIntoField = function (fieldId) {
            scanInto('field:' + fieldId);
        };

        // Kamera ishga tushgach — uzluksiz fokusni track darajasida qo'llaymiz
        // (qurilma qo'llab-quvvatlasa). Bu uzoq/kichik kodda chotki yordam beradi.
        function _applyTrackTuning() {
            try {
                const video = document.querySelector('#reader video');
                const stream = video && video.srcObject;
                const track = stream && stream.getVideoTracks && stream.getVideoTracks()[0];
                if (!track || !track.applyConstraints) return;
                const caps = track.getCapabilities ? track.getCapabilities() : {};
                const adv = [];
                if (caps.focusMode && caps.focusMode.includes && caps.focusMode.includes('continuous')) {
                    adv.push({ focusMode: 'continuous' });
                }
                // Ba'zi qurilmalarda yorug'lik kamligida yordam beradigan exposure
                if (caps.exposureMode && caps.exposureMode.includes && caps.exposureMode.includes('continuous')) {
                    adv.push({ exposureMode: 'continuous' });
                }
                if (adv.length) track.applyConstraints({ advanced: adv }).catch(() => {});
            } catch (_) {}
        }

        // KICHIK, shtrix-kodga mos ramka (keng-past)
        function _qrboxFn(vw, vh) {
            let w = Math.floor(Math.min(vw, 320) * 0.48);   // ~155px
            w = Math.max(125, Math.min(w, 180));
            let h = Math.floor(Math.min(w * 0.5, vh * 0.26));
            h = Math.max(75, h);
            return { width: w, height: h };
        }

        function _scanFormats() {
            return window.Html5QrcodeSupportedFormats ? [
                Html5QrcodeSupportedFormats.QR_CODE,
                Html5QrcodeSupportedFormats.EAN_13,
                Html5QrcodeSupportedFormats.EAN_8,
                Html5QrcodeSupportedFormats.UPC_A,
                Html5QrcodeSupportedFormats.UPC_E,
                Html5QrcodeSupportedFormats.UPC_EAN_EXTENSION,
                Html5QrcodeSupportedFormats.CODE_128,
                Html5QrcodeSupportedFormats.CODE_39,
                Html5QrcodeSupportedFormats.CODE_93,
                Html5QrcodeSupportedFormats.ITF,
                Html5QrcodeSupportedFormats.CODABAR,
            ] : undefined;
        }

        function _scanErrMsg(e) {
            let msg = (e && e.message) || String(e || '');
            if (/permission|NotAllowed/i.test(msg))
                return 'Kameraga ruxsat berilmadi. Sozlamalar → Telegram → Kamera ni yoqing.';
            if (/NotFound|Requested device|no camera/i.test(msg))
                return 'Kamera topilmadi.';
            if (/secure|https|NotSupported|getUserMedia|not supported/i.test(msg))
                return 'Bu qurilmada jonli kamera qo\'llab-quvvatlanmaydi.';
            return msg || 'Kamera ochilmadi.';
        }

        // Bitta urinish — HAR DOIM yangi Html5Qrcode obyekti bilan
        // ("already under transition" xatosini oldini olish uchun).
        async function _tryStart(cameraArg, cfg, onOk) {
            // Oldingi muvaffaqiyatsiz urinishdan qolgan video/elementlarni tozalaymiz
            try { const r = document.getElementById('reader'); if (r) r.innerHTML = ''; } catch(_) {}
            const inst = new Html5Qrcode("reader", {
                formatsToSupport: _scanFormats(), verbose: false
            });
            await inst.start(cameraArg, cfg, onOk, () => {});
            _html5qr = inst;   // muvaffaqiyatli — saqlaymiz
        }

        async function startCameraScan() {
            if (_starting) return;
            _starting = true;
            _scanBusy = false;
            const overlay = $('scanOverlay');
            overlay.classList.add('active');
            $('scanHint').textContent = 'Kamera ishga tushmoqda...';

            const onOk = (decodedText) => { _acceptScan(decodedText); };

            const config = {
                fps: 20,
                qrbox: _qrboxFn,
                aspectRatio: 1.7777,
                disableFlip: true,
                experimentalFeatures: { useBarCodeDetectorIfSupported: true },
            };
            // Yuqori aniqlik — YUMSHOQ (ideal) konstraint, qo'llab-quvvatlanmasa
            // ham xato bermaydi. advanced focusMode'ni START'da BERMAYMIZ —
            // u iOS'da kamerani ochilishiga to'sqinlik qiladi; fokusni kamera
            // ochilgach _applyTrackTuning() orqali qo'llaymiz.
            const camHi = { facingMode: "environment",
                            width: { ideal: 1280 }, height: { ideal: 720 } };

            const onStarted = () => {
                _starting = false;
                $('scanHint').textContent = 'Kodni ramka ichiga joylang';
                _applyTrackTuning();   // uzluksiz fokus (qurilma qo'llasa)
            };

            // 1) facingMode + yumshoq aniqlik
            try {
                await _tryStart(camHi, config, onOk);
                onStarted(); return;
            } catch (e1) { /* keyingi urinish */ }

            // 2) kameralar ro'yxatidan orqa kamera (deviceId)
            try {
                const cams = await Html5Qrcode.getCameras();
                if (cams && cams.length) {
                    let cam = cams.find(c => /back|rear|orqa|environment/i.test(c.label))
                              || cams[cams.length - 1];
                    await _tryStart(cam.id, config, onOk);
                    onStarted(); return;
                }
            } catch (e2) { /* keyingi urinish */ }

            // 3) eng oddiy rejim
            try {
                await _tryStart({ facingMode: "environment" },
                    { fps: 15, qrbox: _qrboxFn, disableFlip: true,
                      experimentalFeatures: { useBarCodeDetectorIfSupported: true } },
                    onOk);
                onStarted(); return;
            } catch (e3) {
                _starting = false;
                $('scanHint').textContent = '⚠️ ' + _scanErrMsg(e3);
            }
        }

        // Kamerani HAR QANDAY holatda bo'shatadi.
        //
        // Nima uchun alohida kerak: `#reader` ichini `innerHTML=''` bilan
        // tozalash video elementini olib tashlaydi, lekin KAMERA OQIMINI
        // to'xtatmaydi — oqimga endi hech kim ishora qilmaydi, u esa ishlab
        // turaveradi va telefonda kamera chirog'i yonib qolaveradi.
        // Shuning uchun avval treklar to'xtatiladi, keyin element olinadi.
        function _releaseCameras() {
            try {
                document.querySelectorAll('#reader video').forEach(function (v) {
                    const s = v.srcObject;
                    if (s && s.getTracks) {
                        try { s.getTracks().forEach(function (t) { t.stop(); }); } catch(_) {}
                    }
                    try { v.pause(); } catch(_) {}
                    try { v.srcObject = null; } catch(_) {}
                });
            } catch (_) {}
        }

        window.stopScan = function () {
            const overlay = $('scanOverlay');
            const inst = _html5qr;
            _starting = false;
            clearTimeout(_widenTimer);
            _stopNative();          // qurilma skaneri ishlayotgan bo'lsa
            // Quagga2 ni to'xtatamiz (agar ishlayotgan bo'lsa)
            if (_quaggaOn && window.Quagga) {
                try { Quagga.offDetected(_onQuaggaDetected); } catch(_) {}
                try { Quagga.stop(); } catch(_) {}
                _quaggaOn = false;
            }
            _releaseCameras();
            const r = document.getElementById('reader');
            try { if (r) r.innerHTML = ''; } catch(_) {}
            const finish = () => {
                _html5qr = null;
                overlay.classList.remove('active');
            };
            if (inst) {
                let running = true;
                try { running = (inst.getState && inst.getState() === 2); } catch(_) {}
                if (running) {
                    inst.stop()
                        .then(() => { try { inst.clear(); } catch(_){} finish(); })
                        .catch(() => finish());
                } else {
                    try { inst.clear(); } catch(_) {}
                    finish();
                }
            } else {
                finish();
            }
        };

        // Ilova fonga o'tsa yoki yopilsa — kamerani o'chiramiz.
        //
        // Nima uchun: foydalanuvchi skanerni ochib, "Yopish"ni bosmasdan
        // Mini App'dan chiqib ketishi mumkin (orqaga surish, boshqa ilovaga
        // o'tish). O'shanda kamera ishlab turaverardi va telefonda kamera
        // chirog'i yonib qolardi.
        function _scanOpen() {
            const ov = document.getElementById('scanOverlay');
            return !!(ov && ov.classList.contains('active'));
        }
        document.addEventListener('visibilitychange', function () {
            if (!document.hidden) return;
            if (_scanOpen()) {
                try { stopScan(); } catch (_) { _releaseCameras(); }
            }
            // Surat olish oynasi ham ochiq qolmasin
            const cam = document.getElementById('camOverlay');
            if (cam && cam.classList.contains('active')) {
                try { closePhotoCam(); } catch (_) {}
            }
        });
        // `pagehide` — sahifa butunlay yopilganda (visibilitychange
        // ba'zi brauzerlarda kelmaydi).
        window.addEventListener('pagehide', function () {
            _releaseCameras();
            try { closePhotoCam(); } catch (_) {}
        });

        function tgQrFallback() {
            try {
                tg.showScanQrPopup({ text: "QR kodni kameraga tuting" },
                    function (text) {
                        const code = (text || '').trim();
                        if (!code) return false;
                        try { tg.closeScanQrPopup(); } catch (_) {}
                        _applyScan(code);
                        return true;
                    });
            } catch (e) {
                toast('Skaner ochilmadi: ' + (e.message || e));
            }
        }

        // Yengil toast
        function toast(msg) {
            let t = $('miniToast');
            if (!t) {
                t = document.createElement('div'); t.id='miniToast';
                t.style.cssText='position:fixed;left:50%;bottom:90px;transform:translateX(-50%);'+
                    'background:rgba(0,0,0,0.82);color:#fff;padding:9px 16px;border-radius:12px;'+
                    'font-size:14px;z-index:200;transition:opacity .3s;pointer-events:none;';
                document.body.appendChild(t);
            }
            t.textContent = msg; t.style.opacity='1';
            clearTimeout(t._tm); t._tm = setTimeout(()=>{ t.style.opacity='0'; }, 1300);
        }

        // ================================================================
        // FOYDALANUVCHILAR (admin)
        // ================================================================
        window.openClients = async function () {
            showScreen('screenStats');
            const body = $('statsBody');
            body.innerHTML = '<div class="list-status">Yuklanmoqda...</div>';
            const { ok, status, data } = await apiFetch('/api/clients');
            if (!ok || !data.ok) {
                body.innerHTML = '<div class="list-status">' + (status===403?'Ruxsat yo\'q':status===401?'🔒 Qaytadan kiring.':'Xato: '+(data.error||status)) + '</div>';
                return;
            }
            let html = `<div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;">
                <button class="list-back" onclick="showScreen('screenAdmin')">← Orqaga</button>
                <h2 style="font-size:18px;font-weight:800;">👥 Foydalanuvchilar (${data.total})</h2>
            </div>`;
            (data.items || []).forEach(c => {
                const debtCls = c.debt_sum > 0 ? 'val-red' : 'val-green';
                const debtTxt = c.debt_sum > 0 ? fmtSum(c.debt_sum)+" so'm" : '✅ Qarzsiz';
                const typeTxt = c.is_internal ? '🏠' : (c.client_type === 'optom' ? '📦' : '🛍️');
                // «Chinor» ichki mijoziga qarz tugmasi qo'yilmaydi
                let creditRow = '';
                if (!c.is_internal) {
                    const on = !!c.allow_credit;
                    creditRow = `<div class="stat-row"><span class="lbl">🤝 Qarzga savdo</span>
                        <button onclick="toggleClientCredit(${c.id}, ${on ? 0 : 1})"
                          style="border:none;border-radius:8px;padding:5px 12px;cursor:pointer;font-weight:700;font-size:13px;color:#fff;background:${on ? '#16a34a' : '#94a3b8'};">
                          ${on ? '✓ Ruxsat' : 'Yopiq'}</button></div>`;
                }
                const nameExtra = c.is_internal ? ' <span style="color:#7c3aed;font-size:12px;">(ichki rasxod)</span>' : '';
                html += `<div class="stat-block">
                    <div class="stat-row"><span class="lbl">${typeTxt} ${escapeHtml(c.shop_name)}${nameExtra}</span><span class="val ${debtCls}">${debtTxt}</span></div>
                    <div class="stat-row"><span class="lbl">📱 Telefon</span><span class="val">${escapeHtml(c.phone||'—')}</span></div>
                    ${creditRow}
                    <div class="stat-row"><span class="lbl">📅 Qo'shilgan</span><span class="val">${c.created_at}</span></div>
                </div>`;
            });
            if (!(data.items||[]).length) html += '<div class="list-status">Foydalanuvchilar yo\'q</div>';
            body.innerHTML = html;
        };

        window.toggleClientCredit = async function (id, allow) {
            const { ok, data } = await apiFetch('/api/client/credit', {
                method: 'POST',
                body: JSON.stringify({ client_id: id, allow: !!allow }),
            });
            if (ok && data.ok) {
                toast(allow ? '✓ Qarzga ruxsat berildi' : 'Qarz yopildi');
                openClients();  // ro'yxatni yangilaymiz
            } else {
                toast('❌ ' + ((data && data.error) || 'Xato'));
            }
        };
        window.openOrders = async function () {
            showScreen('screenStats');
            const body = $('statsBody');
            body.innerHTML = '<div class="list-status">Yuklanmoqda...</div>';
            const { ok, status, data } = await apiFetch('/api/orders');
            if (!ok || !data.ok) {
                body.innerHTML = '<div class="list-status">' + (status===403?'Ruxsat yo\'q':status===401?'🔒 Qaytadan kiring.':'Xato: '+(data.error||status)) + '</div>';
                return;
            }
            let html = `<div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;">
                <button class="list-back" onclick="showScreen('screenAdmin')">← Orqaga</button>
                <h2 style="font-size:18px;font-weight:800;">📋 Buyurtmalar (${data.total})</h2>
            </div>`;
            const ORDER_ST = {
                accepted: '✅ Qabul qilindi', on_way: "🚚 Yo'lda",
                delivered: '📦 Yetkazildi', confirmed: '🎉 Tasdiqladi',
                cancelled: '❌ Bekor qilindi',
            };
            (data.items || []).forEach(o => {
                const st = ORDER_ST[o.status] || ('⏳ ' + (o.status || ''));
                html += `<div class="stat-block">
                    <div class="stat-row"><span class="lbl">#${o.id} ${escapeHtml(o.shop_name||'')}</span><span class="val ${o.status==='cancelled'?'val-red':''}">${st}</span></div>
                    <div class="stat-row"><span class="lbl">💰 Summa</span><span class="val val-green">${fmtSum(o.total)} so'm</span></div>
                    <div class="stat-row"><span class="lbl">📅 Sana</span><span class="val">${o.created_at}</span></div>
                </div>`;
            });
            if (!(data.items||[]).length) html += '<div class="list-status">Buyurtmalar yo\'q</div>';
            body.innerHTML = html;
        };
        // Xodim rollari — server qabul qiladigan qiymatlar bilan bir xil.
        const STAFF_ROLES = [
            ['full',     '👑 To\'liq admin'],
            ['products', '📦 Mahsulotlar'],
            ['cashier',  '💵 Kassir'],
            ['stats',    '📊 Hisobotlar'],
        ];
        const _roleLabel = r =>
            (STAFF_ROLES.find(x => x[0] === r) || ['', '🔰 Cheklangan'])[1];

        window.openStaff = async function () {
            showScreen('screenStats');
            const body = $('statsBody');
            body.innerHTML = '<div class="list-status">Yuklanmoqda...</div>';
            const { ok, status, data } = await apiFetch('/api/staff');
            if (!ok || !data.ok) {
                body.innerHTML = '<div class="list-status">' + (status===403?'Ruxsat yo\'q':status===401?'🔒 Qaytadan kiring.':'Xato: '+(data.error||status)) + '</div>';
                return;
            }
            const items = data.items || [];
            const canManage = !!data.can_manage;
            const active = items.filter(a => !a.pending);
            const pending = items.filter(a => a.pending);

            let html = `<div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;">
                <button class="list-back" onclick="showScreen('screenAdmin')">← Orqaga</button>
                <h2 style="font-size:18px;font-weight:800;">👔 Xodimlar (${active.length})</h2>
            </div>`;

            // ── Qo'shish formasi (faqat to'liq adminlarga) ──────────────
            if (canManage) {
                html += `<div class="stat-block">
                    <div class="stat-row"><span class="lbl">➕ Yangi xodim</span></div>
                    <input type="tel" id="stPhone" placeholder="Telefon: +998 90 123 45 67"
                           inputmode="tel" autocomplete="off"
                           style="width:100%;border:1px solid #e5e7eb;border-radius:10px;padding:10px 12px;font-size:15px;margin-top:8px;">
                    <input type="text" id="stName" placeholder="Ism (ixtiyoriy)"
                           autocomplete="off"
                           style="width:100%;border:1px solid #e5e7eb;border-radius:10px;padding:10px 12px;font-size:15px;margin-top:8px;">
                    <select id="stRole"
                            style="width:100%;border:1px solid #e5e7eb;border-radius:10px;padding:10px 12px;font-size:15px;margin-top:8px;background:#fff;">
                        ${STAFF_ROLES.map(r => `<option value="${r[0]}">${r[1]}</option>`).join('')}
                    </select>
                    <button class="save-btn" style="margin-top:10px" onclick="addStaff()">➕ Qo'shish</button>
                    <div style="font-size:12.5px;color:var(--muted);margin-top:8px;line-height:1.5">
                        Raqamni yozib qo'shasiz. Xodim botga kirib
                        «📱 Raqamni yuborish» tugmasini bossa — hisobi
                        o'zi faollashadi.
                    </div>
                </div>`;
            }

            // ── Kutilayotganlar ─────────────────────────────────────────
            if (pending.length) {
                html += `<div class="stat-block">
                    <div class="stat-row"><span class="lbl">⏳ Kutilmoqda (${pending.length})</span>
                    <span class="val" style="font-size:12px;color:var(--muted)">botga kirmagan</span></div>`;
                pending.forEach(p => {
                    html += `<div class="stat-row">
                        <span class="lbl">📱 ${escapeHtml(p.phone)}${p.full_name ? ' · ' + escapeHtml(p.full_name) : ''}</span>
                        <span class="val">${_roleLabel(p.role)}${canManage
                            ? ` <button onclick="removeStaff(0, ${p.pending_id})"
                                   style="border:none;background:#fee;color:#c00;border-radius:8px;padding:4px 9px;margin-left:6px;cursor:pointer;">✕</button>`
                            : ''}</span>
                    </div>`;
                });
                html += '</div>';
            }

            // ── Faol xodimlar ───────────────────────────────────────────
            active.forEach(a => {
                const nomi = escapeHtml(a.full_name || a.username || ('ID ' + a.telegram_id));
                html += `<div class="stat-block">
                    <div class="stat-row"><span class="lbl">👤 ${nomi}${a.is_glavniy ? ' ⭐' : ''}</span>
                        <span class="val">${_roleLabel(a.role)}</span></div>
                    ${a.phone ? `<div class="stat-row"><span class="lbl">📱 Telefon</span><span class="val">${escapeHtml(a.phone)}</span></div>` : ''}
                    <div class="stat-row"><span class="lbl">📅 Qo'shilgan</span><span class="val">${a.created_at || '—'}</span></div>`;
                if (canManage && !a.is_glavniy) {
                    html += `<div class="stat-row"><span class="lbl">🔑 Rol</span><span class="val">
                        <select onchange="setStaffRole(${a.telegram_id}, this.value)"
                                style="border:1px solid #e5e7eb;border-radius:9px;padding:6px 8px;font-size:13.5px;background:#fff;">
                            ${STAFF_ROLES.map(r => `<option value="${r[0]}"${r[0]===a.role?' selected':''}>${r[1]}</option>`).join('')}
                        </select>
                        <button onclick="removeStaff(${a.telegram_id}, 0)"
                                style="border:none;background:#fee;color:#c00;border-radius:8px;padding:6px 10px;margin-left:6px;cursor:pointer;">🗑</button>
                    </span></div>`;
                }
                html += '</div>';
            });
            if (!active.length) html += '<div class="list-status">Xodimlar yo\'q</div>';
            body.innerHTML = html;
        };

        window.addStaff = async function () {
            const phone = ($('stPhone') || {value:''}).value.trim();
            const name  = ($('stName')  || {value:''}).value.trim();
            const role  = ($('stRole')  || {value:'full'}).value;
            if (phone.replace(/\D/g, '').length < 7) {
                toast('❌ Telefon raqamini to\'liq yozing'); return;
            }
            const { ok, data } = await apiFetch('/api/staff/add', {
                method: 'POST',
                body: JSON.stringify({ phone: phone, full_name: name, role: role })
            });
            if (ok && data.ok) {
                $('stPhone').value = ''; $('stName').value = '';
                // `promoted` — raqam bazada mijoz sifatida bor edi, shuning
                // uchun xodim kontakt yubormasdan darrov faollashdi.
                toast(data.promoted
                    ? '✅ Faollashdi — hisob darrov ochildi'
                    : '✅ Qo\'shildi — xodim botga kirsin');
                openStaff();
                return;
            }
            // Server texnik kodlar qaytaradi — ularni tushunarli qilamiz.
            const kod = (data && data.error) || '';
            const izoh = {
                'exists_admin':  'Bu raqam allaqachon xodim sifatida qo\'shilgan',
                'exists_pending':'Bu raqam allaqachon ro\'yxatda — xodim botga kirishi kerak',
                'bad_phone':     'Telefon raqami noto\'g\'ri'
            }[kod] || kod || 'Qo\'shilmadi';
            toast('❌ ' + izoh);
        };

        window.setStaffRole = async function (tgId, role) {
            const { ok, data } = await apiFetch('/api/staff/role', {
                method: 'POST',
                body: JSON.stringify({ telegram_id: tgId, role: role })
            });
            if (ok && data.ok) toast('✅ Rol o\'zgardi');
            else { toast('❌ ' + ((data && data.error) || 'Xato')); openStaff(); }
        };

        window.removeStaff = async function (tgId, pendingId) {
            if (!confirm('Xodim o\'chirilsinmi?')) return;
            const { ok, data } = await apiFetch('/api/staff/remove', {
                method: 'POST',
                body: JSON.stringify(pendingId ? { pending_id: pendingId }
                                               : { telegram_id: tgId })
            });
            if (ok && data.ok) { toast('✅ O\'chirildi'); openStaff(); }
            else toast('❌ ' + ((data && data.error) || 'Xato'));
        };
        window.openSettings = async function () {
            showScreen('screenStats');
            const body = $('statsBody');
            body.innerHTML = '<div class="list-status">Yuklanmoqda...</div>';
            const { ok, status, data } = await apiFetch('/api/settings');
            if (!ok || !data.ok) {
                body.innerHTML = '<div class="list-status">' + (status===403?'Ruxsat yo\'q':status===401?'🔒 Qaytadan kiring.':'Xato: '+(data.error||status)) + '</div>';
                return;
            }
            const s = data.settings || {};
            const toggle = (label, key, val) => `<div class="stat-row"><span class="lbl">${label}</span><label style="position:relative;display:inline-block;width:44px;height:24px;"><input type="checkbox" ${val?'checked':''} style="opacity:0;width:0;height:0;" data-key="${key}" onchange="toggleSetting(this)"><span style="position:absolute;cursor:pointer;top:0;left:0;right:0;bottom:0;background:${val?'#2481cc':'#ccc'};border-radius:12px;transition:.3s;"><span style="position:absolute;height:20px;width:20px;left:${val?'22px':'2px'};bottom:2px;background:white;border-radius:50%;transition:.3s;"></span></span></label></div>`;
            let html = `<div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;"><button class="list-back" onclick="showScreen('screenAdmin')">← Orqaga</button><h2 style="font-size:18px;font-weight:800;">⚙️ Sozlamalar</h2></div>
            <div class="stat-block"><div class="stat-row"><span class="lbl">💵 USD kursi</span><input type="number" id="usdRateInput" value="${s.usd_rate}" step="100" style="width:120px;text-align:right;border:1px solid #e5e7eb;border-radius:10px;padding:7px 9px;font-size:14px;"></div><button class="save-btn" onclick="saveUsdRate()">💾 Kursni saqlash</button></div>
            <div class="stat-block">`;
            html += toggle('📦 Optom narx', 'wholesale_enabled', s.wholesale_enabled);
            html += toggle('🛍️ Dona narx', 'dona_enabled', s.dona_enabled);
            html += toggle('🔖 Shtrix-kod', 'barcode_enabled', s.barcode_enabled);
            html += toggle('📢 Kanal e\'loni', 'channel_enabled', s.channel_enabled);
            html += toggle('📋 Mijoz buyurtmasi', 'client_orders_enabled', s.client_orders_enabled);
            html += toggle('💳 Nasiya', 'nasiya_enabled', s.nasiya_enabled);
            html += toggle('🗂 Kategoriyalar', 'categories_enabled', s.categories_enabled);
            html += toggle('🚚 Yetkazib beruvchi', 'suppliers_enabled', s.suppliers_enabled);
            html += toggle('🌐 Mini App', 'mini_app_enabled', s.mini_app_enabled);
            html += toggle('🤖 AI Konsultant', 'ai_consult_enabled', s.ai_consult_enabled);
            html += toggle('📊 AI Analitika', 'ai_analytics_enabled', s.ai_analytics_enabled);
            html += '</div><div id="settingsStatus" class="list-status"></div>';
            body.innerHTML = html;
        };
        window.toggleSetting = async function (el) {
            const key = el.dataset.key; const val = el.checked;
            const { ok } = await apiFetch('/api/settings', { method: 'POST', body: JSON.stringify({ [key]: val })});
            if (!ok) toast('❌ Xato'); else toast('✅ Saqlandi');
            const t = el.nextElementSibling;
            if (t) { t.style.background = val ? '#2481cc' : '#ccc'; const k = t.querySelector('span'); if (k) k.style.left = val ? '22px' : '2px'; }
        };
        window.saveUsdRate = async function () {
            const rate = parseFloat($('usdRateInput').value);
            if (!rate || rate <= 0) { toast('❌ Kurs noto\'g\'ri'); return; }
            const { ok } = await apiFetch('/api/settings', { method: 'POST', body: JSON.stringify({ usd_rate: rate })});
            if (ok) toast('✅ Kurs saqlandi'); else toast('❌ Xato');
        };
        window.openServices = function () {
            showScreen('screenStats'); const body = $('statsBody');
            body.innerHTML = `<div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;"><button class="list-back" onclick="showScreen('screenClient')">← Orqaga</button><h2 style="font-size:18px;font-weight:800;">🛠️ Xizmatlar</h2></div>
            <div class="stat-block" style="text-align:center;padding:30px;"><span style="font-size:48px;">📦</span><h3>Mahsulot yetkazib berish</h3><p style="color:var(--muted);margin:10px 0;">Shahrimiz bo'ylab tezkor yetkazib berish xizmati</p><p style="font-weight:700;">📞 +998 99 123 45 67</p></div>
            <div class="stat-block" style="text-align:center;padding:30px;"><span style="font-size:48px;">🔧</span><h3>Ta'mirlash xizmati</h3><p style="color:var(--muted);margin:10px 0;">Qurilmalaringizni professional ta'mirlash</p><p style="font-weight:700;">📞 +998 99 123 45 67</p></div>
            <div class="stat-block" style="text-align:center;padding:30px;"><span style="font-size:48px;">💡</span><h3>Maslahat xizmati</h3><p style="color:var(--muted);margin:10px 0;">Mutaxassislardan bepul maslahat oling</p><p style="font-weight:700;">📞 +998 99 123 45 67</p></div>`;
        };
        window.openPlaceOrder = function () {
            showScreen('screenStats'); const body = $('statsBody');
            body.innerHTML = `<div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;"><button class="list-back" onclick="showScreen('screenClient')">← Orqaga</button><h2 style="font-size:18px;font-weight:800;">🛒 Buyurtma berish</h2></div><div style="text-align:center;padding:40px 0;"><span style="font-size:64px;">📱</span><h3>Tez orada</h3><p style="color:var(--muted);margin:16px 0;">Buyurtma berish tizimi hozircha faqat Telegram bot orqali ishlaydi.</p><p style="font-weight:700;">Botda «📋 Buyurtma berish» tugmasini bosing.</p></div>`;
        };
        window.openPromotions = function () {
            showScreen('screenStats'); const body = $('statsBody');
            body.innerHTML = `<div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;"><button class="list-back" onclick="showScreen('screenClient')">← Orqaga</button><h2 style="font-size:18px;font-weight:800;">🔥 Aksiyalar</h2></div><div class="stat-block" style="text-align:center;padding:30px;background:linear-gradient(135deg,#fff5f5,#fff);"><span style="font-size:48px;">🎉</span><h3>Hozircha aksiyalar yo'q</h3><p style="color:var(--muted);margin:10px 0;">Yangi aksiyalar haqida Telegram bot orqali xabardor bo'ling.</p></div>`;
        };
        window.openContact = function () {
            showScreen('screenStats'); const body = $('statsBody');
            body.innerHTML = `<div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;"><button class="list-back" onclick="showScreen('screenClient')">← Orqaga</button><h2 style="font-size:18px;font-weight:800;">📞 Aloqa</h2></div>
            <div class="stat-block" style="text-align:center;padding:24px;"><span style="font-size:48px;">📞</span><h3>Telefon</h3><p style="font-size:20px;font-weight:800;color:var(--primary);margin:8px 0;">+998 99 123 45 67</p></div>
            <div class="stat-block" style="text-align:center;padding:24px;"><span style="font-size:48px;">📍</span><h3>Manzil</h3><p style="color:var(--muted);margin:8px 0;">Toshkent shahri, Yunusobod tumani</p></div>
            <div class="stat-block" style="text-align:center;padding:24px;"><span style="font-size:48px;">🕐</span><h3>Ish vaqti</h3><p style="color:var(--muted);margin:8px 0;">Dushanba - Shanba: 09:00 - 20:00</p><p style="color:var(--muted);">Yakshanba: Dam olish kuni</p></div>
            <div class="stat-block" style="text-align:center;padding:24px;"><span style="font-size:48px;">💬</span><h3>Ijtimoiy tarmoqlar</h3><p style="color:var(--muted);margin:8px 0;">Telegram: @chinor_bot</p><p style="color:var(--muted);">Instagram: @chinor_uz</p></div>`;
        };
        window.handleAction = function (actionName) {
            switch (actionName) {
                case 'Foydalanuvchilar': openClients(); break;
                case 'Buyurtmalar': openOrders(); break;
                case 'Xodimlar': openStaff(); break;
                case 'Sozlamalar': openSettings(); break;
                case 'Xizmatlar': openServices(); break;
                case 'Buyurtma berish': openPlaceOrder(); break;
                case 'Aksiyalar': openPromotions(); break;
                case 'Aloqa': openContact(); break;
                default: if (tg && tg.showPopup) tg.showPopup({ title: actionName, message: 'Bu bo\'lim keyingi fazada ulanadi.', buttons: [{type:'close'}] }); else alert(actionName + ' — keyingi fazada');
            }
        };

        // ================================================================
        // ESC KEY
        // ================================================================
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && screenLogin.classList.contains('active')) {
                loginInput.blur();
                passwordInput.blur();
            }
        });
