/* ============================================================================
 * گاد استار — Telegram Mini App (WebApp) frontend, v1
 * Single-page app: index.html + style.css + this file. No build step,
 * no dependencies except the Telegram WebApp script.
 *
 * --- PHOTO HANDLING (decision) ---
 * Telegram WebApp `sendData()` only carries a SMALL TEXT payload back to the
 * bot (and there is no file-upload channel inside a WebApp). A downscaled
 * data-URL preview is NOT sent either: it is unreliable (size limits, and
 * the bot cannot verify a payment from a tiny preview).
 * So for receipt photos, ad media and support photos the app sends ONLY
 * metadata in the sendData payload:
 *   receipt_kind: "photo", receipt_file_name, receipt_file_size
 *   (and for ads: media_file_name; for support: has_photo, photo_file_name)
 * The bot then asks the user IN THE BOT CHAT to send the photo as a normal
 * Telegram photo message, and matches it to the order/request by order
 * number (or by user id). The success screen tells the user exactly that.
 * The file is still previewed locally (object URL) so the user sees what
 * they picked; nothing is uploaded anywhere from the WebApp.
 *
 * --- Telegram API use ---
 * ready() + expand() on load. BackButton drives in-app back navigation
 * (shown on every screen except home). HapticFeedback on taps, guarded.
 * Submissions go through the Supabase Edge Function «godstar-api»
 * (submitPayload): the app POSTs {action:"submit", initData, payload}
 * and the function stores it in the webapp_inbox table; the bot polls
 * that table (every 10s), validates initData with the bot token, and
 * processes the payload exactly like a web_app_data message. That is
 * why an order or a wallet charge registers no matter where the Mini
 * App was launched from — blue menu button, keyboard button, or inline
 * button. tg.sendData is only a fallback when the function call fails
 * AND the launch is a keyboard launch (query_id absent in initData);
 * from a menu launch sendData never reaches the bot, so it is not
 * attempted there. «سفارش‌های من» and the wallet balance are fetched
 * the same way ({action:"my-data"}) and show the user's REAL orders
 * and balance inside the Mini App.
 * MainButton is deliberately NOT used: every screen already has its own
 * in-page submit button, and a second MainButton would risk double
 * submits. themeParams are deliberately ignored: v1 is the minimal LIGHT
 * theme by design, even inside Telegram.
 * When Telegram.WebApp is absent (plain browser) the app runs in demo
 * mode with demo data; submits are logged to the console and the
 * success screen is shown locally instead of closing the WebApp.
 *
 * --- payloads sent to the bot (exact JSON keys) ---
 * order:        {type:"order", service:"ads"|"members"|"bot",
 *                title, sub, channel_id, amount_usdt, discount_code,
 *                discount_usdt, payable_usdt, pay_from_wallet,
 *                receipt_kind:"photo"|"hash"|null, receipt_hash,
 *                receipt_file_name, receipt_file_size,
 *                ...service fields: ads->{views, media, ad_text,
 *                target_channels, media_file_name};
 *                members->{quantity};
 *                bot->{bot_type:"management"|"custom", plan_months,
 *                plan_price_usdt, notes}}
 * wallet_charge: {type:"wallet_charge", amount_usdt,
 *                receipt_kind, receipt_hash, receipt_file_name,
 *                receipt_file_size}
 * reorder:     {type:"reorder", order_no}
 * support_message: {type:"support_message", to:"support"|"admin",
 *                text, has_photo, photo_file_name, photo_file_size}
 * Discount codes are validated client-side for FORMAT only (and the demo
 * code GOD10 = 10% is applied locally for display). The SERVER (bot)
 * recomputes the discount and the payable amount; never trust the
 * client-side numbers.
 * ========================================================================== */
"use strict";

var tg = (window.Telegram && window.Telegram.WebApp) ? window.Telegram.WebApp : null;
var DEMO = !tg;

/* آدرس Edge Function سوپابیس: مینی‌اپ سفارش/شارژ/پیام را اینجا POST
   می‌کند و تابع آن را در جدول webapp_inbox می‌گذارد؛ ربات همان را
   می‌خواند و پردازش می‌کند. به همین دلیل ثبت سفارش به دکمه‌ی بازشدن
   مینی‌اپ وابسته نیست — از دکمه‌ی آبی منو هم ثبت می‌شود. */
var FUNCTION_URL = "https://czxubuwpwtqzxvhniovu.supabase.co/functions/v1/godstar-api";
/* sendData فقط وقتی کار می‌کند که مینی‌اپ از دکمه‌ی کیبورد باز شده
   باشد (query_id در initData نباشد)؛ فقط به‌عنوان جایگزینِ اضطراری
   وقتی فراخوانی Edge Function ناموفق باشد استفاده می‌شود. */
var CAN_SEND = DEMO || !(tg && tg.initDataUnsafe && tg.initDataUnsafe.query_id);

/* ---------------- helpers ---------------- */
function $(id) { return document.getElementById(id); }
function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

/* Persian digits, e.g. 50000 -> "۵۰٬۰۰۰", 13.5 -> "۱۳٫۵" */
function fa(n) {
  if (n === null || n === undefined || n === "") return "";
  return Number(n).toLocaleString("fa-IR", { maximumFractionDigits: 1 });
}
function faTether(n) { return fa(n) + " تتر"; }
function haptic(kind) {
  try {
    if (tg && tg.HapticFeedback) {
      if (kind === "light" || kind === "medium" || kind === "heavy") tg.HapticFeedback.impactOccurred(kind);
      else tg.HapticFeedback.selectionChanged();
    }
  } catch (e) { /* haptics are best-effort */ }
}
function submitPayload(payload) {
  /* پی‌لود را از راه Edge Function به ربات می‌رساند؛ خروجی Promise
     با true یعنی سفارش/پیام در صندوق ربات ثبت شد (ربات چند ثانیه بعد
     آن را پردازش می‌کند و در گفتگو تایید می‌فرستد). در حالت نمونه
     (مرورگر) فقط در کنسول نوشته می‌شود. اگر فراخوانی تابع ناموفق
     باشد و بازشدن از کیبورد باشد، به‌عنوان جایگزین sendData زده می‌شود. */
  if (DEMO) {
    console.log("[GodStar demo] submit payload:", JSON.stringify(payload));
    return Promise.resolve(true);
  }
  return fetch(FUNCTION_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "submit", initData: tg ? tg.initData : "", payload: payload })
  }).then(function (res) {
    if (!res.ok) throw new Error("http " + res.status);
    return res.json();
  }).then(function (data) {
    return !!(data && data.ok);
  }).catch(function (err) {
    console.log("[GodStar] submit via function failed, trying sendData fallback:", err);
    if (CAN_SEND && tg && typeof tg.sendData === "function") {
      try { tg.sendData(JSON.stringify(payload)); return true; } catch (e) { return false; }
    }
    return false;
  });
}
function submitFailed(kind) {
  var msg = "ثبت نشد؛ لطفاً دوباره تلاش کن.";
  if (tg && tg.showAlert) { try { tg.showAlert(msg); } catch (e) {} }
  var note = kind === "pay" ? $("pay-note") : kind === "wallet" ? $("wallet-note") : null;
  if (note) { note.textContent = msg; note.classList.remove("hidden"); }
}
function copyText(text, btn) {
  function done() {
    if (!btn) return;
    var old = btn.textContent;
    btn.textContent = "کپی شد";
    setTimeout(function () { btn.textContent = old; }, 1600);
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text); done(); });
  } else { fallbackCopy(text); done(); }
}
function deriveReferralCode(userId) {
  /* کد معرفی از روی آیدی تلگرام — همان تابع در pricing.py است و باید
     همان خروجی را بدهد (FNV-1a سی‌ودوبیتی، GOD + شش رقم). */
  var h = 2166136261;
  var s = String(userId);
  for (var i = 0; i < s.length; i++) {
    h = Math.imul(h ^ s.charCodeAt(i), 16777619) >>> 0;
  }
  return "GOD" + (100000 + (h % 900000));
}
function fallbackCopy(text) {
  var ta = document.createElement("textarea");
  ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
  document.body.appendChild(ta); ta.select();
  try { document.execCommand("copy"); } catch (e) {}
  document.body.removeChild(ta);
}

