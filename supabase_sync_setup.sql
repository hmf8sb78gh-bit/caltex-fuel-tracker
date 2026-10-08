alter table public.fuel_fillups add column if not exists litres numeric;
alter table public.fuel_fillups add column if not exists pump_price numeric;
alter table public.fuel_fillups add column if not exists card_discount numeric;
alter table public.fuel_fillups add column if not exists voucher_value numeric;
alter table public.fuel_fillups add column if not exists voucher_sheets integer;
alter table public.fuel_fillups add column if not exists full_tank boolean;
create table public.consumable_history (
 id uuid primary key default gen_random_uuid(), user_id uuid not null references auth.users(id) on delete cascade,
 item_id text not null, replacement_date date not null, mileage numeric, cost numeric not null default 0,
 legacy boolean not null default false, created_at timestamptz not null default now()
);
create index consumable_history_user_date on public.consumable_history(user_id,replacement_date desc);
alter table public.consumable_history enable row level security;
create policy consum_history_own on public.consumable_history for all to authenticated
 using ((select auth.uid())=user_id) with check ((select auth.uid())=user_id);
grant select,insert,update,delete on public.consumable_history to authenticated;
create table public.tracker_sync_operations (
 user_id uuid not null references auth.users(id) on delete cascade, operation_id uuid not null,
 kind text not null, created_at timestamptz not null default now(), primary key(user_id,operation_id)
);
alter table public.tracker_sync_operations enable row level security;
create policy tracker_operations_own on public.tracker_sync_operations for all to authenticated
 using ((select auth.uid())=user_id) with check ((select auth.uid())=user_id);
grant select,insert,delete on public.tracker_sync_operations to authenticated;
-- Preserve only the latest historical costs that still exist; older replacements cannot be recovered.
insert into public.consumable_history(user_id,item_id,replacement_date,mileage,cost,legacy)
select r.user_id,c.key,
 case when coalesce(r."costYm"->>c.key,'') ~ '^\d{4}-\d{2}$' then (r."costYm"->>c.key||'-01')::date else r.updated_at::date end,
 case when coalesce(r.km->>c.key,'') ~ '^\d+(\.\d+)?$' then (r.km->>c.key)::numeric else null end,
 c.value::numeric,true
from public.consumable_records r cross join lateral jsonb_each_text(coalesce(r.cost,'{}')) c
where c.value ~ '^\d+(\.\d+)?$' and c.value::numeric>0;
create or replace function public.sync_tracker_operation(operation_id uuid,kind text,payload jsonb)
returns boolean language plpgsql security invoker set search_path='' as $$
declare uid uuid := auth.uid(); inserted integer; h jsonb; r jsonb; existing_id uuid;
begin
 if uid is null then raise exception 'Login required'; end if;
 insert into public.tracker_sync_operations(user_id,operation_id,kind)
 values(uid,operation_id,kind) on conflict do nothing;
 get diagnostics inserted = row_count;
 if inserted=0 then return true; end if;
 if kind in ('profile','fillup') then
  r:=payload->'profile';
  if r is null then raise exception 'Missing profile'; end if;
  insert into public.fuel_records(user_id,last_odometer,current_mileage,last_consumption,fuel_type,full_interval,last_interval,updated_at)
  values(uid,(r->>'last_odometer')::numeric,(r->>'current_mileage')::numeric,(r->>'last_consumption')::numeric,
  r->>'fuel_type',r->'full_interval',nullif(r->'last_interval','null'::jsonb),now())
  on conflict(user_id) do update set last_odometer=excluded.last_odometer,current_mileage=excluded.current_mileage,
  last_consumption=excluded.last_consumption,fuel_type=excluded.fuel_type,full_interval=excluded.full_interval,last_interval=excluded.last_interval,updated_at=now();
  if kind='fillup' then
   r:=payload->'fillup';
   if not ((r->>'litres')::numeric>0 and (r->>'pump_price')::numeric>0) then raise exception 'Invalid fillup'; end if;
   insert into public.fuel_fillups(id,user_id,fill_date,paid_amount,trip_km,odometer,fuel_type,litres,pump_price,card_discount,voucher_value,voucher_sheets,full_tank)
   values(operation_id,uid,(r->>'fill_date')::date,(r->>'paid_amount')::numeric,(r->>'trip_km')::numeric,(r->>'odometer')::numeric,r->>'fuel_type',
   (r->>'litres')::numeric,(r->>'pump_price')::numeric,(r->>'card_discount')::numeric,(r->>'voucher_value')::numeric,(r->>'voucher_sheets')::integer,(r->>'full_tank')::boolean);
  end if;
 elsif kind='consumables' then
  r:=payload->'snapshot';
  insert into public.consumable_records(user_id,km,cost,"costYm",updated_at)
  values(uid,r->'km',r->'cost',r->'costYm',now())
  on conflict(user_id) do update set km=excluded.km,cost=excluded.cost,"costYm"=excluded."costYm",updated_at=now();
  for h in select value from jsonb_array_elements(payload->'history') loop
   insert into public.consumable_history(id,user_id,item_id,replacement_date,mileage,cost)
   values((h->>'id')::uuid,uid,h->>'item_id',(h->>'replacement_date')::date,(h->>'mileage')::numeric,(h->>'cost')::numeric);
  end loop;
 elsif kind='repair' then
  insert into public.repair_records(id,user_id,repair_date,item,mileage,cost)
  values(operation_id,uid,(payload->>'repair_date')::date,payload->>'item',(payload->>'mileage')::numeric,(payload->>'cost')::numeric);
 elsif kind='parking' then
  insert into public.vehicle_dues(id,user_id,kind,amount,due_date,note)
  values(operation_id,uid,'parking',(payload->>'amount')::numeric,(payload->>'due_date')::date,'');
 elsif kind='due' then
  if payload->>'kind' not in ('license','insurance','inspection','hmb_north','hmb_insurance') then raise exception 'Invalid due'; end if;
  select id into existing_id from public.vehicle_dues where user_id=uid and vehicle_dues.kind=payload->>'kind' order by due_date desc limit 1;
  if existing_id is null then
   insert into public.vehicle_dues(id,user_id,kind,amount,due_date,note)
   values(operation_id,uid,payload->>'kind',(payload->>'amount')::numeric,(payload->>'due_date')::date,payload->>'note');
  else
   update public.vehicle_dues set amount=(payload->>'amount')::numeric,due_date=(payload->>'due_date')::date,note=payload->>'note' where id=existing_id and user_id=uid;
  end if;
 else raise exception 'Unknown operation'; end if;
 return true;
end $$;
revoke all on function public.sync_tracker_operation(uuid,text,jsonb) from public,anon;
grant execute on function public.sync_tracker_operation(uuid,text,jsonb) to authenticated;
