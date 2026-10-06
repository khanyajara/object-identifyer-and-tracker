-- Run in this project's Supabase SQL editor.
-- Keeps existing accounts, records and stored files.
-- Backend uses SUPABASE_SECRET_KEY or SUPABASE_SERVICE_ROLE_KEY.
begin;

create table if not exists public.roadwatch_accounts (
    username text primary key
        check (username ~ '^[a-z0-9][a-z0-9_.-]{2,31}$'),
    password_hash text not null check (password_hash like '$2%'),
    created_at timestamptz not null default now()
);

create table if not exists public.roadwatch_records (
    device_id text not null,
    record_id text not null,
    kind text not null,
    payload jsonb not null,
    updated_at timestamptz not null default now(),
    primary key (device_id, record_id)
);

create index if not exists roadwatch_records_kind_updated
    on public.roadwatch_records (kind, updated_at desc);

create or replace function public.roadwatch_touch_record()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists roadwatch_record_updated
    on public.roadwatch_records;
create trigger roadwatch_record_updated
    before update on public.roadwatch_records
    for each row execute function public.roadwatch_touch_record();

alter table public.roadwatch_accounts enable row level security;
alter table public.roadwatch_records enable row level security;

revoke all on public.roadwatch_accounts, public.roadwatch_records
    from public, anon, authenticated;
grant usage on schema public to service_role;
grant select, insert, update, delete
    on public.roadwatch_accounts, public.roadwatch_records
    to service_role;

-- Compressed MP4 recordings. Private playback uses signed URLs.
-- No change to an existing bucket's file-size limit.
insert into storage.buckets (id, name, public, allowed_mime_types)
values ('videos', 'videos', false, array['video/mp4'])
on conflict (id) do update
set public = false,
    allowed_mime_types = excluded.allowed_mime_types;

-- Thumbnails, snapshots and document exports.
insert into storage.buckets (id, name, public, allowed_mime_types)
values (
    'roadwatch-assets', 'roadwatch-assets', false,
    array['image/jpeg', 'image/png', 'image/webp', 'application/pdf', 'text/csv']
)
on conflict (id) do update
set public = false,
    allowed_mime_types = excluded.allowed_mime_types;

-- No new public/client policies. Uploads and metadata use backend credentials.
-- Video title, document_name, object path and playback URL live in payload.
notify pgrst, 'reload schema';
commit;