/* ---------------- demo / shared state ---------------- */
var WALLET_ADDRESS = "0x747f4B72a967fC504a77Ba38a681939dDeB6F793";
var BOT_USERNAME = "GodStarShopBot"; /* ربات اصلی؛ هرگز GodStarServiceBot (بازنشسته) یا GodStarBot نیست */
var REFERRAL_CODE = "GOD4821"; /* حالت نمونه (مرورگر)؛ داخل تلگرام از روی آیدی کاربر ساخته می‌شود */
var walletBalance = 20; /* demo balance (USDT); real balance comes from the bot later */
var walletKnown = true; /* false داخل تلگرام: موجودی واقعی را وب‌اپ نمی‌داند؛ ربات اعتبارسنجی می‌کند */

var friends = [
  { name: "سارا", status: "active" },   /* "active" | "waiting" */
  { name: "علی",  status: "waiting" }
];

/* Orders list is rendered from this array so the bot can later replace it
   with real JSON of the same shape:
   {service, title, sub, status, statusLabel, statusClass, meta,
    daysLeft?, renew?} */
var orders = [
  { service: "member", title: "خرید ممبر آپلودری", sub: "۵٬۰۰۰ ممبر — GodNews24",
    status: "done", statusLabel: "تکمیل شده", statusClass: "green", meta: "۱۵ تتر — ۲ آذر" },
  { service: "ads", title: "تبلیغات Ads تلگرام", sub: "۵۰٬۰۰۰ بازدید با عکس",
    status: "doing", statusLabel: "در حال انجام", statusClass: "amber", meta: "۳۰ تتر — امروز" },
  { service: "bot", title: "ربات تلگرام", sub: "اشتراک ۳ ماهه — مدیریت کانال",
    status: "active", statusLabel: "فعال", statusClass: "green", meta: "۲۸ تتر — تا ۵ آذر",
    daysLeft: "۳ روز مانده", renew: true }
];

var chatMessages = [
  { mine: true,  text: "سلام، موقع پرداخت این خطا را می‌گیرم", time: "۱۴:۳۲" },
  { mine: true,  photo: true, caption: "اسکرین‌شات خطا", time: "۱۴:۳۲" },
  { mine: false, text: "سلام! ممنون، عکس را دیدم؛ این‌طوری درستش کن:", time: "۱۴:۳۵" },
  { mine: false, photo: true, caption: "آموزش قدم‌به‌قدم", time: "۱۴:۳۵" },
  { mine: true,  text: "ممنون، درست شد", time: "۱۴:۳۶" }
];

var pendingOrder = null;   /* set by a service screen, consumed by payment */
var supportPhoto = null;   /* File picked in the support chat (metadata only) */
var supportTarget = "support"; /* "support" | "admin" — set by «ارتباط با مدیریت» */

/* ---------------- navigation ---------------- */
var navStack = ["home"];
/* «سفارش‌ها» و «پشتیبانی» صفحه‌ی جدا نیستند؛ پنل‌های کشویی صفحه‌ی
   اصلی‌اند (PANELS). فقط صفحه‌های فرعی (جزئیات سرویس، پرداخت، کیف
   پول، موفقیت) اینجا صفحه‌اند. */
var SCREEN_IDS = { home: "screen-home", ads: "screen-ads", member: "screen-member",
  bot: "screen-bot", payment: "screen-payment", wallet: "screen-wallet",
  success: "screen-success" };

/* پنل‌های اصلی: 0 خدمات، 1 سفارش‌های من، 2 پشتیبانی. در چیدمان راست‌به‌چپ
   پنل بعدی سمت چپ است؛ پس برای دیدنش تراک باید به راست بلغزد
   (translateX مثبت) — کشیدن انگشت به راست = پنل بعدی. */
var PANELS = ["home", "orders", "support"];
var currentPanel = 0;
var homePanel = 0; /* پنلی که موقع باز کردن صفحه‌ی فرعی فعال بود، برای برگشت */

function setPanel(i, animate) {
  var next = Math.max(0, Math.min(PANELS.length - 1, i));
  var changed = next !== currentPanel;
  currentPanel = next;
  var track = $("swipe-track");
  if (track) {
    track.classList.toggle("no-anim", animate === false);
    track.style.transform = "translateX(" + (currentPanel * (100 / PANELS.length)) + "%)";
  }
  $all(".tab").forEach(function (t, j) {
    t.classList.toggle("active", j === currentPanel);
  });
  if (changed) window.scrollTo(0, 0);
  /* باز کردن پنل سفارش‌ها، داده‌ی واقعی را تازه می‌کند. */
  if (PANELS[currentPanel] === "orders" && !DEMO) loadMyData();
}

function bindSwipe() {
  var el = $("swipe-main");
  var track = $("swipe-track");
  if (!el || !track) return;
  var startX = 0, startY = 0, dragging = false;
  var basePct = 0;
  el.addEventListener("touchstart", function (e) {
    var t = e.touches[0];
    startX = t.clientX; startY = t.clientY; dragging = false;
    basePct = currentPanel * (100 / PANELS.length);
    track.classList.add("no-anim");
  }, { passive: true });
  el.addEventListener("touchmove", function (e) {
    var t = e.touches[0];
    var dx = t.clientX - startX, dy = t.clientY - startY;
    if (!dragging && Math.abs(dx) > 12 && Math.abs(dx) > Math.abs(dy)) dragging = true;
    if (dragging) {
      e.preventDefault();
      var w = track.clientWidth;
      var pct = basePct + (dx / w) * 100;
      var max = (PANELS.length - 1) * (100 / PANELS.length);
      pct = Math.max(-8, Math.min(max + 8, pct));
      track.style.transform = "translateX(" + pct + "%)";
    }
  }, { passive: false });
  function endSwipe(e) {
    track.classList.remove("no-anim");
    if (!dragging) { setPanel(currentPanel); return; }
    var dx = e.changedTouches[0].clientX - startX;
    var w = el.clientWidth;
    var threshold = Math.max(60, w * 0.2);
    if (dx > threshold) setPanel(currentPanel + 1);
    else if (dx < -threshold) setPanel(currentPanel - 1);
    else setPanel(currentPanel);
  }
  el.addEventListener("touchend", endSwipe);
  el.addEventListener("touchcancel", function () {
    track.classList.remove("no-anim");
    setPanel(currentPanel);
  });
}

