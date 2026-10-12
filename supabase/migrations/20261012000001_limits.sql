-- Limits the database enforces whatever writes the row (the API, or a future direct client).
-- The API checks the same limits first so users get friendly messages; these are the backstop.
-- Also: sign-in only through Google, task bookkeeping for stuck tasks, and no user access to
-- pipeline_runs (its error details can contain upstream URLs).

-- ── Helpers ──────────────────────────────────────────────────────────────────

create function public.text_array_ok(arr text[], max_items int, max_len int)
returns boolean language sql immutable as $$
  select coalesce(cardinality(arr), 0) <= max_items
     and coalesce((select max(char_length(x)) from unnest(arr) as x), 0) <= max_len
$$;

-- Caps the number of rows a user can have in a table. The advisory lock makes two
-- concurrent inserts wait for each other, so the count can't be raced past the limit.
create function public.enforce_row_limit()
returns trigger language plpgsql security definer set search_path = '' as $$
declare
  max_rows int := tg_argv[0]::int;
  n int;
begin
  perform pg_advisory_xact_lock(hashtextextended(tg_table_name || ':' || new.user_id::text, 0));
  execute format('select count(*) from public.%I where user_id = $1', tg_table_name) into n using new.user_id;
  if n >= max_rows then
    raise exception 'limit of % rows in % reached', max_rows, tg_table_name using errcode = 'check_violation';
  end if;
  return new;
end;
$$;
revoke execute on function public.enforce_row_limit() from public, anon, authenticated;

-- ── Per-user tables ──────────────────────────────────────────────────────────

alter table public.profiles
  add constraint profiles_display_name_len check (char_length(display_name) <= 120);

alter table public.subscriptions
  add constraint subscriptions_focus_len check (char_length(focus) <= 120);
create trigger subscriptions_row_limit before insert on public.subscriptions
  for each row execute function public.enforce_row_limit('25');

alter table public.custom_industry_requests
  add constraint custom_industry_requests_label_len check (char_length(label) between 2 and 120);
create trigger custom_industry_requests_row_limit before insert on public.custom_industry_requests
  for each row execute function public.enforce_row_limit('50');

create trigger watchlist_row_limit before insert on public.watchlist
  for each row execute function public.enforce_row_limit('500');

alter table public.resumes
  add constraint resumes_text_len check (char_length(text) <= 30000),
  add constraint resumes_filename_len check (char_length(filename) <= 255),
  add constraint resumes_role_types_ok check (public.text_array_ok(role_types, 20, 100)),
  add constraint resumes_storage_path_own check (storage_path is null or storage_path like user_id::text || '/%');
create trigger resumes_row_limit before insert on public.resumes
  for each row execute function public.enforce_row_limit('5');

alter table public.job_preferences
  add constraint job_preferences_role_types_ok check (public.text_array_ok(role_types, 20, 100)),
  add constraint job_preferences_custom_len check (char_length(custom_role_types) <= 300),
  add constraint job_preferences_locations_ok check (public.text_array_ok(locations, 20, 100)),
  add constraint job_preferences_seniority_ok check (public.text_array_ok(seniority, 10, 50)),
  add constraint job_preferences_industries_ok check (public.text_array_ok(industry_ids, 25, 64));

-- ── Tasks: link to the usage reservation, and what the task is for ───────────

alter table public.tasks
  add column usage_id bigint,
  add column ref text;  -- e.g. 'tailor:<job_id>:<resume_id>', to find a duplicate in-progress task
create index tasks_active_ref on public.tasks (user_id, ref) where status in ('queued', 'running');

-- ── pipeline_runs: worker bookkeeping only ───────────────────────────────────

drop policy "signed-in users can read" on public.pipeline_runs;
revoke select on public.pipeline_runs from authenticated;

-- ── Sign-in: Google accounts only ────────────────────────────────────────────

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  domain text := lower(split_part(coalesce(new.email, ''), '@', 2));
  community uuid;
begin
  if coalesce(new.raw_app_meta_data ->> 'provider', '') <> 'google' then
    raise exception 'Sign-in is limited to Google accounts' using errcode = 'P0001';
  end if;
  select c.id into community from public.communities c where c.email_domain = domain;
  if community is null then
    raise exception 'Sign-in is limited to approved school accounts (got domain "%")', domain
      using errcode = 'P0001';
  end if;
  insert into public.profiles (id, community_id, email, display_name)
  values (new.id, community, lower(new.email),
          left(coalesce(new.raw_user_meta_data ->> 'full_name', new.raw_user_meta_data ->> 'name'), 120));
  return new;
end;
$$;
revoke execute on function public.handle_new_user() from public, anon, authenticated;
