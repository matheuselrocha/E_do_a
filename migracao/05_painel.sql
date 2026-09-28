-- ============================================================================
-- Enxoval do Aprovado — Fase 1: painel das lojas parceiras (login + escrita de preço)
-- Rode no SQL Editor DEPOIS do 04_fase0.sql. Reexecutável.
--
-- Modelo de segurança:
--  * Contas são criadas SÓ pelo admin (Supabase → Authentication → Add user) e
--    ligadas a uma loja em loja_usuarios. Cadastro público deve ficar DESLIGADO.
--  * Uma conta só escreve preços da PRÓPRIA loja, e só se a loja for parceira.
--  * Só pode dar preço a item de concurso que a loja atende (loja_concurso).
--  * Só a coluna `preco` é gravável (não dá pra mover um preço de item/loja).
--  * A loja não cria itens, não mexe em lojas/concursos e não vê leads.
-- ============================================================================

-- 1) conta -> loja (1 conta = 1 loja; uma loja pode ter várias contas)
create table if not exists loja_usuarios (
  user_id    uuid primary key references auth.users(id) on delete cascade,
  loja_id    uuid not null references lojas(id) on delete cascade,
  created_at timestamptz not null default now()
);
create index if not exists idx_loja_usuarios_loja on loja_usuarios (loja_id);

alter table loja_usuarios enable row level security;
revoke all on loja_usuarios from anon, authenticated;
grant select on loja_usuarios to authenticated;
drop policy if exists le_propria on loja_usuarios;
create policy le_propria on loja_usuarios for select to authenticated
  using (user_id = auth.uid());

-- importação/admin (service_role) enxerga os vínculos
grant all on loja_usuarios to service_role;

-- 2) loja da conta logada (null se não houver vínculo ou a loja não for parceira)
create or replace function minha_loja() returns uuid
language sql stable security definer set search_path = public as $$
  select lu.loja_id
    from loja_usuarios lu
    join lojas l on l.id = lu.loja_id
   where lu.user_id = auth.uid() and l.parceira
$$;
revoke execute on function minha_loja() from public, anon;
grant execute on function minha_loja() to authenticated;

-- 3) usuário logado continua lendo o catálogo (as policies de 03_rls.sql são só p/ anon)
grant select on concursos, lojas, loja_concurso, itens_enxoval, produtos_online, precos to authenticated;
drop policy if exists leitura_logada on concursos;
create policy leitura_logada on concursos       for select to authenticated using (true);
drop policy if exists leitura_logada on lojas;
create policy leitura_logada on lojas           for select to authenticated using (true);
drop policy if exists leitura_logada on loja_concurso;
create policy leitura_logada on loja_concurso   for select to authenticated using (true);
drop policy if exists leitura_logada on itens_enxoval;
create policy leitura_logada on itens_enxoval   for select to authenticated using (true);
drop policy if exists leitura_logada on produtos_online;
create policy leitura_logada on produtos_online for select to authenticated using (true);
drop policy if exists leitura_logada on precos;
create policy leitura_logada on precos          for select to authenticated using (true);

-- 4) escrita de preço pela loja
--    privilégio por COLUNA: insere item/loja/preço; atualiza só o preço
revoke insert, update, delete on precos from authenticated;
grant insert (item_id, loja_id, preco) on precos to authenticated;
grant update (preco)                   on precos to authenticated;
grant delete                           on precos to authenticated;

drop policy if exists loja_insere on precos;
create policy loja_insere on precos for insert to authenticated
  with check (
    loja_id = minha_loja()
    and preco > 0 and preco < 100000
    and exists (select 1
                  from itens_enxoval i
                  join loja_concurso lc on lc.concurso_id = i.concurso_id
                 where i.id = precos.item_id and lc.loja_id = minha_loja())
  );

drop policy if exists loja_altera on precos;
create policy loja_altera on precos for update to authenticated
  using (loja_id = minha_loja())
  with check (loja_id = minha_loja() and preco > 0 and preco < 100000);

drop policy if exists loja_remove on precos;
create policy loja_remove on precos for delete to authenticated
  using (loja_id = minha_loja());

-- (precos_historico continua sem policy: a loja não lê o histórico. O trigger
--  precos_log grava origem = 'painel' e alterado_por = auth.uid() automaticamente.)

-- ----------------------------------------------------------------------------
-- COMO LIGAR UMA CONTA A UMA LOJA (depois de criar o usuário em Authentication):
--   insert into loja_usuarios (user_id, loja_id)
--   select u.id, l.id from auth.users u, lojas l
--    where u.email = 'email@da.loja' and l.nome = 'Nome exato da loja'
--   on conflict (user_id) do update set loja_id = excluded.loja_id;
-- ----------------------------------------------------------------------------