function showScreen(name, push) {
  var panelIdx = PANELS.indexOf(name);
  if (panelIdx >= 0) {
    /* بخش اصلی: صفحه‌ی جدا باز نمی‌شود؛ همان صفحه‌ی اصلی با پنل درست. */
    $all(".screen").forEach(function (s) { s.classList.remove("active"); });
    $("screen-home").classList.add("active");
    window.scrollTo(0, 0);
    navStack = ["home"];
    setPanel(panelIdx);
    updateBackButton();
    return;
  }
  var id = SCREEN_IDS[name];
  if (!id) return;
  /* رفتن از صفحه‌ی اصلی به صفحه‌ی فرعی: پنل فعلی را نگه دار تا
     دکمه‌ی برگشت به همان پنل برگردد. */
  if (navStack[navStack.length - 1] === "home") homePanel = currentPanel;
  $all(".screen").forEach(function (s) { s.classList.remove("active"); });
  $(id).classList.add("active");
  window.scrollTo(0, 0);
  if (push !== false) {
    if (navStack[navStack.length - 1] !== name) navStack.push(name);
  }
  updateBackButton();
  /* باز کردن کیف پول، داده‌ی واقعی را تازه می‌کند. */
  if (name === "wallet" && !DEMO) loadMyData();
}
function goBack() {
  if (navStack.length > 1) {
    navStack.pop();
    var prev = navStack[navStack.length - 1];
    showScreen(prev, false);
    if (prev === "home") setPanel(homePanel, false);
  }
}
function updateBackButton() {
  if (!tg || !tg.BackButton) return;
  var onHome = navStack[navStack.length - 1] === "home";
  if (onHome) tg.BackButton.hide(); else tg.BackButton.show();
}
function bindNav() {
  $all("[data-nav]").forEach(function (el) {
    el.addEventListener("click", function () {
      haptic("selection");
      showScreen(el.getAttribute("data-nav"));
    });
  });
  $all("[data-back]").forEach(function (el) {
    el.addEventListener("click", function () { haptic("light"); goBack(); });
  });
  if (tg && tg.BackButton) tg.BackButton.onClick(function () { goBack(); });
}

/* ---------------- home: referral + wallet ---------------- */
function renderFriends(list) {
  var box = $("friends-list");
  box.innerHTML = "";
  if (!list || list.length === 0) {
    var p = document.createElement("p");
    p.className = "friends-empty";
    p.textContent = "هنوز کسی با کدت سفارش نداده";
    box.appendChild(p);
    return;
  }
  list.forEach(function (f) {
    var row = document.createElement("div");
    row.className = "friend-row";
    var who = document.createElement("div");
    who.className = "friend-who";
    var av = document.createElement("span");
    av.className = "avatar";
    av.textContent = f.name.charAt(0);
    var nm = document.createElement("span");
    nm.textContent = f.name;
    who.appendChild(av); who.appendChild(nm);
    var chip = document.createElement("span");
    chip.className = "chip-status " + (f.status === "active" ? "green" : "amber");
    chip.textContent = f.status === "active" ? "فعال شد" : "در انتظار سفارش";
    row.appendChild(who); row.appendChild(chip);
    box.appendChild(row);
  });
}
function refreshWalletLabels() {
  /* داخل تلگرام موجودی واقعی معلوم نیست؛ به‌جای خط تیره صفر نشان داده می‌شود. */
  var label = faTether(walletBalance);
  $("wallet-balance-home").textContent = label;
  $("wallet-balance-pay").textContent = label;
  $("wallet-balance-check").textContent = label;
  $("wallet-balance-wallet").textContent = label;
}
function bindHome() {
  $("btn-charge-home").addEventListener("click", function () { haptic("light"); showScreen("wallet"); });
  $("btn-hero-start").addEventListener("click", function () {
    haptic("light");
    var anchor = $("services-anchor");
    if (anchor) anchor.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  $("btn-share-referral").addEventListener("click", function () {
    haptic("light");
    var url = "https://t.me/" + BOT_USERNAME + "?start=" + REFERRAL_CODE;
    var text = "با کد معرفی من در گاد استار سفارش بده؛ هر دو ۱۰٪ تخفیف می‌گیریم!";
    if (tg && tg.openTelegramLink) {
      tg.openTelegramLink("https://t.me/share/url?url=" + encodeURIComponent(url) +
        "&text=" + encodeURIComponent(text));
    } else if (navigator.share) {
      navigator.share({ title: "گاد استار", text: text, url: url }).catch(function () {});
    } else {
      copyText(url, $("btn-share-referral"));
    }
  });
  $all(".service-row").forEach(function (row) {
    row.addEventListener("click", function () {
      haptic("selection");
      showScreen(row.getAttribute("data-service"));
    });
  });
}

/* ---------------- saved-ID chips ---------------- */
function bindChips() {
  $all(".chips").forEach(function (wrap) {
    var targetId = wrap.getAttribute("data-chips-for");
    $all(".chip", wrap).forEach(function (chip) {
      chip.addEventListener("click", function () {
        haptic("selection");
        var input = $(targetId);
        if (input) {
          input.value = chip.getAttribute("data-id");
          input.dispatchEvent(new Event("input", { bubbles: true }));
          input.focus();
        }
      });
    });
  });
}

/* ---------------- copy buttons ---------------- */
function bindCopyButtons() {
  $all("[data-copy]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      haptic("light");
      copyText(btn.getAttribute("data-copy"), btn);
    });
  });
}

/* ---------------- file preview (local only, never uploaded) ---------------- */
function bindFilePreview(inputId, labelId, previewId) {
  var input = $(inputId);
  if (!input) return null;
  var state = { file: null };
  input.addEventListener("change", function () {
    var f = input.files && input.files[0];
    state.file = f || null;
    var label = $(labelId), preview = $(previewId);
    if (f) {
      if (f.type && f.type.indexOf("image/") === 0 && preview) {
        preview.src = URL.createObjectURL(f);
        preview.classList.remove("hidden");
        if (label) label.textContent = f.name;
      } else if (label) {
        label.textContent = f.name;
        if (preview) preview.classList.add("hidden");
      }
    }
  });
  return state;
}

/* ---------------- ADS ---------------- */
var adsState = { views: 50000, media: "none", mediaFile: null };
var ADS_RATE = { none: 5, photo: 6, video: 7 };
var ADS_MEDIA_LABEL = { none: "بدون عکس و فیلم", photo: "با عکس", video: "با فیلم" };

