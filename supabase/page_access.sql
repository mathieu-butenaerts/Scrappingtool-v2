-- Pagina-toegang voor Marktmonitor (scrappingtool), Leadbeheer en Maatman
-- ---------------------------------------------------------------------------
-- Eenmalig uitvoeren in de Supabase SQL-editor (Project > SQL Editor > New query).
-- Alle drie de sites delen hetzelfde Supabase-project en dezelfde `profiles`,
-- dus dit is de enige plek waar toegang wordt beheerd (via web/admin.html).
--
-- Opbouw
--   app_pages            register van alle pagina's (één rij per pagina)
--   default_page_access  welke pagina's nieuwe gebruikers standaard krijgen
--   user_page_access     welke pagina's een bestaande gebruiker heeft
--   has_page_access(key) true voor admins en voor actieve gebruikers met die pagina
--
-- Handhaving: de sites sturen gebruikers zonder toegang weg (auth.js >
-- requirePage) EN de RLS-policies onderaan blokkeren de data zelf, zodat de
-- API niet te omzeilen is met een eigen request.
--
-- Veilig om opnieuw te draaien: het seeden van pagina's, standaardrechten en
-- het backfillen van bestaande gebruikers gebeurt alleen als app_pages nog leeg
-- is. Een latere run overschrijft dus nooit wat je in het beheerpaneel instelt.
--
-- LET OP: leadbeheer/supabase/schema.sql zet de RLS-policies van de leadbeheer-
-- tabellen terug naar "elke ingelogde gebruiker" als je dat script opnieuw
-- draait. Draai dit bestand daarna opnieuw (of pas dat schema aan).
-- ---------------------------------------------------------------------------

-- 1. Tabellen ---------------------------------------------------------------

create table if not exists public.app_pages (
  key          text primary key,            -- bv. 'leadbeheer.dashboard'
  system       text not null,               -- 'scrappingtool' | 'leadbeheer' | 'maatman'
  system_label text not null,               -- kolomkop in het beheerpaneel
  label        text not null,               -- naam van de pagina
  url          text,                        -- waar de pagina te vinden is (voor links)
  sort_order   integer not null default 0
);

create table if not exists public.default_page_access (
  page_key text primary key references public.app_pages(key) on delete cascade
);

create table if not exists public.user_page_access (
  user_id  uuid not null references auth.users(id) on delete cascade,
  page_key text not null references public.app_pages(key) on delete cascade,
  primary key (user_id, page_key)
);

-- 2. Hulpfuncties -----------------------------------------------------------
-- security definer zodat ze `profiles` kunnen lezen zonder zelf in een
-- RLS-lus terecht te komen.

create or replace function public.pf_is_admin() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from public.profiles p
    where p.id = auth.uid()
      and p.role = 'admin'
      and coalesce(p.status, 'active') <> 'blocked'
  );
$$;

create or replace function public.has_page_access(p_key text) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from public.profiles p
    where p.id = auth.uid()
      and coalesce(p.status, 'active') <> 'blocked'
      and (
        p.role = 'admin'
        or exists (
          select 1 from public.user_page_access a
          where a.user_id = p.id and a.page_key = p_key
        )
      )
  );
$$;

grant execute on function public.pf_is_admin()            to authenticated;
grant execute on function public.has_page_access(text)    to authenticated;

-- 3. Nieuwe gebruikers krijgen automatisch de standaardpagina's -------------

create or replace function public.apply_default_page_access() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.user_page_access (user_id, page_key)
  select new.id, d.page_key from public.default_page_access d
  on conflict do nothing;
  return new;
end;
$$;

drop trigger if exists trg_apply_default_page_access on public.profiles;
create trigger trg_apply_default_page_access
  after insert on public.profiles
  for each row execute function public.apply_default_page_access();

-- 4. Seed (alleen de allereerste keer) --------------------------------------
-- Houdt het huidige gedrag in stand zodat niemand buitengesloten wordt:
-- bestaande gebruikers krijgen Marktmonitor + Leadbeheer, en dat zijn ook de
-- standaardrechten voor nieuwe gebruikers. Maatman staat klaar maar is uit.

