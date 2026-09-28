-- ============================================================================
-- Enxoval do Aprovado — Fase 0 (fundação do painel das lojas)
-- Rode no SQL Editor DEPOIS de 01/02/03. Reexecutável.
--
-- 1) Chaves naturais únicas → a importação passa a fazer UPSERT (IDs estáveis)
--    em vez de apagar e recriar tudo a cada hora.
-- 2) lojas.parceira → loja contratante: a importação NÃO mexe nos preços dela
--    (a fonte passa a ser o painel da loja).
-- 3) precos.updated_at só muda quando o preço/link muda de verdade.
-- 4) precos_historico → toda mudança de preço fica registrada (auditoria e
--    base do futuro alerta de preço).
-- ============================================================================

-- 1) chaves naturais (usadas no on_conflict da importação)
do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'itens_enxoval_concurso_nome_key') then
    alter table itens_enxoval add constraint itens_enxoval_concurso_nome_key unique (concurso_id, nome_padronizado);
  end if;
  if not exists (select 1 from pg_constraint where conname = 'produtos_online_nome_key') then
    alter table produtos_online add constraint produtos_online_nome_key unique (nome);
  end if;
end $$;
-- (concursos.nome, lojas.nome, loja_concurso(loja_id, concurso_id) e precos(item_id, loja_id)
--  já são únicos desde o 01_schema.sql)

-- 2) loja parceira (vem da coluna "Parceira" da aba Contatos)
alter table lojas add column if not exists parceira boolean not null default false;

-- 3) updated_at do preço reflete a última mudança real
create or replace function precos_touch() returns trigger language plpgsql as $$
begin
  if new.preco is distinct from old.preco or new.link_produto is distinct from old.link_produto then
    new.updated_at := now();
  else
    new.updated_at := old.updated_at;
  end if;
  return new;
end $$;
drop trigger if exists trg_precos_touch on precos;
create trigger trg_precos_touch before update on precos
  for each row execute function precos_touch();

-- 4) histórico de preços (sem FK de propósito: sobrevive à remoção do item/loja)
create table if not exists precos_historico (
  id bigint generated always as identity primary key,
  item_id uuid not null,
  loja_id uuid not null,
  preco_antigo numeric,                 -- null = preço criado
  preco_novo numeric,                   -- null = preço removido
  origem text not null,                 -- 'importacao' (planilha) | 'painel' (loja logada)
  alterado_por uuid,                    -- auth.uid() quando vier do painel
  alterado_em timestamptz not null default now()
);
create index if not exists idx_hist_item_loja on precos_historico (item_id, loja_id, alterado_em desc);

create or replace function precos_log() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  quem uuid := auth.uid();
  org  text := case when auth.uid() is null then 'importacao' else 'painel' end;
begin
  if tg_op = 'INSERT' then
    insert into precos_historico (item_id, loja_id, preco_antigo, preco_novo, origem, alterado_por)
    values (new.item_id, new.loja_id, null, new.preco, org, quem);
  elsif tg_op = 'UPDATE' then
    if new.preco is distinct from old.preco then
      insert into precos_historico (item_id, loja_id, preco_antigo, preco_novo, origem, alterado_por)
      values (new.item_id, new.loja_id, old.preco, new.preco, org, quem);
    end if;
  elsif tg_op = 'DELETE' then
    insert into precos_historico (item_id, loja_id, preco_antigo, preco_novo, origem, alterado_por)
    values (old.item_id, old.loja_id, old.preco, null, org, quem);
  end if;
  return null;
end $$;
drop trigger if exists trg_precos_log on precos;
create trigger trg_precos_log after insert or update or delete on precos
  for each row execute function precos_log();

-- histórico é interno: RLS ligado e SEM policy (anon/authenticated não leem nada)
alter table precos_historico enable row level security;
revoke all on precos_historico from anon, authenticated;

-- privilégios para o script de migração (service_role)
grant all privileges on all tables    in schema public to service_role;
grant all privileges on all sequences in schema public to service_role;