function adsPrice() { return (adsState.views / 10000) * ADS_RATE[adsState.media]; }
function renderAds() {
  $("ads-views-label").textContent = fa(adsState.views) + " بازدید";
  $("ads-slider").value = String(adsState.views);
  $all("#ads-pills button").forEach(function (b) {
    b.classList.toggle("active", Number(b.getAttribute("data-v")) === adsState.views);
  });
  var price = adsPrice();
  $("ads-price").textContent = faTether(price);
  $("ads-order-price").textContent = faTether(price);
  $("ads-price-detail").textContent = fa(adsState.views) + " بازدید × " +
    fa(ADS_RATE[adsState.media]) + " تتر — " + ADS_MEDIA_LABEL[adsState.media];
  var ok = $("ads-text").value.trim().length > 0 && $("ads-channel").value.trim().length > 0;
  $("ads-order").disabled = !ok;
  $("ads-note").classList.toggle("hidden", ok);
}
function bindAds() {
  $("ads-slider").addEventListener("input", function () {
    adsState.views = Number($("ads-slider").value); renderAds();
  });
  $("ads-minus").addEventListener("click", function () {
    adsState.views = Math.max(10000, adsState.views - 10000); haptic("light"); renderAds();
  });
  $("ads-plus").addEventListener("click", function () {
    adsState.views = Math.min(1000000, adsState.views + 10000); haptic("light"); renderAds();
  });
  $all("#ads-pills button").forEach(function (b) {
    b.addEventListener("click", function () {
      adsState.views = Number(b.getAttribute("data-v")); haptic("selection"); renderAds();
    });
  });
  $all("#ads-media input").forEach(function (r) {
    r.addEventListener("change", function () {
      adsState.media = r.value; haptic("selection");
      var wrap = $("ads-upload-wrap");
      if (adsState.media === "none") { wrap.classList.add("hidden"); }
      else {
        wrap.classList.remove("hidden");
        var isPhoto = adsState.media === "photo";
        $("ads-upload-title").textContent = isPhoto ? "آپلود عکس تبلیغ" : "آپلود فیلم تبلیغ";
        $("ads-upload-label").textContent = isPhoto ?
          "عکس تبلیغ را برای بارگذاری انتخاب کن" : "فیلم تبلیغ را برای بارگذاری انتخاب کن";
      }
      renderAds();
    });
  });
  adsState.mediaFile = bindFilePreview("ads-media-file", "ads-upload-label", "ads-upload-preview");
  ["ads-text", "ads-channel"].forEach(function (id) {
    $(id).addEventListener("input", renderAds);
  });
  $("ads-order").addEventListener("click", function () {
    haptic("medium");
    var price = adsPrice();
    openPayment({
      service: "ads",
      title: "تبلیغات Ads تلگرام",
      sub: fa(adsState.views) + " بازدید " + ADS_MEDIA_LABEL[adsState.media] + " — " + $("ads-channel").value.trim(),
      amount: price,
      views: adsState.views,
      media: adsState.media,
      ad_text: $("ads-text").value.trim(),
      channel_id: $("ads-channel").value.trim(),
      target_channels: $("ads-targets").value.trim(),
      media_file_name: adsState.mediaFile && adsState.mediaFile.file ? adsState.mediaFile.file.name : null
    });
  });
}

/* ---------------- MEMBERS ---------------- */
var memberState = { count: 5000 };
function memberPrice() { return (memberState.count / 1000) * 3; }
function renderMember() {
  $("member-count-label").textContent = fa(memberState.count) + " ممبر";
  $("member-slider").value = String(memberState.count);
  $all("#member-pills button").forEach(function (b) {
    b.classList.toggle("active", Number(b.getAttribute("data-v")) === memberState.count);
  });
  var price = memberPrice();
  $("member-price").textContent = faTether(price);
  $("member-order-price").textContent = faTether(price);
  var ok = $("member-channel").value.trim().length > 0;
  $("member-order").disabled = !ok;
  $("member-note").classList.toggle("hidden", ok);
}
function bindMember() {
  $("member-slider").addEventListener("input", function () {
    memberState.count = Number($("member-slider").value); renderMember();
  });
  $("member-minus").addEventListener("click", function () {
    memberState.count = Math.max(1000, memberState.count - 1000); haptic("light"); renderMember();
  });
  $("member-plus").addEventListener("click", function () {
    memberState.count = Math.min(20000, memberState.count + 1000); haptic("light"); renderMember();
  });
  $all("#member-pills button").forEach(function (b) {
    b.addEventListener("click", function () {
      memberState.count = Number(b.getAttribute("data-v")); haptic("selection"); renderMember();
    });
  });
  $("member-channel").addEventListener("input", renderMember);
  $("member-order").addEventListener("click", function () {
    haptic("medium");
    openPayment({
      service: "members",
      title: "خرید ممبر آپلودری",
      sub: fa(memberState.count) + " ممبر — " + $("member-channel").value.trim(),
      amount: memberPrice(),
      quantity: memberState.count,
      channel_id: $("member-channel").value.trim()
    });
  });
}

/* ---------------- BOT ---------------- */
var botState = { type: "management", plan: "1" };
var BOT_PLANS = { "1": 10, "3": 28, "6": 54, "12": 100 };
var BOT_PLAN_LABEL = { "1": "۱ ماهه", "3": "۳ ماهه", "6": "۶ ماهه", "12": "۱۲ ماهه" };
function botPrice() { return botState.type === "custom" ? null : BOT_PLANS[botState.plan]; }
function renderBot() {
  var isCustom = botState.type === "custom";
  $("bot-plans-wrap").classList.toggle("hidden", isCustom);
  $("bot-custom-wrap").classList.toggle("hidden", !isCustom);
  var price = botPrice();
  if (isCustom) {
    $("bot-price-detail").textContent = "ربات اختصاصی — قیمت پس از بررسی";
    $("bot-price").textContent = "توافقی";
    $("bot-order-price").textContent = "";
    $("bot-order").firstChild.textContent = "ثبت درخواست";
  } else {
    $("bot-price-detail").textContent = "اشتراک " + BOT_PLAN_LABEL[botState.plan] + " — ربات مدیریت کانال";
    $("bot-price").textContent = faTether(price);
    $("bot-order-price").textContent = faTether(price);
    $("bot-order").firstChild.textContent = "ثبت سفارش — ";
  }
  var ok = $("bot-channel").value.trim().length > 0;
  $("bot-order").disabled = !ok;
  $("bot-note").classList.toggle("hidden", ok);
}
function bindBot() {
  $all("#bot-type input").forEach(function (r) {
    r.addEventListener("change", function () {
      botState.type = r.value; haptic("selection"); renderBot();
    });
  });
  $all("#bot-plans input").forEach(function (r) {
    r.addEventListener("change", function () {
      botState.plan = r.value; haptic("selection"); renderBot();
    });
  });
  $("bot-channel").addEventListener("input", renderBot);
  $("bot-order").addEventListener("click", function () {
    haptic("medium");
    if (botState.type === "custom") {
      openPayment({
        service: "bot",
        title: "ربات تلگرام",
        sub: "ربات اختصاصی — " + $("bot-channel").value.trim(),
        amount: 0,
        bot_type: "custom",
        plan_months: null,
        plan_price_usdt: null,
        channel_id: $("bot-channel").value.trim(),
        notes: $("bot-notes").value.trim()
      });
    } else {
      openPayment({
        service: "bot",
        title: "ربات تلگرام",
        sub: "اشتراک " + BOT_PLAN_LABEL[botState.plan] + " — مدیریت کانال",
        amount: BOT_PLANS[botState.plan],
        bot_type: "management",
        plan_months: Number(botState.plan),
        plan_price_usdt: BOT_PLANS[botState.plan],
        channel_id: $("bot-channel").value.trim(),
        notes: $("bot-notes").value.trim()
      });
    }
  });
}

