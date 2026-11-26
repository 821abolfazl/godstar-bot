// godstar-api — Edge Function «گاد استار» (نسخه‌ی ۱٫۰)
//
// مینی‌اپ روی GitHub Pages صفحه‌ی ثابتی است و نمی‌تواند مستقیم به ربات
// پیام بدهد؛ Telegram.WebApp.sendData هم فقط وقتی کار می‌کند که مینی‌اپ
// از دکمه‌ی کیبورد باز شده باشد (از دکمه‌ی آبی منو بی‌صدا هیچ کاری
// نمی‌کند). پس مینی‌اپ سفارش/شارژ/پیام را اینجا POST می‌کند و این تابع
// آن را در جدول webapp_inbox می‌گذارد؛ ربات هر ۱۰ ثانیه صندوق را
// می‌خواند، initData را با توکن ربات اعتبارسنجی می‌کند، پی‌لود را
// پردازش می‌کند و ردیف را حذف می‌کند.
//
// دو اکشن (POST با JSON ‏{action, initData, payload?}‏):
//   submit  — پی‌لود را در webapp_inbox می‌گذارد؛ {ok:true}
//   my-data — سفارش‌های واقعی همان کاربر + موجودی کیف پولش؛
//             {ok:true, wallet_balance, orders:[...]}
// هر دو اکشن اول initData را دقیقاً مثل مستندات تلگرام اعتبارسنجی
// می‌کنند (data_check_string + HMAC-SHA256 با SHA256 توکن) و auth_date
// نباید بیش از ۲۴ ساعت قدیمی باشد؛ در غیر این صورت 401.
//
// متغیرهای محیطی (در داشبورد سوپابیس → Edge Functions → Secrets):
//   SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, TELEGRAM_BOT_TOKEN
// هیچ‌یک از این‌ها اینجا hardcode نشده‌اند.

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...corsHeaders, "Content-Type": "application/json" },
  });
}

async function validateInitData(
  initData: string,
  token: string,
): Promise<number | null> {
  if (!initData || !token) return null;
  const params = new URLSearchParams(initData);
  const receivedHash = params.get("hash");
  if (!receivedHash) return null;
  params.delete("hash");
  params.sort();
  const parts: string[] = [];
  params.forEach((value, key) => parts.push(`${key}=${value}`));
  const dataCheckString = parts.join("\n");
  const authDate = Number(params.get("auth_date") || "0");
  if (!authDate || Date.now() / 1000 - authDate > 24 * 3600) return null;
  // کلید مخفی = SHA256 توکن ربات؛ امضا = HMAC-SHA256 آن روی رشته‌ی بررسی
  const secretKey = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(token),
  );
  const key = await crypto.subtle.importKey(
    "raw",
    secretKey,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(dataCheckString),
  );
  const hex = [...new Uint8Array(sig)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  // مقایسه‌ی ثابت‌زمان (تا طول نشت نکند)
  if (hex.length !== receivedHash.length) return null;
  let diff = 0;
  for (let i = 0; i < hex.length; i++) {
    diff |= hex.charCodeAt(i) ^ receivedHash.charCodeAt(i);
  }
  if (diff !== 0) return null;
  try {
    const user = JSON.parse(params.get("user") || "{}");
    if (!user || typeof user.id !== "number") return null;
    return user.id;
  } catch {
    return null;
  }
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders });
  }
  if (req.method !== "POST") {
    return jsonResponse({ ok: false, error: "method_not_allowed" }, 405);
  }
  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return jsonResponse({ ok: false, error: "bad_json" }, 400);
  }
  const supabaseUrl = Deno.env.get("SUPABASE_URL") || "";
  const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
  const botToken = Deno.env.get("TELEGRAM_BOT_TOKEN") || "";
  if (!supabaseUrl || !serviceKey || !botToken) {
    return jsonResponse({ ok: false, error: "server_misconfigured" }, 500);
  }
  const userId = await validateInitData(
    String(body.initData || ""),
    botToken,
  );
  if (userId === null) {
    return jsonResponse({ ok: false, error: "unauthorized" }, 401);
  }
  const supabase = createClient(supabaseUrl, serviceKey, {
    auth: { persistSession: false },
  });

  if (body.action === "submit") {
    if (!body.payload || typeof body.payload !== "object") {
      return jsonResponse({ ok: false, error: "bad_payload" }, 400);
    }
    const { error } = await supabase.from("webapp_inbox").insert({
      id: crypto.randomUUID(),
      user_id: userId,
      init_data: String(body.initData || ""),
      payload: body.payload,
    });
    if (error) {
      return jsonResponse(
        { ok: false, error: "inbox_insert_failed", detail: error.message },
        500,
      );
    }
    return jsonResponse({ ok: true });
  }

  if (body.action === "my-data") {
    const { data: orders, error: ordersError } = await supabase
      .from("orders")
      .select("order_no,type,service,amount,status,created_at,details")
      .eq("user_id", userId)
      .order("created_at", { ascending: false })
      .limit(50);
    if (ordersError) {
      return jsonResponse({ ok: false, error: "orders_failed" }, 500);
    }
    const { data: userRow } = await supabase
      .from("users")
      .select("wallet_balance")
      .eq("chat_id", userId)
      .maybeSingle();
    return jsonResponse({
      ok: true,
      wallet_balance: userRow ? Number(userRow.wallet_balance) : 0,
      orders: orders || [],
    });
  }

  return jsonResponse({ ok: false, error: "unknown_action" }, 400);
});
