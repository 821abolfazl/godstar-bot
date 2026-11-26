-- ============================================================
-- «گاد استار» (God Star) — اسکیمای سوپابیس
-- در SQL Editor سوپابیس (پروژه‌ی ربات) اجرا شود.
-- جدول‌های رابطه‌ای واقعی؛ برخلاف ربات دنبال‌کننده، اینجا
-- هیچ بلاب رمزنگاری‌شده‌ای نیست.
-- ============================================================

-- کاربران ربات
create table if not exists users (
  chat_id bigint primary key,
  username text not null default '',
  first_name text not null default '',
  wallet_balance numeric(12, 2) not null default 0,
  completed_orders integer not null default 0,
  referral_code text not null default '',
  referred_by text not null default '',
  referral_discount_used boolean not null default false,
  referral_credit boolean not null default false,
  created_at timestamptz not null default now()
);
create index if not exists users_referral_code_idx on users (referral_code);

-- سفارش‌ها (شماره‌ی سفارش GS-1001 و بالاتر)
create table if not exists orders (
  order_no text primary key,
  user_id bigint not null references users (chat_id),
  type text not null,               -- order | wallet_charge
  service text not null,            -- ads | members | bot | wallet_charge
  details jsonb not null default '{}'::jsonb,
  amount numeric(12, 2) not null,   -- مبلغ نهایی به تتر (بعد از تخفیف)
  discount_kind text not null default '',  -- loyalty | referral | code | ''
  discount_percent integer not null default 0,
  discount_amount numeric(12, 2) not null default 0,
  paid_by_wallet boolean not null default false,
  status text not null default 'pending',  -- pending | confirmed | rejected
  tx_hash text not null default '',
  receipt_file_id text not null default '',
  created_at timestamptz not null default now()
);
create index if not exists orders_user_idx on orders (user_id);
create index if not exists orders_status_idx on orders (status);

-- تراکنش‌های کیف پول (شارژ و پرداخت از کیف پول)
create table if not exists wallet_tx (
  id bigserial primary key,
  chat_id bigint not null references users (chat_id),
  amount numeric(12, 2) not null,  -- مثبت = شارژ، منفی = برداشت
  kind text not null,               -- credit | debit
  note text not null default '',
  created_at timestamptz not null default now()
);
create index if not exists wallet_tx_chat_idx on wallet_tx (chat_id);

-- کدهای تخفیف (مدیریت با ادمین)
create table if not exists discount_codes (
  code text primary key,
  percent integer not null check (percent > 0 and percent <= 100),
  max_uses integer not null default 0,  -- صفر یعنی نامحدود
  uses integer not null default 0,
  active boolean not null default true
);

-- معرفی‌ها: هر ردیف = یک بار استفاده‌ی یک دوست از یک کد
create table if not exists referrals (
  id bigserial primary key,
  code text not null,
  referrer_id bigint not null references users (chat_id),
  friend_id bigint not null references users (chat_id),
  used boolean not null default true,
  activated boolean not null default false,  -- بعد از اولین سفارش دوست
  created_at timestamptz not null default now(),
  unique (code, friend_id)  -- هر کد برای هر نفر فقط یک بار
);
create index if not exists referrals_referrer_idx on referrals (referrer_id);

-- نگاشت پیام فورواردشده‌ی پشتیبانی به مشتری (برای برگرداندن جواب ادمین)
create table if not exists support_threads (
  admin_message_id bigint primary key,
  customer_chat_id bigint not null references users (chat_id),
  created_at timestamptz not null default now()
);

-- پاسخ‌های خودکار (مدیریت با ادمین)
create table if not exists auto_replies (
  id bigserial primary key,
  question text not null,
  answer text not null
);

-- یادآوری‌های ثبت‌شده‌ی سفارش‌ها (هر سفارش + نوع، فقط یک بار):
-- kind = 'pending_admin' (سفارش بیش از ۲ ساعت در انتظار تایید)
--      | 'renewal' (یادآوری تمدید اشتراک، ۳ روز مانده به پایان)
create table if not exists order_reminders (
  order_no text not null,
  kind text not null,
  created_at timestamptz not null default now(),
  primary key (order_no, kind)
);

-- شروع اشتراک از لحظه‌ی تایید ادمین است، نه ثبت سفارش
alter table public.orders
  add column if not exists confirmed_at timestamptz;

-- پاسخ‌های خودکار پیش‌فرض
insert into auto_replies (question, answer) values
  ('سفارشم کی آماده می‌شه؟',
   'سفارش‌های ممبر و تبلیغات معمولاً تا ۲۴ ساعت بعد از تایید پرداخت انجام می‌شوند و نتیجه‌اش را با پیام بهت خبر می‌دهیم.'),
  ('چطور پرداخت کنم؟',
   'فقط تتر (USDT) روی شبکه‌ی BEP20 به آدرس کیف پول ما بفرست و رسید یا کد هش تراکنش را ارسال کن.')
on conflict do nothing;

-- کد تخفیف نمونه
insert into discount_codes (code, percent, max_uses, uses, active)
values ('GOD10', 10, 0, 0, true)
on conflict (code) do nothing;

-- دسترسی‌ها (۲۰۲۶-۱۱-۲۳: بدون این GRANTها نقش service_role روی جدول‌ها
-- هیچ دسترسی ندارد و PostgREST با 403 «permission denied for table»
-- جواب می‌دهد؛ کلیدها سالم بودند و خطا از نبود GRANT بود):
grant select, insert, update, delete on public.users to service_role;
grant select, insert, update, delete on public.orders to service_role;
grant select, insert, update, delete on public.wallet_tx to service_role;
grant select, insert, update, delete on public.discount_codes to service_role;
grant select, insert, update, delete on public.referrals to service_role;
grant select, insert, update, delete on public.support_threads to service_role;
grant select, insert, update, delete on public.auto_replies to service_role;
grant select, insert, update, delete on public.order_reminders to service_role;
grant usage, select on all sequences in schema public to service_role;

-- صندوق ورودی مینی‌اپ (نسخه‌ی ۱٫۰): مینی‌اپ سفارش/شارژ/پیام را از راه
-- Edge Function «godstar-api» اینجا می‌گذارد تا از هر جای باز شدن
-- (دکمه‌ی آبی منو یا کیبورد) ثبت شود؛ ربات هر ۱۰ ثانیه می‌خواند،
-- initData را با توکن ربات اعتبارسنجی می‌کند، پی‌لود را پردازش
-- می‌کند و ردیف را حذف می‌کند. جدول گذراست و در حافظه‌ی ربات
-- (load/save) نمی‌آید.
create table if not exists webapp_inbox (
  id uuid primary key,
  user_id bigint not null,
  init_data text not null,
  payload jsonb not null,
  created_at timestamptz not null default now()
);
create index if not exists webapp_inbox_created_idx on webapp_inbox (created_at);

grant select, insert, update, delete on public.webapp_inbox to service_role;