/* ---------------- PAYMENT ---------------- */
var payState = { discountCode: "", discount: 0, receiptKind: "photo", receiptFile: null };
var DEMO_DISCOUNTS = { "GOD10": 0.10 }; /* display-only; the bot recomputes */

function payPayable() {
  if (!pendingOrder) return 0;
  return Math.max(0, pendingOrder.amount - payState.discount);
}
function renderPayment() {
  if (!pendingOrder) return;
  $("pay-title").textContent = pendingOrder.title;
  $("pay-sub").textContent = pendingOrder.sub;
  $("pay-amount").textContent = pendingOrder.amount > 0 ? faTether(pendingOrder.amount) : "توافقی";
  var hasCode = payState.discountCode.length > 0;
  $("pay-discount-label").textContent = hasCode ?
    ("تخفیف با کد " + payState.discountCode + ":") : "تخفیف:";
  $("pay-discount").textContent = payState.discount > 0 ?
    (fa(payState.discount) + " تتر−") : "۰ تتر";
  var payable = payPayable();
  $("pay-payable").textContent = pendingOrder.amount > 0 ? faTether(payable) : "توافقی";
  $("pay-submit-price").textContent = pendingOrder.amount > 0 ? faTether(payable) : "توافقی";
  $("discount-applied").classList.toggle("hidden", !(hasCode && payState.discount > 0));
  var walletPay = $("pay-from-wallet").checked;
  var receiptOk = payState.receiptKind === "photo"
    ? !!(payState.receiptFile && payState.receiptFile.file)
    : $("pay-hash").value.trim().length > 0;
  var walletOk = walletPay && pendingOrder.amount > 0 &&
    (!walletKnown || walletBalance >= payable); /* موجودی نامعلوم = ربات بررسی می‌کند */
  /* پرداخت از کیف پول: روش‌های دستی (آدرس + رسید) پنهان می‌شوند —
     فقط وقتی کیف پول واقعاً کافی است؛ اگر موجودی کم باشد روش دستی
     می‌ماند تا کاربر بتواند با تتر پرداخت کند */
  $("pay-manual-wrap").classList.toggle("hidden", walletPay && walletOk);
  $("pay-submit").disabled = !(receiptOk || walletOk);
  $("pay-note").classList.toggle("hidden", receiptOk || walletOk);
  if (walletOk) $("pay-note").textContent = "";
  else $("pay-note").textContent = "برای ثبت نهایی، رسید یا کد هش را ارسال کن";
}
function applyDiscountCode() {
  var raw = $("discount-code").value.trim().toUpperCase();
  /* client-side format check only: 3-12 Latin letters/digits */
  var okFormat = /^[A-Z0-9]{3,12}$/.test(raw);
  $("discount-invalid").classList.add("hidden");
  if (raw === "") { payState.discountCode = ""; payState.discount = 0; renderPayment(); return; }
  if (!okFormat) {
    payState.discountCode = raw; payState.discount = 0;
    $("discount-invalid").classList.remove("hidden");
    renderPayment(); return;
  }
  payState.discountCode = raw;
  var rate = DEMO_DISCOUNTS[raw];
  payState.discount = (rate && pendingOrder) ? pendingOrder.amount * rate : 0;
  if (!rate) $("discount-invalid").classList.remove("hidden");
  renderPayment();
}
function openPayment(order) {
  pendingOrder = order;
  payState.discountCode = "";
  payState.discount = 0;
  payState.receiptKind = "photo";
  /* reset the shared file state IN PLACE — do not replace the object, the
     change listener from bindFilePreview writes into this same object */
  if (payState.receiptFile) payState.receiptFile.file = null;
  $("pay-receipt-file").value = "";
  $("discount-code").value = ""; /* کد تخفیف را خود کاربر وارد می‌کند؛ چیزی از پیش پر نمی‌شود */
  $("pay-from-wallet").checked = false;
  $("pay-hash").value = "";
  var photoRadio = document.querySelector('input[name="pay-receipt"][value="photo"]');
  if (photoRadio) photoRadio.checked = true;
  $("pay-upload-wrap").classList.remove("hidden");
  $("pay-hash-wrap").classList.add("hidden");
  $("pay-upload-preview").classList.add("hidden");
  $("pay-upload-label").textContent = "عکس فیش را برای بارگذاری انتخاب کن";
  applyDiscountCode();
  renderPayment();
  showScreen("payment");
}
function bindPayment() {
  payState.receiptFile = bindFilePreview("pay-receipt-file", "pay-upload-label", "pay-upload-preview");
  /* re-render once a receipt file is picked (bindFilePreview has no hook,
     so listen on the same input again) */
  $("pay-receipt-file").addEventListener("change", function () { renderPayment(); });
  $("discount-code").addEventListener("input", applyDiscountCode);
  $all("#pay-receipt input").forEach(function (r) {
    r.addEventListener("change", function () {
      payState.receiptKind = r.value; haptic("selection");
      $("pay-upload-wrap").classList.toggle("hidden", r.value !== "photo");
      $("pay-hash-wrap").classList.toggle("hidden", r.value !== "hash");
      renderPayment();
    });
  });
  $("pay-hash").addEventListener("input", renderPayment);
  $("pay-from-wallet").addEventListener("change", renderPayment);
  $("btn-charge-pay").addEventListener("click", function () { haptic("light"); showScreen("wallet"); });
  $("pay-submit").addEventListener("click", function () {
    if (!pendingOrder) return;
    haptic("medium");
    var walletPay = $("pay-from-wallet").checked &&
      (!walletKnown || walletBalance >= payPayable()) && pendingOrder.amount > 0;
    var payload = {
      type: "order",
      service: pendingOrder.service,
      title: pendingOrder.title,
      sub: pendingOrder.sub,
      channel_id: pendingOrder.channel_id || null,
      amount_usdt: pendingOrder.amount,
      discount_code: payState.discountCode || null,
      discount_usdt: payState.discount,
      payable_usdt: payPayable(),
      pay_from_wallet: walletPay,
      receipt_kind: walletPay ? null : payState.receiptKind,
      receipt_hash: (!walletPay && payState.receiptKind === "hash") ? $("pay-hash").value.trim() : null,
      receipt_file_name: (!walletPay && payState.receiptKind === "photo" && payState.receiptFile.file)
        ? payState.receiptFile.file.name : null,
      receipt_file_size: (!walletPay && payState.receiptKind === "photo" && payState.receiptFile.file)
        ? payState.receiptFile.file.size : null
    };
    if (pendingOrder.service === "ads") {
      payload.views = pendingOrder.views;
      payload.media = pendingOrder.media;
      payload.ad_text = pendingOrder.ad_text;
      payload.target_channels = pendingOrder.target_channels || null;
      payload.media_file_name = pendingOrder.media_file_name;
    } else if (pendingOrder.service === "members") {
      payload.quantity = pendingOrder.quantity;
    } else if (pendingOrder.service === "bot") {
      payload.bot_type = pendingOrder.bot_type;
      payload.plan_months = pendingOrder.plan_months;
      payload.plan_price_usdt = pendingOrder.plan_price_usdt;
      payload.notes = pendingOrder.notes || null;
    }
    var note = walletPay
      ? "پرداخت از کیف پول انجام شد؛ نتیجه‌ی بررسی را با پیام بهت خبر می‌دهیم."
      : (payState.receiptKind === "photo"
        ? "عکس فیش را در گفتگو با ربات (@" + BOT_USERNAME + ") ارسال کن تا سفارشت تایید شود."
        : "نتیجه‌ی بررسی پرداخت را با پیام بهت خبر می‌دهیم.");
    $("pay-submit").disabled = true;
    submitPayload(payload).then(function (ok) {
      if (!ok) { submitFailed("pay"); renderPayment(); return; }
      showSuccess("سفارش شما ثبت شد", null, note);
    });
  });
}

