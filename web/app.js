/* ==========================================================================
   麦门盲盒 · 前端交互状态机
   ========================================================================== */
(function () {
  "use strict";

  // ---- 状态 ----
  const state = {
    mode: "offline",            // offline | live
    scene: "pickup",            // pickup | dt | delivery
    searchType: 1,
    stores: [],                 // 门店列表
    addresses: [],
    selectedStore: null,        // {storeCode, storeName, beCode}
    selectedAddressId: null,
    modes: new Set(),           // 选中的挑战模式
    taste: null,
    sessionId: null,
    card: null,
    shareText: "",
    lastOrderId: null,        // 最近一次下单的订单号，供「查支付状态」用
  };

  // Token 只存内存，不落 localStorage/sessionStorage
  let token = "";

  // ---- DOM ----
  const $ = (id) => document.getElementById(id);
  const els = {
    modeSwitch: $("modeSwitch"),
    tokenWrap: $("tokenWrap"),
    tokenField: $("tokenField"),
    tokenInput: $("tokenInput"),
    clearToken: $("clearToken"),
    tokenHint: $("tokenHint"),
    connStatus: $("connStatus"),
    sceneCards: $("sceneCards"),
    storeSearch: $("storeSearch"),
    searchFields: $("searchFields"),
    cityInput: $("cityInput"),
    keywordInput: $("keywordInput"),
    searchStores: $("searchStores"),
    addressWrap: $("addressWrap"),
    loadAddresses: $("loadAddresses"),
    addressSelect: $("addressSelect"),
    storeList: $("storeList"),
    storePlaceholder: $("storePlaceholder"),
    modeChips: $("modeChips"),
    constraints: $("constraints"),
    budgetConstraint: $("budgetConstraint"),
    budgetInput: $("budgetInput"),
    calorieConstraint: $("calorieConstraint"),
    calorieInput: $("calorieInput"),
    tasteConstraint: $("tasteConstraint"),
    tasteChips: $("tasteChips"),
    openBtn: $("openBtn"),
    openHint: $("openHint"),
    reveal: $("reveal"),
    boxStage: $("boxStage"),
    giftBox: $("giftBox"),
    openingText: $("openingText"),
    revealCard: $("revealCard"),
    revealTitle: $("revealTitle"),
    challengeBadge: $("challengeBadge"),
    challengeProgress: $("challengeProgress"),
    revealItems: $("revealItems"),
    revealStore: $("revealStore"),
    revealOriginal: $("revealOriginal"),
    revealPayable: $("revealPayable"),
    revealCouponRow: $("revealCouponRow"),
    revealCoupon: $("revealCoupon"),
    revealTakeWayRow: $("revealTakeWayRow"),
    revealTakeWay: $("revealTakeWay"),
    revealQuip: $("revealQuip"),
    revealSeed: $("revealSeed"),
    revealNotes: $("revealNotes"),
    nutritionNote: $("nutritionNote"),
    shareBtn: $("shareBtn"),
    orderBtn: $("orderBtn"),
    confirmModal: $("confirmModal"),
    confirmBody: $("confirmBody"),
    confirmRemarkWrap: $("confirmRemarkWrap"),
    remarkInput: $("remarkInput"),
    confirmCheck: $("confirmCheck"),
    cancelOrderBtn: $("cancelOrderBtn"),
    doOrderBtn: $("doOrderBtn"),
    orderResultModal: $("orderResultModal"),
    orderResultTitle: $("orderResultTitle"),
    orderResultBody: $("orderResultBody"),
    closeOrderResultBtn: $("closeOrderResultBtn"),
    checkOrderStatusBtn: $("checkOrderStatusBtn"),
    toast: $("toast"),
  };

  // ---- 工具 ----
  const MODE_LABELS = {
    blind: "纯盲盒", budget: "预算挑战", calorie: "热量挑战", taste: "口味挑战",
  };
  const CAT_LABELS = { main: "主食", side: "小食", drink: "饮品", dessert: "甜品" };
  const BE_TYPE_LABELS = { 1: "到店自取", 5: "得来速车道取餐", 2: "麦乐送到家" };

  let toastTimer = null;
  function toast(msg, type) {
    els.toast.textContent = msg;
    // 重置动画：先去掉 in 类强制重排，再重新触发进入动画
    els.toast.classList.remove("in");
    els.toast.className = "toast" + (type ? " " + type : "");
    els.toast.hidden = false;
    // 强制重排，确保移除 .in 后再加能重新触发过渡
    void els.toast.offsetWidth;
    requestAnimationFrame(() => els.toast.classList.add("in"));
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      els.toast.classList.remove("in");
      // 等退出过渡结束再隐藏（约 200ms）
      setTimeout(() => { els.toast.hidden = true; }, 220);
    }, 4000);
  }

  // 统一按钮 loading 态：busy=true 时禁用 + 显示 spinner + 保留原文案
  // （通过 data-label 记住原文案，恢复时还原）。避免各处手写 disabled/textContent 不一致。
  function setBusy(btn, busy, busyLabel) {
    if (!btn) return;
    if (busy) {
      if (!btn.dataset.label) btn.dataset.label = btn.textContent;
      btn.disabled = true;
      btn.classList.add("loading");
      if (busyLabel) btn.textContent = busyLabel;
    } else {
      btn.disabled = false;
      btn.classList.remove("loading");
      if (btn.dataset.label) {
        btn.textContent = btn.dataset.label;
        delete btn.dataset.label;
      }
    }
  }

  async function api(path, body) {
    const res = await fetch(path, {
      method: body === undefined ? "GET" : "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data;
    try { data = await res.json(); } catch { data = { ok: false, error: { message: "响应不是 JSON" } }; }
    if (!data.ok) {
      const err = data.error || {};
      const e = new Error(err.message || "请求失败");
      e.code = err.code;
      e.retryable = err.retryable;
      throw e;
    }
    return data;
  }

  function bodyWithToken(extra) {
    const b = Object.assign({}, extra || {});
    // 关键：把「离线/联网」随请求显式传给后端，否则后端只会用启动参数。
    b.offline = state.mode === "offline";
    if (state.mode === "live") b.token = token;
    return b;
  }

  function needToken() {
    return state.mode === "live" && !token;
  }

  // ---- 连接状态 ----
  async function checkHealth() {
    try {
      const r = await api("/api/health");
      els.connStatus.classList.remove("fail");
      els.connStatus.classList.add("ok");
      // 标签反映「前端当前选择」，而非后端启动模式：切到联网后应显示联网状态
      els.connStatus.querySelector(".conn-label").textContent =
        state.mode === "offline" ? "离线演示" : "联网真店";
    } catch (e) {
      els.connStatus.classList.remove("ok");
      els.connStatus.classList.add("fail");
      els.connStatus.querySelector(".conn-label").textContent = "后端未连接";
    }
  }

  // ---- 模式切换 ----
  els.modeSwitch.addEventListener("click", (e) => {
    const btn = e.target.closest(".mode-opt");
    if (!btn) return;
    state.mode = btn.dataset.mode;
    els.modeSwitch.querySelectorAll(".mode-opt").forEach((b) => {
      const on = b === btn;
      b.classList.toggle("active", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
    els.tokenWrap.hidden = state.mode !== "live";
    if (state.mode === "offline") {
      token = "";
      els.tokenInput.value = "";
      // 重置 Token 框的校验状态
      refreshTokenField();
    }
    updateOpenBtn();
    checkHealth();
  });

  // ---- Token 输入：校验 + 清空 + 状态反馈 ----
  // Token 合法性：非空，且不包含空白字符（MCP Token 通常为无空格的紧凑字符串）
  function tokenValidity(value) {
    if (!value) return { ok: false, msg: "" };           // 空值：不提示，仅清空状态
    if (/\s/.test(value)) return { ok: false, msg: "Token 不能包含空格" };
    if (value.length < 8) return { ok: false, msg: "Token 过短，请检查是否完整" };
    return { ok: true, msg: "Token 已就绪" };
  }

  function refreshTokenField() {
    const raw = els.tokenInput.value;
    const value = raw.trim();
    const v = tokenValidity(value);
    const hasValue = value.length > 0;

    // 有值时显示清空按钮（始终可见，不做 hover/focus 渐显）
    els.clearToken.hidden = !hasValue;

    // 校验状态：清空非法态 → 设置合法/非法态
    els.tokenInput.classList.remove("valid");
    if (!value) {
      els.tokenInput.removeAttribute("aria-invalid");
      setTokenHint("", "");
    } else if (v.ok) {
      els.tokenInput.removeAttribute("aria-invalid");
      els.tokenInput.classList.add("valid");
      setTokenHint(v.msg, "ok");
    } else {
      els.tokenInput.setAttribute("aria-invalid", "true");
      setTokenHint(v.msg, "error");
    }
  }

  function setTokenHint(msg, type) {
    els.tokenHint.textContent = msg;
    els.tokenHint.className = "token-hint" + (type ? " " + type : "");
  }

  els.tokenInput.addEventListener("input", () => {
    // 非法（含空格）时不写入 token，避免把脏值传给后端
    const value = els.tokenInput.value.trim();
    token = (value && tokenValidity(value).ok) ? value : "";
    refreshTokenField();
    updateOpenBtn();
  });

  els.clearToken.addEventListener("click", () => {
    token = "";
    els.tokenInput.value = "";
    refreshTokenField();
    updateOpenBtn();
    els.tokenInput.focus();
  });

  // ---- 场景选择 ----
  els.sceneCards.addEventListener("click", (e) => {
    const card = e.target.closest(".scene-card");
    if (!card) return;
    state.scene = card.dataset.scene;
    els.sceneCards.querySelectorAll(".scene-card").forEach((c) => {
      const on = c === card;
      c.classList.toggle("active", on);
      c.setAttribute("aria-pressed", on ? "true" : "false");
    });
    state.stores = [];
    state.selectedStore = null;
    state.selectedAddressId = null;
    els.storeList.innerHTML = '<p class="placeholder" id="storePlaceholder">先查门店</p>';
    // 显示/隐藏对应搜索区
    els.storeSearch.hidden = state.scene === "delivery";
    els.addressWrap.hidden = state.scene !== "delivery";
    els.addressSelect.hidden = true;
    updateOpenBtn();
  });

  // ---- 搜索方式 ----
  document.querySelectorAll('input[name="searchType"]').forEach((r) => {
    r.addEventListener("change", () => {
      state.searchType = Number(document.querySelector('input[name="searchType"]:checked').value);
      els.searchFields.hidden = state.searchType !== 2;
    });
  });
  // 重新输入时清除校验错误标记
  els.cityInput.addEventListener("input", () => clearInvalid(els.cityInput));
  els.keywordInput.addEventListener("input", () => clearInvalid(els.keywordInput));

  // ---- 查门店 ----
  els.searchStores.addEventListener("click", async () => {
    if (needToken()) return toast("请先填写 MCP Token", "error");
    // 表单校验：按城市搜必须填城市 + 关键词
    if (state.searchType === 2) {
      const city = els.cityInput.value.trim();
      const keyword = els.keywordInput.value.trim();
      let valid = true;
      if (!city) { markInvalid(els.cityInput, "请填写城市"); valid = false; }
      else clearInvalid(els.cityInput);
      if (!keyword) { markInvalid(els.keywordInput, "请填写关键词"); valid = false; }
      else clearInvalid(els.keywordInput);
      if (!valid) return;
    }
    setBusy(els.searchStores, true, "查询中…");
    renderSkeleton();
    try {
      const body = { scene: state.scene, searchType: state.searchType };
      if (state.searchType === 2) {
        body.city = els.cityInput.value.trim();
        body.keyword = els.keywordInput.value.trim();
      }
      const r = await api("/api/stores", bodyWithToken(body));
      state.stores = r.stores || [];
      renderStores();
    } catch (e) {
      renderStateMessage("查询失败：" + e.message, "error");
      toast(e.message, "error");
    } finally {
      setBusy(els.searchStores, false);
    }
  });

  // ---- 查地址（麦乐送）----
  els.loadAddresses.addEventListener("click", async () => {
    if (needToken()) return toast("请先填写 MCP Token", "error");
    setBusy(els.loadAddresses, true, "查询中…");
    try {
      const r = await api("/api/addresses", bodyWithToken());
      state.addresses = r.addresses || [];
      if (!state.addresses.length) return toast("没有可用配送地址", "error");
      els.addressSelect.innerHTML = "";
      state.addresses.forEach((a) => {
        const opt = document.createElement("option");
        opt.value = a.addressId;
        opt.textContent = `${a.contactName} · ${a.fullAddress}`;
        els.addressSelect.appendChild(opt);
      });
      els.addressSelect.hidden = false;
      state.selectedAddressId = state.addresses[0].addressId;
      await loadDeliveryStores();
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setBusy(els.loadAddresses, false);
    }
  });

  els.addressSelect.addEventListener("change", async () => {
    state.selectedAddressId = els.addressSelect.value;
    await loadDeliveryStores();
  });

  async function loadDeliveryStores() {
    renderSkeleton();
    try {
      const r = await api("/api/stores", bodyWithToken({
        scene: "delivery", addressId: state.selectedAddressId,
      }));
      state.stores = r.stores || [];
      renderStores();
    } catch (e) {
      renderStateMessage("查询失败：" + e.message, "error");
      toast(e.message, "error");
    }
  }

  function renderStores() {
    if (!state.stores.length) {
      els.storeList.innerHTML = '<p class="placeholder">没查到门店，试试按城市搜索</p>';
      updateOpenBtn();
      return;
    }
    els.storeList.innerHTML = "";
    state.stores.forEach((s) => {
      const div = document.createElement("div");
      div.className = "store-item";
      div.dataset.code = s.storeCode;
      // 门店卡片可键盘操作：role=option + tabindex，回车/空格选中
      div.setAttribute("role", "option");
      div.setAttribute("tabindex", "0");
      div.setAttribute("aria-selected", "false");
      const meta = BE_TYPE_LABELS[s.beType] || "";
      div.innerHTML = `
        <div>
          <div class="s-name">${escapeHtml(s.storeName)}</div>
          <div class="s-meta">${meta}</div>
        </div>
        <span class="s-check" aria-hidden="true">✓</span>
        <div class="s-meta">${escapeHtml(s.storeCode)}</div>`;
      const select = () => {
        state.selectedStore = s;
        els.storeList.querySelectorAll(".store-item").forEach((n) => {
          const on = n === div;
          n.classList.toggle("active", on);
          n.setAttribute("aria-selected", on ? "true" : "false");
        });
        updateOpenBtn();
      };
      div.addEventListener("click", select);
      div.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(); }
      });
      els.storeList.appendChild(div);
    });
  }

  // 骨架屏：查询中的占位
  function renderSkeleton() {
    els.storeList.innerHTML = "";
    for (let i = 0; i < 3; i++) {
      const sk = document.createElement("div");
      sk.className = "skeleton";
      sk.innerHTML = '<div class="skeleton-line w60"></div><div class="skeleton-line w40"></div>';
      els.storeList.appendChild(sk);
    }
  }

  // 空态 / 错误态提示
  function renderStateMessage(msg, type) {
    els.storeList.innerHTML =
      `<p class="placeholder${type === "error" ? " error" : ""}">${escapeHtml(msg)}</p>`;
    updateOpenBtn();
  }

  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  // ---- 表单校验工具 ----
  // 给输入框标红 + 返回错误信息；空值返回 null 表示通过
  function markInvalid(input, msg) {
    input.setAttribute("aria-invalid", "true");
    if (msg) toast(msg, "error");
  }
  function clearInvalid(input) {
    input.removeAttribute("aria-invalid");
  }
  function validatePositiveNumber(input, label, { min, max, integer }) {
    const raw = input.value.trim();
    if (!raw) return null; // 未填视为「不启用该约束」，由 openBox 决定
    const n = Number(raw);
    if (!Number.isFinite(n) || n <= 0) {
      markInvalid(input, `${label}需为正数`);
      return null;
    }
    if (integer && !Number.isInteger(n)) {
      markInvalid(input, `${label}需为整数`);
      return null;
    }
    if (min != null && n < min) {
      markInvalid(input, `${label}不能小于 ${min}`);
      return null;
    }
    if (max != null && n > max) {
      markInvalid(input, `${label}不能超过 ${max}`);
      return null;
    }
    clearInvalid(input);
    return n;
  }

  // ---- 挑战模式 ----
  els.modeChips.addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    // 纯盲盒与其他挑战互斥：选中纯盲盒后，其余挑战按钮被禁用，不可点
    if (chip.disabled) return;
    const m = chip.dataset.mode;
    if (m === "blind") {
      // 纯盲盒：切换自身选中态；选中时清空其他挑战
      if (state.modes.has("blind")) {
        state.modes.delete("blind");
      } else {
        state.modes.clear();
        state.modes.add("blind");
      }
    } else {
      // 其他挑战：选中时若纯盲盒在，先移除纯盲盒
      if (state.modes.has(m)) state.modes.delete(m);
      else {
        state.modes.delete("blind");
        state.modes.add(m);
      }
    }
    syncModeChips();
    renderConstraints();
    updateOpenBtn();
  });

  // 同步挑战 chips 的选中态与禁用态
  function syncModeChips() {
    const blindOn = state.modes.has("blind");
    els.modeChips.querySelectorAll(".chip").forEach((chip) => {
      const m = chip.dataset.mode;
      const on = state.modes.has(m);
      chip.classList.toggle("active", on);
      chip.setAttribute("aria-pressed", on ? "true" : "false");
      // 纯盲盒选中时，禁用其余挑战；其余挑战选中时，纯盲盒本身仍可点（用于取消）
      chip.disabled = blindOn && m !== "blind";
    });
  }

  function renderConstraints() {
    els.budgetConstraint.hidden = !state.modes.has("budget");
    els.calorieConstraint.hidden = !state.modes.has("calorie");
    els.tasteConstraint.hidden = !state.modes.has("taste");
  }

  els.tasteChips.addEventListener("click", (e) => {
    const chip = e.target.closest(".taste-chip");
    if (!chip) return;
    state.taste = chip.dataset.taste;
    els.tasteChips.querySelectorAll(".taste-chip").forEach((c) => {
      const on = c === chip;
      c.classList.toggle("active", on);
      c.setAttribute("aria-pressed", on ? "true" : "false");
    });
    updateOpenBtn();
  });

  // 预算/热量即时校验：用户输入时即时标红，但只在开盒时强制拦截
  els.budgetInput.addEventListener("input", () => {
    if (els.budgetInput.value.trim()) validatePositiveNumber(els.budgetInput, "预算", { min: 1, max: 100000 });
  });
  els.calorieInput.addEventListener("input", () => {
    if (els.calorieInput.value.trim()) validatePositiveNumber(els.calorieInput, "热量", { min: 1, max: 50000 });
  });
  els.budgetInput.addEventListener("blur", () => clearInvalid(els.budgetInput));
  els.calorieInput.addEventListener("blur", () => clearInvalid(els.calorieInput));

  // ---- 开盒按钮状态 ----
  function updateOpenBtn() {
    const needStore = !state.selectedStore;
    const needTok = needToken();
    let hint = "";
    if (needTok) hint = "请先填写 MCP Token";
    else if (needStore) hint = "请先选择门店";
    else if (state.modes.size === 0) hint = "可点「开一盒」直接开纯盲盒";
    else hint = "一切就绪，开盒吧";
    els.openHint.textContent = hint;
    els.openBtn.disabled = needStore || needTok;
  }

  // ---- 开盒 ----
  els.openBtn.addEventListener("click", openBox);

  async function openBox() {
    if (needToken()) return toast("请先填写 MCP Token", "error");
    if (!state.selectedStore) return toast("请先选择门店", "error");

    // 开盒前强制校验预算/热量（已启用对应挑战时）
    let budgetYuan = null;
    let calorieCap = null;
    if (state.modes.has("budget")) {
      budgetYuan = validatePositiveNumber(els.budgetInput, "预算", { min: 1, max: 100000 });
      if (budgetYuan === null && els.budgetInput.value.trim()) return; // 有值但非法
    }
    if (state.modes.has("calorie")) {
      calorieCap = validatePositiveNumber(els.calorieInput, "热量", { min: 1, max: 50000 });
      if (calorieCap === null && els.calorieInput.value.trim()) return;
    }

    const body = {
      scene: state.scene,
      store: {
        storeCode: state.selectedStore.storeCode,
        storeName: state.selectedStore.storeName,
        beCode: state.selectedStore.beCode || null,
      },
      modes: Array.from(state.modes),
      budgetYuan,
      calorieCap,
      taste: state.modes.has("taste") ? state.taste : null,
      withModification: false,
      timestampMs: null,
    };
    if (state.scene === "delivery") body.addressId = state.selectedAddressId;

    // 进入 OPENING：禁按钮 + 显示盲盒动画
    setBusy(els.openBtn, true, "开盒中…");
    els.reveal.hidden = false;
    els.revealCard.hidden = true;
    els.boxStage.hidden = false;
    els.giftBox.className = "gift-box shaking";
    els.openingText.textContent = "正在开盒…（预计 5–15 秒）";

    try {
      const r = await api("/api/open", bodyWithToken(body));
      state.sessionId = r.sessionId;
      state.card = r.card;
      state.shareText = r.shareText || "";
      // 重新开盒后恢复下单按钮可用
      els.orderBtn.disabled = false;
      els.orderBtn.textContent = "✅ 确认下单";
      await playReveal();
    } catch (e) {
      els.giftBox.className = "gift-box";
      els.boxStage.hidden = true;
      els.reveal.hidden = true;
      toast(e.message, "error");
      if (e.retryable) toast("可以换个约束或门店再试", "error");
    } finally {
      setBusy(els.openBtn, false);
      updateOpenBtn();
    }
  }

  // ---- 揭晓动画 ----
  function playReveal() {
    return new Promise((resolve) => {
      // 阶段1-2：晃动 → 开盖
      els.giftBox.classList.remove("shaking");
      els.giftBox.classList.add("opening");
      els.openingText.textContent = "揭晓中…";
      setTimeout(() => {
        els.boxStage.hidden = true;
        els.revealCard.hidden = false;
        els.revealCard.classList.add("pop-in");
        fillCard();
        // 揭晓卡出现后平滑滚动到可视区（尊重 reduced-motion）
        requestAnimationFrame(() => {
          els.revealCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
          // 焦点移入揭晓卡标题，读屏器播报结果，用户可继续 Tab 到「下单」
          els.revealTitle.focus({ preventScroll: true });
        });
        resolve();
      }, 750);
    });
  }

  function fillCard() {
    const c = state.card;
    // 标题
    const modeNames = (c.modes || []).map((m) => MODE_LABELS[m] || m);
    els.revealTitle.textContent = "麦门盲盒 · " + (modeNames.join(" + ") || "纯盲盒");

    // 挑战徽章
    if (c.challengeOk) {
      els.challengeBadge.textContent = "挑战成功";
      els.challengeBadge.classList.remove("fail");
    } else {
      els.challengeBadge.textContent = "未达标（已放宽）";
      els.challengeBadge.classList.add("fail");
    }

    // 挑战进度
    els.challengeProgress.innerHTML = "";
    if (c.budgetYuan != null) {
      const payable = parseFloat(c.price.payableYuan);
      const remain = c.budgetYuan - payable;
      els.challengeProgress.appendChild(cpRow(
        "预算", `${c.budgetYuan.toFixed(2)} 元`,
        `券后 ¥${c.price.payableYuan} → ${remain >= 0 ? "余额" : "超出"} ${Math.abs(remain).toFixed(2)} 元`
      ));
    }
    if (c.calorieCap != null) {
      const cal = c.calories;
      els.challengeProgress.appendChild(cpRow(
        "热量", `${c.calorieCap} 大卡`,
        cal != null ? `${cal} 大卡` : "无法计算"
      ));
    }

    // 品项
    els.revealItems.innerHTML = "";
    (c.items || []).forEach((it, i) => {
      const li = document.createElement("li");
      li.className = "reveal-item";
      li.style.animationDelay = (i * 140) + "ms";
      const cat = CAT_LABELS[it.category] || "配品";
      const role = i === 0 ? "主品" : "配品";
      li.innerHTML = `
        <span class="it-cat">${role}·${cat}</span>
        <span class="it-name">${escapeHtml(it.name)} × ${it.quantity}</span>
        <span class="it-price">¥${it.unitPriceYuan}</span>`;
      els.revealItems.appendChild(li);
      // 触发逐项淡入
      requestAnimationFrame(() => li.classList.add("in"));
    });

    // 门店 / 价格
    els.revealStore.textContent = c.store ? c.store.storeName : "—";
    els.revealOriginal.textContent = "¥" + c.price.originalYuan;
    els.revealPayable.textContent = "¥" + c.price.payableYuan;

    if (c.coupon) {
      els.revealCouponRow.hidden = false;
      els.revealCoupon.textContent = c.coupon.title || "优惠券";
    } else if ((c.price.discountFen || 0) > 0) {
      els.revealCouponRow.hidden = false;
      els.revealCoupon.textContent = "服务端优惠 ¥" + (c.price.discountFen / 100).toFixed(2) + "（非券来源）";
    } else {
      els.revealCouponRow.hidden = true;
    }

    if (c.price.takeWayList && c.price.takeWayList.length) {
      els.revealTakeWayRow.hidden = false;
      els.revealTakeWay.textContent = c.price.takeWayList.map((t) => t.name || t.code).join(" / ");
    } else {
      els.revealTakeWayRow.hidden = true;
    }

    // 点评 + 种子验算行
    els.revealQuip.textContent = makeQuip(c.seed);
    els.revealSeed.textContent =
      `种子 ${c.seed} → 第 ${c.mainIndex + 1} 项（主品候选池 ${c.mainPoolSize} 件；` +
      `${c.seed} mod ${c.mainPoolSize} = ${c.mainIndex}）`;

    // 注意项
    els.revealNotes.innerHTML = "";
    (c.notes || []).forEach((n) => {
      const li = document.createElement("li");
      li.textContent = n;
      els.revealNotes.appendChild(li);
    });

    // 营养提示
    els.nutritionNote.hidden = !(c.modes || []).includes("calorie");
  }

  function cpRow(label, target, actual) {
    const div = document.createElement("div");
    div.className = "cp-row";
    div.innerHTML = `<span class="cp-label">${label} ${escapeHtml(target)}</span><span>→ ${escapeHtml(actual)}</span>`;
    return div;
  }

  const QUIPS = [
    (s) => `服务器在 ${s} 毫秒时点了点头，于是有了这顿。`,
    () => "盲盒官只负责抽，好不好吃是玄学的事。",
    () => "这份是算出来的，不是想出来的 —— 价格来自真实菜单。",
    () => "随机来自时间戳，不来自心情。所以你可以复核它。",
    () => "开盒完成。不满意可以重开，但换的是种子，不是人品。",
  ];
  function makeQuip(seed) {
    return QUIPS[(seed % QUIPS.length + QUIPS.length) % QUIPS.length](seed);
  }

  // ---- 分享 ----
  els.shareBtn.addEventListener("click", async () => {
    const text = state.shareText || "";
    if (!text) return toast("先开一盒再分享", "error");
    try {
      const ok = await copyText(text);
      toast(ok ? "已复制分享文案" : "复制失败，请手动复制", ok ? "success" : "error");
    } catch (e) {
      console.error("复制分享文案失败：", e);
      toast("复制失败，请手动复制", "error");
    }
  });

  // 复制到剪贴板：优先 navigator.clipboard（需安全上下文），
  // 失败/不可用时降级到 document.execCommand('copy') + 隐藏 textarea。
  // 这样即使通过局域网 IP 访问（非安全上下文）也能复制成功。
  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch (e) {
        // 落到降级方案
      }
    }
    return legacyCopy(text);
  }

  function legacyCopy(text) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    ta.style.top = "0";
    document.body.appendChild(ta);
    const selection = document.getSelection();
    const savedRange = selection && selection.rangeCount > 0 ? selection.getRangeAt(0) : null;
    ta.select();
    ta.setSelectionRange(0, text.length);
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (e) {
      ok = false;
    }
    document.body.removeChild(ta);
    if (savedRange && selection) {
      selection.removeAllRanges();
      selection.addRange(savedRange);
    }
    return ok;
  }

  // ---- 下单确认 ----
  els.orderBtn.addEventListener("click", () => {
    if (!state.card) {
      toast("请先开一盒再下单", "error");
      return;
    }
    try {
      renderConfirmModal();
      showModal(true);
      els.confirmCheck.checked = false;
      els.doOrderBtn.disabled = true;
      els.remarkInput.value = "";
    } catch (e) {
      // 弹窗渲染失败绝不能静默吞掉，给用户可见反馈并打日志
      console.error("渲染下单确认弹窗失败：", e);
      toast("无法生成下单明细，请重新开一盒", "error");
    }
  });

  // ---- 弹窗管理 ----
  // 统一封装：opacity/transform 过渡 + 焦点陷阱 + Esc/遮罩关闭。
  // 用 .open 类驱动 CSS 过渡，避免只用 hidden 的生硬闪现。
  let lastFocusedEl = null;

  function openModal(backdrop) {
    if (!backdrop) return;
    lastFocusedEl = document.activeElement;
    backdrop.hidden = false;
    // 下一帧加 .open 触发过渡
    requestAnimationFrame(() => requestAnimationFrame(() => backdrop.classList.add("open")));
    // 焦点移入弹窗内第一个可聚焦元素
    const focusable = backdrop.querySelector("button, input, select, [tabindex]");
    if (focusable) focusable.focus();
    document.body.style.overflow = "hidden";
  }

  function closeModal(backdrop) {
    if (!backdrop) return;
    backdrop.classList.remove("open");
    // 等退出过渡结束再隐藏
    setTimeout(() => {
      backdrop.hidden = true;
      document.body.style.overflow = "";
      if (lastFocusedEl && lastFocusedEl.focus) lastFocusedEl.focus();
      lastFocusedEl = null;
    }, 220);
  }

  // 给两个弹窗绑定 Esc 关闭 + 点击遮罩关闭 + 焦点陷阱
  function bindModalBehaviors(backdrop) {
    if (!backdrop || backdrop.dataset.bound) return;
    backdrop.dataset.bound = "1";
    // 点击遮罩本身（非弹窗内容）关闭
    backdrop.addEventListener("click", (e) => {
      if (e.target === backdrop) closeModal(backdrop);
    });
    // Esc 关闭
    backdrop.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        closeModal(backdrop);
        return;
      }
      // Tab 焦点陷阱：焦点在弹窗内循环
      if (e.key === "Tab") {
        const focusable = backdrop.querySelectorAll(
          "button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex='-1'])");
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    });
  }

  // 兼容旧调用：showModal(show) 只操作 confirmModal
  function showModal(show) {
    if (show) openModal(els.confirmModal);
    else closeModal(els.confirmModal);
  }

  function renderConfirmModal() {
    const c = state.card || {};
    const price = c.price || {};
    const items = Array.isArray(c.items) ? c.items : [];
    const takeWays = Array.isArray(price.takeWayList) ? price.takeWayList : [];
    const rows = [];
    rows.push(mRow("门店", c.store ? c.store.storeName : "—"));
    const takeWay = takeWays.length
      ? takeWays.map((t) => (t && (t.name || t.code)) || "—").join(" / ") : "—";
    rows.push(mRow("取餐方式", takeWay));
    rows.push(mRow("品项", items.length
      ? items.map((i) => `${i.name || "?"}×${i.quantity || 1}`).join("、") : "—"));
    rows.push(mRow("原价", "¥" + (price.originalYuan != null ? price.originalYuan : "—")));
    rows.push(mRow("券后价", "¥" + (price.payableYuan != null ? price.payableYuan : "—")));
    els.confirmBody.innerHTML = rows.join("");
    // 备注仅外送
    els.confirmRemarkWrap.hidden = state.scene !== "delivery";
  }

  function mRow(label, value) {
    return `<div class="m-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
  }

  els.confirmCheck.addEventListener("change", () => {
    els.doOrderBtn.disabled = !els.confirmCheck.checked;
  });

  els.cancelOrderBtn.addEventListener("click", () => {
    showModal(false);
  });

  els.doOrderBtn.addEventListener("click", async () => {
    if (!els.confirmCheck || !els.confirmCheck.checked) return;
    if (state.mode === "live" && !token) return toast("请先填写 MCP Token", "error");
    if (!state.sessionId || !state.card) {
      toast("会话已失效，请重新开一盒", "error");
      showModal(false);
      return;
    }

    const price = state.card.price || {};
    const takeWays = Array.isArray(price.takeWayList) ? price.takeWayList : [];
    const body = {
      sessionId: state.sessionId,
      confirm: true,
      takeWayCode: takeWays.length && takeWays[0] ? takeWays[0].code || null : null,
    };
    if (state.scene === "delivery") {
      // 备注字数校验（≤50 字）
      const remark = els.remarkInput.value.trim();
      if (remark.length > 50) {
        markInvalid(els.remarkInput, "备注不能超过 50 字");
        return;
      }
      body.remark = remark || null;
    }

    setBusy(els.doOrderBtn, true, "下单中…");
    try {
      const r = await api("/api/order", bodyWithToken(body));
      showModal(false);
      // 下单成功后弹出「结果」弹窗，展示订单号与支付引导（而非只弹一句 toast）
      state.lastOrderId = (r.order && r.order.orderId) || null;
      renderOrderResult(r.order || {});
      openModal(els.orderResultModal);
      // 下单后重置会话与卡状态，避免「二次下单」死锁
      state.sessionId = null;
      state.card = null;
      els.orderBtn.disabled = true;
      els.orderBtn.textContent = "已下单";
    } catch (e) {
      toast(e.message, "error");
    } finally {
      // 恢复按钮状态：先恢复 loading，再根据勾选态决定 disabled
      setBusy(els.doOrderBtn, false);
      if (els.confirmCheck) els.doOrderBtn.disabled = !els.confirmCheck.checked;
    }
  });

  // 渲染下单结果弹窗：订单号 + 支付引导
  function renderOrderResult(order) {
    const orderId = order.orderId || "(未返回)";
    const isOffline = !!order.offline;
    const status = order.status || "待支付";
    els.orderResultTitle.textContent = isOffline ? "🧪 下单成功（离线演示）" : "🎉 下单成功";

    const rows = [];
    rows.push(mRow("订单号", orderId));
    rows.push(mRow("状态", `<span class="${status === "待支付" ? "order-status-wait" : "order-status-ok"}">${escapeHtml(status)}</span>`));
    if (isOffline) {
      rows.push(mRow("说明", "这是离线假订单，未产生真实交易"));
    }
    els.orderResultBody.innerHTML = rows.join("");

    // 离线假订单没有真实支付，隐藏「查支付状态」按钮；联网才显示
    els.checkOrderStatusBtn.hidden = isOffline;
  }

  // ---- 下单结果弹窗 ----
  els.closeOrderResultBtn.addEventListener("click", () => {
    closeModal(els.orderResultModal);
  });

  els.checkOrderStatusBtn.addEventListener("click", async () => {
    if (!state.lastOrderId) return toast("没有可查询的订单", "error");
    if (state.mode === "live" && !token) return toast("请先填写 MCP Token", "error");
    setBusy(els.checkOrderStatusBtn, true, "查询中…");
    try {
      const r = await api("/api/order-status", bodyWithToken({ orderId: state.lastOrderId }));
      const o = r.order || {};
      const status = o.status || "未知";
      toast(`订单 ${o.orderId} 当前状态：${status}`, "success");
      // 更新结果弹窗里的状态行
      renderOrderResult({ orderId: o.orderId, status: status, offline: o.offline });
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setBusy(els.checkOrderStatusBtn, false);
    }
  });

  // ---- 初始化 ----
  bindModalBehaviors(els.confirmModal);
  bindModalBehaviors(els.orderResultModal);
  els.remarkInput.addEventListener("input", () => clearInvalid(els.remarkInput));
  refreshTokenField();
  syncModeChips();
  updateOpenBtn();
  checkHealth();
  renderConstraints();

  // 全局兜底：任何未捕获异常都给出可见反馈，杜绝「点了没反应」的静默失败
  window.addEventListener("error", (ev) => {
    console.error("[麦门盲盒] 未捕获错误：", ev.error || ev.message);
    toast("页面发生异常，请刷新后重试", "error");
  });
  window.addEventListener("unhandledrejection", (ev) => {
    console.error("[麦门盲盒] 未处理的 Promise 拒绝：", ev.reason);
    toast("操作失败，请重试", "error");
  });
})();
