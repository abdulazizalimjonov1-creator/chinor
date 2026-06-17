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
        //    https://your-app.netlify.app/?api=https://abc.ngrok.io
        const _urlParams = new URLSearchParams(location.search);
        const API_BASE_URL = (
            _urlParams.get('api') ||
            'https://unnatural-vibes-praying.ngrok-free.dev'
        ).replace(/\/+$/, '');
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

                // --- Panelni ochamiz ---
                const u = data.user || {};
                const letter = (u.name || 'U').charAt(0).toUpperCase();
                if (data.role === 'admin') {
                    adminAvatar.textContent = letter;
                    adminName.textContent = `Xush kelibsiz, ${u.name}!`;
                    showScreen(screenAdmin);
                    loadDashboardStats();
                } else {
                    clientAvatar.textContent = letter;
                    clientName.textContent = `Xush kelibsiz, ${u.name}!`;
                    showScreen(screenClient);
                }
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

        function showProductDetail(p) {
            _curProduct = p;
            const isAdmin = (_prodRole === 'admin');
            const low = p.qty <= 0;
            let html = '';
            if (p.image_url) {
                html += `<img class="detail-img" id="detailImg" alt="" style="display:none">`;
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
            }
            $('prodDetailBody').innerHTML = html;
            if (p.image_url) setImgSrc($('detailImg'), p.image_url);
            showScreen('screenProdDetail');
        }

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
        const prodImgInput = $('prodImgInput');

        window.pickProductImage = function () {
            if (prodImgInput) prodImgInput.click();
        };
        if (prodImgInput) {
            prodImgInput.addEventListener('change', async function () {
                const file = this.files && this.files[0];
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
                    $('pfStatus').textContent = r.ok
                        ? '✅ Rasm yuklandi'
                        : ('❌ Rasm yuklanmadi: ' + r.error);
                } else {
                    // Yangi mahsulot — saqlashdan keyin yuklash uchun saqlab qo'yamiz
                    _pendingImageFile = file;
                    $('pfStatus').textContent = '🖼 Rasm tanlandi (saqlashda yuklanadi)';
                }
            });
        }

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
                if (data && data.ok) return { ok: true, url: data.image_url };
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
            body.innerHTML = `
                <div class="stat-block">
                    <h3>📅 Bugun</h3>
                    ${row('🧾 Sotuvlar', t.sale_count + ' ta')}
                    ${row('💰 Tushum', fmtSum(t.revenue_sum) + " so'm", 'val-green')}
                    ${row('✅ Foyda', fmtSum(t.profit_sum) + " so'm", 'val-green')}
                </div>
                <div class="stat-block">
                    <h3>📆 Bu oy (${data.month_label})</h3>
                    ${row('🧾 Sotuvlar', m.sale_count + ' ta')}
                    ${row('🚚 Buyurtmalar', m.order_count + ' ta')}
                    ${row('💰 Tushum', fmtSum(m.revenue_sum) + " so'm", 'val-green')}
                    ${row('📦 Xarajat', fmtSum(m.cost_sum) + " so'm")}
                    ${row('✅ Sof foyda', fmtSum(m.profit_sum) + " so'm", 'val-green')}
                </div>
                <div class="stat-block">
                    <h3>🏆 Top mahsulotlar (oy)</h3>
                    ${topHtml}
                </div>`;
        };

        // ================================================================
        // MENING HISOBIM (mijoz)
        // ================================================================
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
                </div>`;
        };

        // ================================================================
        // POS SOTUV (kassa) — savat in-memory
        // ================================================================
        let cart = {};            // { pid: {id,name,price_sum,qty,unit,max} }
        let _posPage = 0, _posQuery = '', _posLoading = false, _posSearchTimer = null;

        const posList = $('posList'), posStatus = $('posStatus'), posMore = $('posMore');
        const posSearch = $('posSearch'), posSearchClear = $('posSearchClear');

        window.openPOS = function () {
            _posQuery = ''; posSearch.value = ''; posSearchClear.style.display = 'none';
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
        window.clearCart = function () { cart = {}; renderCart(); updateCartBar(); showScreen('screenPOS'); };

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
        window.finishSale = async function (payment) {
            if (_saleInProgress) return;
            const ids = Object.keys(cart);
            if (!ids.length) { toast('Savat bo\'sh'); return; }
            _saleInProgress = true;
            $('payCashBtn').disabled = true; $('payCardBtn').disabled = true;
            $('cartStatus').textContent = '⏳ Sotuv saqlanmoqda...';
            const items = ids.map(k => ({ product_id: cart[k].id, qty: cart[k].qty }));
            let disc = parseFloat($('cartDiscount').value) || 0;
            try {
                const { ok, status, data } = await apiFetch('/api/sale', {
                    method: 'POST',
                    body: JSON.stringify({
                        initData: (tg && tg.initData) || '',
                        items, payment, discount_sum: disc
                    })
                });
                if (!ok || !data.ok) {
                    $('cartStatus').textContent = '❌ ' + (data.error || ('Xato '+status));
                } else {
                    cart = {};
                    $('cartDiscount').value = '';
                    renderCart(); updateCartBar();
                    $('cartStatus').textContent =
                        `✅ Sotuv #${data.sale_id} yakunlandi! Jami: ${fmtSum(data.total_sum)} so'm`;
                    if (tg && tg.showPopup) {
                        tg.showPopup({ title:'✅ Sotuv yakunlandi',
                            message:`Chek #${data.sale_id}\nJami: ${fmtSum(data.total_sum)} so'm` +
                                    (data.change_sum>0?`\nQaytim: ${fmtSum(data.change_sum)} so'm`:''),
                            buttons:[{type:'close'}] });
                    }
                    setTimeout(()=>{ showScreen('screenPOS'); loadPOS(true); }, 1400);
                }
            } catch (err) {
                $('cartStatus').textContent = 'Tarmoq xatosi: ' + (err.message||err);
            }
            $('payCashBtn').disabled = false; $('payCardBtn').disabled = false;
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

        window.scanInto = function (target) {
            _scanTarget = target || 'product';
            if (_starting) return;   // allaqachon ishga tushmoqda
            if (window.Quagga) {
                startQuaggaScan();           // tezkor 1D (web-worker)
            } else if (window.Html5Qrcode) {
                startCameraScan();           // zaxira: html5-qrcode
            } else if (tg && tg.showScanQrPopup) {
                tgQrFallback();              // oxirgi zaxira: Telegram QR
            } else {
                toast('Skaner mavjud emas. Internetni tekshiring.');
            }
        };

        // ================================================================
        // QUAGGA2 — tezkor 1D shtrix-kod skaneri (ko'p-yadroli web-worker)
        // ================================================================
        let _quaggaOn = false;
        let _lastCode = null, _lastCount = 0;

        function startQuaggaScan() {
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
                    // Faqat markaziy tasma skanerlanadi — tezroq va aniqroq
                    area: { top: "25%", right: "8%", left: "8%", bottom: "25%" }
                },
                locator: { patchSize: "medium", halfSample: true },
                numOfWorkers: Math.max(2, (navigator.hardwareConcurrency || 4)),
                frequency: 10,
                decoder: {
                    readers: [
                        "ean_reader", "ean_8_reader",
                        "upc_reader", "upc_e_reader",
                        "code_128_reader", "code_39_reader",
                        "i2of5_reader", "codabar_reader"
                    ]
                },
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
            });

            Quagga.offDetected(_onQuaggaDetected);
            Quagga.onDetected(_onQuaggaDetected);
        }

        function _onQuaggaDetected(result) {
            if (_scanBusy) return;
            const code = result && result.codeResult && result.codeResult.code;
            if (!code) return;
            // Ishonchlilik: bir xil kod 2 marta ketma-ket o'qilsa qabul qilamiz
            // (Quagga ba'zan xato o'qishi mumkin — bu xatoliklarni kamaytiradi).
            if (code === _lastCode) {
                _lastCount += 1;
            } else {
                _lastCode = code; _lastCount = 1;
            }
            if (_lastCount < 2) return;

            _scanBusy = true;
            if (tg && tg.HapticFeedback) {
                try { tg.HapticFeedback.impactOccurred('medium'); } catch(_){}
            }
            $('scanHint').textContent = '✅ O\'qildi: ' + code;
            _applyScan(code);
            stopScan();
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

            const onOk = (decodedText) => {
                if (_scanBusy) return;
                _scanBusy = true;
                if (tg && tg.HapticFeedback) {
                    try { tg.HapticFeedback.impactOccurred('medium'); } catch(_){}
                }
                $('scanHint').textContent = '✅ O\'qildi: ' + decodedText;
                _applyScan(decodedText);
                stopScan();
            };

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

        window.stopScan = function () {
            const overlay = $('scanOverlay');
            const inst = _html5qr;
            _starting = false;
            // Quagga2 ni to'xtatamiz (agar ishlayotgan bo'lsa)
            if (_quaggaOn && window.Quagga) {
                try { Quagga.offDetected(_onQuaggaDetected); } catch(_) {}
                try { Quagga.stop(); } catch(_) {}
                _quaggaOn = false;
                const r = document.getElementById('reader');
                try { if (r) r.innerHTML = ''; } catch(_) {}
            }
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
                const typeTxt = c.client_type === 'optom' ? '📦' : '🛍️';
                html += `<div class="stat-block">
                    <div class="stat-row"><span class="lbl">${typeTxt} ${escapeHtml(c.shop_name)}</span><span class="val ${debtCls}">${debtTxt}</span></div>
                    <div class="stat-row"><span class="lbl">📱 Telefon</span><span class="val">${escapeHtml(c.phone||'—')}</span></div>
                    <div class="stat-row"><span class="lbl">📅 Qo'shilgan</span><span class="val">${c.created_at}</span></div>
                </div>`;
            });
            if (!(data.items||[]).length) html += '<div class="list-status">Foydalanuvchilar yo\'q</div>';
            body.innerHTML = html;
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
            (data.items || []).forEach(o => {
                const statusIcon = o.status === 'done' ? '✅' : o.status === 'cancelled' ? '❌' : '⏳';
                html += `<div class="stat-block">
                    <div class="stat-row"><span class="lbl">#${o.id}</span><span class="val">${statusIcon} ${escapeHtml(o.shop_name||'')}</span></div>
                    <div class="stat-row"><span class="lbl">💰 Summa</span><span class="val val-green">${fmtSum(o.total)} so'm</span></div>
                    <div class="stat-row"><span class="lbl">📅 Sana</span><span class="val">${o.created_at}</span></div>
                </div>`;
            });
            if (!(data.items||[]).length) html += '<div class="list-status">Buyurtmalar yo\'q</div>';
            body.innerHTML = html;
        };
        window.openStaff = async function () {
            showScreen('screenStats');
            const body = $('statsBody');
            body.innerHTML = '<div class="list-status">Yuklanmoqda...</div>';
            const { ok, status, data } = await apiFetch('/api/admins');
            if (!ok || !data.ok) {
                body.innerHTML = '<div class="list-status">' + (status===403?'Ruxsat yo\'q':status===401?'🔒 Qaytadan kiring.':'Xato: '+(data.error||status)) + '</div>';
                return;
            }
            let html = `<div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;">
                <button class="list-back" onclick="showScreen('screenAdmin')">← Orqaga</button>
                <h2 style="font-size:18px;font-weight:800;">👔 Xodimlar (${data.total})</h2>
            </div>`;
            (data.items || []).forEach(a => {
                const roleTxt = a.role === 'full' ? '👑 To\'liq' : '🔰 Cheklangan';
                html += `<div class="stat-block">
                    <div class="stat-row"><span class="lbl">👤 ${escapeHtml(a.full_name||'')}</span><span class="val">${roleTxt}</span></div>
                    <div class="stat-row"><span class="lbl">🔑 Login</span><span class="val">${escapeHtml(a.username||'—')}</span></div>
                    <div class="stat-row"><span class="lbl">📅 Qo'shilgan</span><span class="val">${a.created_at}</span></div>
                </div>`;
            });
            if (!(data.items||[]).length) html += '<div class="list-status">Xodimlar yo\'q</div>';
            body.innerHTML = html;
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