/* ---------------- WALLET CHARGE ---------------- */
var walletState = { amount: 20, receiptKind: "photo", receiptFile: null };
function renderWallet() {
  $("wallet-amount-label").textContent = faTether(walletState.amount);
  $("wallet-submit-price").textContent = faTether(walletState.amount);
  $all("#wallet-pills button").forEach(function (b) {
    b.classList.toggle("active", Number(b.getAttribute("data-v")) === walletState.amount);
  });
  var receiptOk = walletState.receiptKind === "photo"
    ? !!(walletState.receiptFile && walletState.receiptFile.file)
    : $("wallet-hash").value.trim().length > 0;
  $("wallet-submit").disabled = !(receiptOk && walletState.amount > 0);
  $("wallet-note").classList.toggle("hidden", receiptOk && walletState.amount > 0);
}
function bindWallet() {
  walletState.receiptFile = bindFilePreview("wallet-receipt-file", "wallet-upload-label", "wallet-upload-preview");
  $("wallet-receipt-file").addEventListener("change", function () { renderWallet(); });
  $all("#wallet-pills button").forEach(function (b) {
    b.addEventListener("click", function () {
      walletState.amount = Number(b.getAttribute("data-v"));
      $("wallet-custom").value = "";
      haptic("selection"); renderWallet();
    });
  });
  $("wallet-custom").addEventListener("input", function () {
    var v = Math.floor(Number($("wallet-custom").value));
    if (v > 0) { walletState.amount = v; renderWallet(); }
  });
  $all("#wallet-receipt input").forEach(function (r) {
    r.addEventListener("change", function () {
      walletState.receiptKind = r.value; haptic("selection");
      $("wallet-upload-wrap").classList.toggle("hidden", r.value !== "photo");
      $("wallet-hash-wrap").classList.toggle("hidden", r.value !== "hash");
      renderWallet();
    });
  });
  $("wallet-hash").addEventListener("input", renderWallet);
  $("wallet-submit").addEventListener("click", function () {
    haptic("medium");
    var payload = {
      type: "wallet_charge",
      amount_usdt: walletState.amount,
      receipt_kind: walletState.receiptKind,
      receipt_hash: walletState.receiptKind === "hash" ? $("wallet-hash").value.trim() : null,
      receipt_file_name: (walletState.receiptKind === "photo" && walletState.receiptFile.file)
        ? walletState.receiptFile.file.name : null,
      receipt_file_size: (walletState.receiptKind === "photo" && walletState.receiptFile.file)
        ? walletState.receiptFile.file.size : null
    };
    var note = walletState.receiptKind === "photo"
      ? "عکس فیش را در گفتگو با ربات (@" + BOT_USERNAME + ") ارسال کن تا شارژ کیف پولت تایید شود."
      : "نتیجه‌ی بررسی پرداخت را با پیام بهت خبر می‌دهیم.";
    $("wallet-submit").disabled = true;
    submitPayload(payload).then(function (ok) {
      if (!ok) { submitFailed("wallet"); renderWallet(); return; }
      showSuccess("شارژ کیف پول ثبت شد", null, note);
    });
  });
}

