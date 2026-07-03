
-- Fund Estimator V4.0 Supabase schema
-- 在 Supabase SQL Editor 中执行本脚本。

create extension if not exists "pgcrypto";

create table if not exists stocks (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  code text not null,
  created_at timestamptz default now()
);

create table if not exists industry_etfs (
  id uuid primary key default gen_random_uuid(),
  industry text not null,
  etf_code text not null,
  created_at timestamptz default now()
);

create table if not exists funds (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  amount numeric default 0,
  cash_weight numeric default 0,
  bond_weight numeric default 0,
  bond_return numeric default 0,
  other_weight numeric default 0,
  other_return numeric default 0,
  created_at timestamptz default now()
);

create table if not exists fund_positions (
  id uuid primary key default gen_random_uuid(),
  fund_id uuid references funds(id) on delete cascade,
  stock_name text not null,
  stock_code text not null,
  weight numeric default 0,
  created_at timestamptz default now()
);

create table if not exists fund_industry (
  id uuid primary key default gen_random_uuid(),
  fund_id uuid references funds(id) on delete cascade unique,
  tech numeric default 0,
  manufacturing numeric default 0,
  consumption numeric default 0,
  finance numeric default 0,
  medical numeric default 0,
  created_at timestamptz default now()
);

-- 简化个人使用：允许 anon key 读写这些表。
-- 如果以后多人共用，应改成 Supabase Auth + RLS 用户隔离。
alter table stocks enable row level security;
alter table industry_etfs enable row level security;
alter table funds enable row level security;
alter table fund_positions enable row level security;
alter table fund_industry enable row level security;

drop policy if exists "allow all stocks" on stocks;
create policy "allow all stocks" on stocks for all using (true) with check (true);

drop policy if exists "allow all industry_etfs" on industry_etfs;
create policy "allow all industry_etfs" on industry_etfs for all using (true) with check (true);

drop policy if exists "allow all funds" on funds;
create policy "allow all funds" on funds for all using (true) with check (true);

drop policy if exists "allow all fund_positions" on fund_positions;
create policy "allow all fund_positions" on fund_positions for all using (true) with check (true);

drop policy if exists "allow all fund_industry" on fund_industry;
create policy "allow all fund_industry" on fund_industry for all using (true) with check (true);
