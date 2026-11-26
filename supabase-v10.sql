-- ============================================================
-- «گاد استار» (God Star) — نسخه‌ی ۱٫۰: فقط جدول صندوق ورودی مینی‌اپ
-- در SQL Editor سوپابیس (پروژه‌ی ربات) اجرا شود.
-- این فایل هیچ چیزی را دوباره seed نمی‌کند (نه GOD10، نه پاسخ‌های
-- خودکار)؛ فقط جدول webapp_inbox را می‌سازد.
-- ============================================================

-- صندوق ورودی مینی‌اپ: مینی‌اپ سفارش/شارژ/پیام را از راه Edge Function
-- «godstar-api» اینجا می‌گذارد تا از هر جای باز شدن (دکمه‌ی آبی منو
-- یا کیبورد) ثبت شود؛ ربات هر ۱۰ ثانیه می‌خواند، initData را با توکن
-- ربات اعتبارسنجی می‌کند، پی‌لود را پردازش می‌کند و ردیف را حذف می‌کند.
create table if not exists webapp_inbox (
  id uuid primary key,
  user_id bigint not null,
  init_data text not null,
  payload jsonb not null,
  created_at timestamptz not null default now()
);
create index if not exists webapp_inbox_created_idx on webapp_inbox (created_at);

grant select, insert, update, delete on public.webapp_inbox to service_role;