/* ---------------- ORDERS ---------------- */
var SERVICE_ICONS = {
  ads: '<span class="svc-icon tint-pink" aria-hidden="true"><svg viewBox="0 0 24 24" width="22" height="22"><path fill="#EB5AA0" d="M12 2.5l2.6 6.2 6.7.5-5.1 4.4 1.6 6.5L12 16.6 6.2 20l1.6-6.5L2.7 9.2l6.7-.5z"/></svg></span>',
  member: '<span class="svc-icon tint-blue" aria-hidden="true"><svg viewBox="0 0 24 24" width="22" height="22"><path fill="#3C82C4" d="M12 12a4.5 4.5 0 1 0-4.5-4.5A4.5 4.5 0 0 0 12 12zm0 2c-3.6 0-7.5 1.8-7.5 4.2V20a1 1 0 0 0 1 1h13a1 1 0 0 0 1-1v-1.8C19.5 15.8 15.6 14 12 14z"/></svg></span>',
  bot: '<span class="svc-icon tint-green" aria-hidden="true"><svg viewBox="0 0 24 24" width="22" height="22"><path fill="#46A064" d="M12 2a1 1 0 0 1 1 1v1.1a6 6 0 0 1 4.6 5.9v5.5a3 3 0 0 1-3 3h-.6v2.3a1 1 0 1 1-2 0v-2.3h-2v2.3a1 1 0 1 1-2 0v-2.3h-.6a3 3 0 0 1-3-3V10A6 6 0 0 1 9 4.1V3a1 1 0 0 1 1-1zm-3.5 8.5a1.5 1.5 0 1 0 1.5 1.5 1.5 1.5 0 0 0-1.5-1.5zm7 0a1.5 1.5 0 1 0 1.5 1.5 1.5 1.5 0 0 0-1.5-1.5zM8 15.5h8a.5.5 0 0 0 .5-.5v-1h-9v1a.5.5 0 0 0 .5.5z"/></svg></span>',
  wallet_charge: '<span class="svc-icon tint-pink" aria-hidden="true"><svg viewBox="0 0 24 24" width="22" height="22"><path fill="#EB5AA0" d="M4 6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2-2V6H6v12h12v-2h2v2a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6zm12 4.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3z"/></svg></span>'
};
function renderOrders(list) {
  var box = $("orders-list");
  box.innerHTML = "";
  if (!list || list.length === 0) {
    var empty = document.createElement("p");
    empty.className = "friends-empty";
    empty.textContent = DEMO
      ? "سفارشی نیست."
      : "هنوز سفارشی ثبت نکردی 🙂 — سفارش‌های واقعی‌ات را با دکمه‌ی «📦 سفارش‌های من» در گفتگو با ربات ببین.";
    box.appendChild(empty);
    if (!DEMO) {
      var btn = document.createElement("button");
      btn.type = "button"; btn.className = "link-pink";
      btn.textContent = "📦 مشاهده سفارش‌ها در ربات";
      btn.addEventListener("click", function () {
        haptic("light");
        submitPayload({ type: "my_orders" }).then(function (ok) {
          if (!ok) { submitFailed(); return; }
          if (tg && tg.showAlert) { try { tg.showAlert("سفارش‌هایت در گفتگو با ربات فرستاده می‌شوند."); } catch (e) {} }
        });
      });
      box.appendChild(btn);
    }
    return;
  }
  list.forEach(function (o) {
    var row = document.createElement("div");
    row.className = "order-row";
    var icon = document.createElement("span");
    icon.innerHTML = SERVICE_ICONS[o.service] || SERVICE_ICONS.ads;
    var main = document.createElement("div");
    main.className = "order-main";
    var t = document.createElement("b"); t.textContent = o.title;
    var s = document.createElement("small"); s.textContent = o.sub;
    main.appendChild(t); main.appendChild(s);
    if (o.daysLeft) {
      var d = document.createElement("small");
      d.className = "days-left"; d.textContent = o.daysLeft;
      main.appendChild(d);
    }
    if (o.renew) {
      var r = document.createElement("button");
      r.type = "button"; r.className = "renew-link"; r.textContent = "تمدید";
      r.addEventListener("click", function () { haptic("light"); showScreen("bot"); });
      main.appendChild(r);
    }
    if (o.order_no && o.canReorder) {
      var rb = document.createElement("button");
      rb.type = "button"; rb.className = "renew-link"; rb.textContent = "🔁 دوباره";
      rb.addEventListener("click", function () {
        haptic("light");
        submitPayload({ type: "reorder", order_no: o.order_no }).then(function (ok) {
          if (!ok) { submitFailed(); return; }
          showSuccess("سفارش دوباره ثبت شد", null, "شماره‌ی سفارش تازه در پیام ربات اعلام می‌شود؛ نتیجه‌ی بررسی را با پیام بهت خبر می‌دهیم.");
        });
      });
      main.appendChild(rb);
    }
    var side = document.createElement("div");
    side.className = "order-side";
    var chip = document.createElement("span");
    chip.className = "chip-status " + (o.statusClass || "green");
    chip.textContent = o.statusLabel;
    var meta = document.createElement("span");
    meta.className = "order-meta"; meta.textContent = o.meta || "";
    side.appendChild(chip); side.appendChild(meta);
    row.appendChild(icon.firstChild || icon);
    row.appendChild(main); row.appendChild(side);
    box.appendChild(row);
  });
}

/* ---------------- سفارش‌های واقعی (Edge Function) ---------------- */
var SERVICE_TITLES = { ads: "تبلیغات Ads تلگرام", members: "خرید ممبر آپلودری",
  bot: "ربات تلگرام", wallet_charge: "شارژ کیف پول" };
var ORDER_STATUS_LABELS = { pending: "در انتظار تایید", confirmed: "تایید شد", rejected: "رد شد" };
var ORDER_STATUS_CLASSES = { pending: "amber", confirmed: "green", rejected: "red" };

function mapRealOrders(real) {
  /* ردیف‌های my-data را به شکل نمایشی renderOrders تبدیل می‌کند. */
  return real.map(function (o) {
    var d = o.details || {};
    var parts = [];
    if (o.service === "ads" && d.views) parts.push(fa(d.views) + " بازدید");
    if (o.service === "members" && d.count) parts.push(fa(d.count) + " ممبر");
    if (o.service === "bot" && d.plan) parts.push("اشتراک " + d.plan);
    if (d.channel_id) parts.push(d.channel_id);
    var sub = parts.join(" — ");
    if (o.status === "rejected" && d.reject_reason) {
      sub += (sub ? " — " : "") + "دلیل رد: " + d.reject_reason;
    }
    var dateStr = "";
    try { dateStr = new Date(o.created_at).toLocaleDateString("fa-IR"); } catch (e) {}
    return {
      service: o.service,
      title: SERVICE_TITLES[o.service] || o.service,
      sub: sub,
      status: o.status,
      statusLabel: ORDER_STATUS_LABELS[o.status] || o.status,
      statusClass: ORDER_STATUS_CLASSES[o.status] || "amber",
      meta: o.order_no + " — " + faTether(o.amount) + (dateStr ? " — " + dateStr : ""),
      order_no: o.order_no,
      canReorder: o.type === "order" &&
        (o.service === "ads" || o.service === "members" || o.service === "bot")
    };
  });
}
function loadMyData() {
  /* سفارش‌های واقعی و موجودی کیف پول را از Edge Function می‌گیرد و
     صفحه‌ی «سفارش‌های من» و برچسب‌های کیف پول را تازه می‌کند. در حالت
     نمونه، همان داده‌ی نمونه‌ی محلی نشان داده می‌شود. */
  if (DEMO) { renderOrders(orders); refreshWalletLabels(); return; }
  var box = $("orders-list");
  if (box) {
    box.innerHTML = "";
    var loading = document.createElement("p");
    loading.className = "friends-empty";
    loading.textContent = "در حال بارگذاری…";
    box.appendChild(loading);
  }
  fetch(FUNCTION_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "my-data", initData: tg ? tg.initData : "" })
  }).then(function (res) {
    if (!res.ok) throw new Error("http " + res.status);
    return res.json();
  }).then(function (data) {
    if (!data || !data.ok) throw new Error("bad response");
    if (typeof data.wallet_balance === "number") {
      walletBalance = data.wallet_balance;
      walletKnown = true;
      refreshWalletLabels();
    }
    renderOrders(mapRealOrders(data.orders || []));
  }).catch(function (err) {
    console.log("[GodStar] my-data failed:", err);
    renderOrders([]); /* حالت خالی فعلی + دکمه‌ی «مشاهده سفارش‌ها در ربات» */
  });
}