do $$
begin
  if not exists (select 1 from public.app_pages) then
    insert into public.app_pages (key, system, system_label, label, url, sort_order) values
      ('scrappingtool.dashboard', 'scrappingtool', 'Scrappingtool', 'Dashboard',
         'https://kloos1123.github.io/Scrappingtool-v2/index.html', 10),
      ('leadbeheer.dashboard',    'leadbeheer',    'Leadbeheer',    'Dashboard',
         'https://mathieu-butenaerts.github.io/leadbeheer/', 20),
      ('maatman.dashboard',       'maatman',       'Maatman',       'Dashboard',
         null, 30);

    insert into public.default_page_access (page_key) values
      ('scrappingtool.dashboard'), ('leadbeheer.dashboard');

    insert into public.user_page_access (user_id, page_key)
    select p.id, d.page_key
    from public.profiles p cross join public.default_page_access d
    on conflict do nothing;
  end if;
end $$;

-- 5. RLS op de beheertabellen ----------------------------------------------

alter table public.app_pages           enable row level security;
alter table public.default_page_access enable row level security;
alter table public.user_page_access    enable row level security;

drop policy if exists "pages read"  on public.app_pages;
drop policy if exists "pages admin" on public.app_pages;
create policy "pages read"  on public.app_pages
  for select to authenticated using (true);
create policy "pages admin" on public.app_pages
  for all to authenticated using (public.pf_is_admin()) with check (public.pf_is_admin());

drop policy if exists "defaults read"  on public.default_page_access;
drop policy if exists "defaults admin" on public.default_page_access;
create policy "defaults read"  on public.default_page_access
  for select to authenticated using (true);
create policy "defaults admin" on public.default_page_access
  for all to authenticated using (public.pf_is_admin()) with check (public.pf_is_admin());

drop policy if exists "access read own" on public.user_page_access;
drop policy if exists "access admin"    on public.user_page_access;
create policy "access read own" on public.user_page_access
  for select to authenticated using (user_id = auth.uid());
create policy "access admin" on public.user_page_access
  for all to authenticated using (public.pf_is_admin()) with check (public.pf_is_admin());

-- 6. RLS op de data zelf ----------------------------------------------------
-- Bestaande policies op deze tabellen worden vervangen (namen verschillen per
-- tabel en de marktmonitor-tabellen staan niet in een script in de repo).
-- De scrapers schrijven met de service-role key en omzeilen RLS, dus die
-- blijven werken.

do $$
declare
  t   text;
  pol record;
begin
  -- Marktmonitor: lezen, plus status bijwerken op tenders
  foreach t in array array['tenders', 'beschrijvingen'] loop
    for pol in select policyname from pg_policies where schemaname = 'public' and tablename = t loop
      execute format('drop policy %I on public.%I', pol.policyname, t);
    end loop;
    execute format('alter table public.%I enable row level security', t);
    execute format(
      'create policy "page access read" on public.%I for select to authenticated using (public.has_page_access(%L))',
      t, 'scrappingtool.dashboard');
  end loop;
  execute format(
    'create policy "page access update" on public.tenders for update to authenticated using (public.has_page_access(%L)) with check (public.has_page_access(%L))',
    'scrappingtool.dashboard', 'scrappingtool.dashboard');

  -- Leadbeheer: lezen en schrijven
  foreach t in array array['companies', 'contacts', 'tags', 'company_tags', 'company_superscore_history'] loop
    for pol in select policyname from pg_policies where schemaname = 'public' and tablename = t loop
      execute format('drop policy %I on public.%I', pol.policyname, t);
    end loop;
    execute format('alter table public.%I enable row level security', t);
    execute format(
      'create policy "page access" on public.%I for all to authenticated using (public.has_page_access(%L)) with check (public.has_page_access(%L))',
      t, 'leadbeheer.dashboard', 'leadbeheer.dashboard');
  end loop;
end $$;

-- Een nieuwe pagina (bv. voor Maatman) toevoegen:
--   insert into public.app_pages (key, system, system_label, label, url, sort_order)
--   values ('maatman.planning', 'maatman', 'Maatman', 'Planning', 'https://…', 31);
-- Daarna verschijnt hij als kolom in het beheerpaneel. Bescherm de data van die
-- pagina met een RLS-policy op basis van public.has_page_access('maatman.planning').