/* ---------------- SUPPORT ---------------- */
function mountainSVG(uid) {
  return '<svg viewBox="0 0 300 170" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="عکس">' +
    '<defs><linearGradient id="sky' + uid + '" x1="0" y1="0" x2="0" y2="1">' +
    '<stop offset="0" stop-color="#BFE3F5"/><stop offset="1" stop-color="#E8F4FA"/></linearGradient></defs>' +
    '<rect width="300" height="170" fill="url(#sky' + uid + ')"/>' +
    '<circle cx="248" cy="38" r="20" fill="#F5C542"/>' +
    '<polygon points="0,170 70,70 140,170" fill="#7FA88F"/>' +
    '<polygon points="60,170 150,45 240,170" fill="#5E8F74"/>' +
    '<polygon points="170,170 235,80 300,170" fill="#4E7D63"/>' +
    '</svg>';
}
function renderChat() {
  var box = $("chat-box");
  box.innerHTML = "";
  if (!chatMessages || chatMessages.length === 0) {
    var hint = document.createElement("p");
    hint.className = "friends-empty";
    hint.textContent = "هنوز پیامی نفرستادی — پیامت مستقیم به پشتیبانی می‌رسد؛ جوابش در گفتگو با ربات برایت می‌آید 📩";
    box.appendChild(hint);
    return;
  }
  chatMessages.forEach(function (m, i) {
    var b = document.createElement("div");
    b.className = "bubble " + (m.mine ? "mine" : "theirs") + (m.photo ? " photo" : "");
    if (m.photo) {
      b.innerHTML = mountainSVG("m" + i) + '<span class="cap"></span>';
      b.querySelector(".cap").textContent = m.caption || "";
    } else {
      b.textContent = m.text;
    }
    var t = document.createElement("span");
    t.className = "time"; t.textContent = m.time;
    b.appendChild(t);
    box.appendChild(b);
  });
  box.scrollTop = box.scrollHeight;
}
function nowTimeFa() {
  var d = new Date();
  return d.getHours().toString().padStart(2, "0") + ":" + d.getMinutes().toString().padStart(2, "0");
}
function bindSupport() {
  $all(".faq-item").forEach(function (item) {
    item.querySelector(".faq-q").addEventListener("click", function () {
      haptic("selection");
      $all(".faq-item").forEach(function (other) {
        if (other !== item) other.classList.remove("open");
      });
      item.classList.toggle("open");
    });
  });
  $("btn-contact-admin").addEventListener("click", function () {
    haptic("light");
    supportTarget = "admin";
    $("support-input").focus();
    $("support-input").placeholder = "پیامت به مدیریت را بنویس...";
  });
  $("btn-attach").addEventListener("click", function () {
    haptic("light");
    $("support-photo").click();
  });
  $("support-photo").addEventListener("change", function () {
    var f = $("support-photo").files && $("support-photo").files[0];
    supportPhoto = f || null;
    $("support-photo-note").textContent = f ?
      ("عکس انتخاب شد: " + f.name + " — بعد از ارسال پیام، عکس را در گفتگو با ربات بفرست.") : "";
  });
  function sendSupport() {
    var text = $("support-input").value.trim();
    if (!text && !supportPhoto) return;
    haptic("medium");
    var photo = supportPhoto;
    var payload = {
      type: "support_message",
      to: supportTarget,
      text: text || null,
      has_photo: !!photo,
      photo_file_name: photo ? photo.name : null,
      photo_file_size: photo ? photo.size : null
    };
    $("btn-send-support").disabled = true;
    submitPayload(payload).then(function (ok) {
      $("btn-send-support").disabled = false;
      if (!ok) { submitFailed(); return; }
      chatMessages.push({
        mine: true,
        photo: !!photo,
        caption: photo ? (text || "عکس") : undefined,
        text: photo ? undefined : text,
        time: nowTimeFa()
      });
      $("support-input").value = "";
      supportPhoto = null;
      $("support-photo").value = "";
      $("support-photo-note").textContent = "";
      supportTarget = "support";
      $("support-input").placeholder = "پیامت را بنویس...";
      renderChat();
    });
  }
  $("btn-send-support").addEventListener("click", sendSupport);
  $("support-input").addEventListener("keydown", function (e) {
    if (e.key === "Enter") sendSupport();
  });
}

/* ---------------- SUCCESS ---------------- */
function showSuccess(title, orderNo, note) {
  /* شماره‌ی واقعی سفارش را ربات در پیام چت می‌فرستد؛ وب‌اپ عدد ساختگی
     نشان نمی‌دهد تا با شماره‌ی واقعی قاطی نشود. */
  $("screen-success").querySelector("h2").textContent = title;
  $("success-order-no").textContent = orderNo || "شماره سفارش در پیام ربات اعلام می‌شود";
  $("success-note").textContent = note;
  showScreen("success");
}
function bindSuccess() {
  $("btn-success-orders").addEventListener("click", function () { haptic("light"); showScreen("orders"); });
  $("btn-success-home").addEventListener("click", function () { haptic("light"); showScreen("home"); });
}

/* ---------------- init ---------------- */
function init() {
  if (tg) {
    try { tg.ready(); tg.expand(); } catch (e) {}
    if (tg.initDataUnsafe && tg.initDataUnsafe.user && tg.initDataUnsafe.user.first_name) {
      /* name is available for later personalisation; demo data stays as-is */
      console.log("[GodStar] user:", tg.initDataUnsafe.user.first_name);
    }
  }
  bindNav(); bindSwipe(); bindHome(); bindChips(); bindCopyButtons();
  bindAds(); bindMember(); bindBot(); bindPayment(); bindWallet(); bindSupport(); bindSuccess();
  if (!DEMO) {
    /* داخل تلگرام داده‌ی نمونه نشان نمی‌دهیم؛ فقط حالت خالی واقعی. */
    orders = [];
    chatMessages = [];
    friends = [];
    walletBalance = 0;
    walletKnown = false;
    /* کد معرفی از همان استارت ساخته شده است (از روی آیدی تلگرام)؛
       زدن روی کد آن را کپی می‌کند و دکمه، لینک معرفی را به اشتراک
       می‌گذارد — دیگر «دریافت کد» لازم نیست. */
    var me = tg && tg.initDataUnsafe && tg.initDataUnsafe.user;
    if (me && me.id) {
      REFERRAL_CODE = deriveReferralCode(me.id);
      var codeEl = $("referral-code");
      codeEl.textContent = REFERRAL_CODE;
      codeEl.style.cursor = "pointer";
      codeEl.addEventListener("click", function () {
        haptic("light");
        copyText(REFERRAL_CODE, codeEl);
      });
    } else {
      $("referral-code").textContent = "—";
    }
    $("btn-share-referral").textContent = "اشتراک‌گذاری";
    var loyaltyCard = document.querySelector(".loyalty-card");
    if (loyaltyCard) loyaltyCard.style.display = "none";
  }
  renderFriends(friends);
  renderOrders(orders);
  renderChat();
  refreshWalletLabels();
  renderAds(); renderMember(); renderBot(); renderWallet();
  if (!DEMO) loadMyData(); /* موجودی و سفارش‌های واقعی از همان استارت */
  showScreen("home", false);
  navStack = ["home"];
  updateBackButton();
}
document.addEventListener("DOMContentLoaded", init);
