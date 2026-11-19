-- ============================================================================
-- Enxoval do Aprovado — Etapa 1 do backend: tudo que ainda só existia na
-- planilha ou no código do site passa a ter lugar no Supabase.
-- Rode no SQL Editor DEPOIS do 08_equipe.sql. Reexecutável. O site não muda.
--
--  * concursos: botão "Itens iniciais" (aba Concursos) + textos que hoje estão
--    em CFG.concursos no index.html (rótulo, ano, selo, mensagem, descrição, ordem).
--  * lojas: estado e cidade (aba Contatos).
--  * itens_enxoval: feminino (hoje CFG.femininos).
--  * cupons (aba Cupons) + vínculo com produtos do catálogo e itens do enxoval.
--  * alteracoes: histórico de toda criação/mudança/remoção nessas tabelas
--    (quem, quando, antes/depois). Preços continuam em precos_historico (04).
-- ============================================================================

-- ---------------------------------------------------------------- concursos
alter table concursos add column if not exists itens_iniciais boolean not null default false;
alter table concursos add column if not exists rotulo       text;   -- texto grande do botão ("CBMDF")
alter table concursos add column if not exists ano          text;   -- texto pequeno do botão ("2025")
alter table concursos add column if not exists selo         text;   -- selo no topo do site
alter table concursos add column if not exists msg_whatsapp text;   -- "Sou do ..." na mensagem às lojas
alter table concursos add column if not exists descricao    text;   -- subtítulo do botão na tela inicial
alter table concursos add column if not exists ordem        int;    -- ordem na tela inicial

-- textos atuais do CFG (só preenche o que está vazio: não desfaz edição futura)
update concursos c set rotulo = coalesce(c.rotulo, v.rotulo), ano = coalesce(c.ano, v.ano), selo = coalesce(c.selo, v.selo),
       msg_whatsapp = coalesce(c.msg_whatsapp, v.msg), descricao = coalesce(c.descricao, v.descr), ordem = coalesce(c.ordem, v.ordem)
  from (values ('CBMDF - 2025',    'CBMDF',    '2025', 'CBMDF', 'CFP CBMDF 2026', 'Corpo de Bombeiros Militar do DF', 1),
               ('CFP PMDF - 2023', 'CFP PMDF', '2023', 'PMDF',  'CFP PMDF 2023',  'Polícia Militar do DF',            2))
       as v(nome, rotulo, ano, selo, msg, descr, ordem)
 where c.nome = v.nome;

-- ---------------------------------------------------------------- lojas / itens
alter table lojas add column if not exists estado text;
alter table lojas add column if not exists cidade text;

alter table itens_enxoval add column if not exists feminino boolean not null default false;
update itens_enxoval set feminino = true
 where nome_padronizado in ('Saia Passeio', 'Saia Túnica') and not feminino;   -- CFG.femininos

-- ---------------------------------------------------------------- cupons
create table if not exists cupons (
  id         uuid primary key default gen_random_uuid(),
  codigo     text not null unique,
  texto      text,                                           -- texto do balão
  loja_id    uuid references lojas(id) on delete set null,
  ativo      boolean not null default true,
  created_at timestamptz not null default now()
);
-- onde o balão aparece: produto do catálogo online e/ou item da calculadora
create table if not exists cupom_produtos (
  id                uuid primary key default gen_random_uuid(),
  cupom_id          uuid not null references cupons(id) on delete cascade,
  produto_online_id uuid not null references produtos_online(id) on delete cascade,
  unique (cupom_id, produto_online_id)
);
create table if not exists cupom_itens (
  id       uuid primary key default gen_random_uuid(),
  cupom_id uuid not null references cupons(id) on delete cascade,
  item_id  uuid not null references itens_enxoval(id) on delete cascade,
  unique (cupom_id, item_id)
);
create index if not exists idx_cupom_produtos_prod on cupom_produtos (produto_online_id);
create index if not exists idx_cupom_itens_item    on cupom_itens (item_id);

alter table cupons         enable row level security;
alter table cupom_produtos enable row level security;
alter table cupom_itens    enable row level security;
revoke all on cupons, cupom_produtos, cupom_itens from anon, authenticated;
grant select on cupons, cupom_produtos, cupom_itens to anon, authenticated;
grant all on cupons, cupom_produtos, cupom_itens to service_role;
-- leitura: visitante vê só cupom ativo; logado vê ativo ou, se for da equipe, todos.
-- Escrita: só service_role por enquanto (etapa 4).
drop policy if exists leitura on cupons;
drop policy if exists leitura_publica on cupons;
create policy leitura_publica on cupons for select to anon using (ativo);
drop policy if exists leitura_logada on cupons;
create policy leitura_logada on cupons for select to authenticated using (ativo or eh_admin());
drop policy if exists leitura on cupom_produtos;
drop policy if exists leitura_publica on cupom_produtos;
create policy leitura_publica on cupom_produtos for select to anon
  using (exists (select 1 from cupons c where c.id = cupom_id and c.ativo));
drop policy if exists leitura_logada on cupom_produtos;
create policy leitura_logada on cupom_produtos for select to authenticated
  using (exists (select 1 from cupons c where c.id = cupom_id and (c.ativo or eh_admin())));
drop policy if exists leitura on cupom_itens;
drop policy if exists leitura_publica on cupom_itens;
create policy leitura_publica on cupom_itens for select to anon
  using (exists (select 1 from cupons c where c.id = cupom_id and c.ativo));
drop policy if exists leitura_logada on cupom_itens;
create policy leitura_logada on cupom_itens for select to authenticated
  using (exists (select 1 from cupons c where c.id = cupom_id and (c.ativo or eh_admin())));

-- ---------------------------------------------------------------- histórico de alterações
create table if not exists alteracoes (
  id          bigserial primary key,
  tabela      text not null,
  registro_id uuid,
  acao        text not null check (acao in ('criou','alterou','removeu')),
  antes       jsonb,                                          -- só os campos que mudaram (em 'alterou')
  depois      jsonb,
  origem      text not null,                                  -- importacao | painel
  alterado_por uuid,                                          -- auth.uid() (null = importação)
  criado_em   timestamptz not null default now()
);
create index if not exists idx_alteracoes_registro on alteracoes (tabela, registro_id, criado_em desc);
create index if not exists idx_alteracoes_data     on alteracoes (criado_em desc);
alter table alteracoes enable row level security;            -- sem policy: ninguém lê pelo navegador
revoke all on alteracoes from anon, authenticated;
grant all on alteracoes to service_role;
grant usage, select on sequence alteracoes_id_seq to service_role;

create or replace function registrar_alteracao() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  o jsonb := case when tg_op <> 'INSERT' then to_jsonb(old) end;
  n jsonb := case when tg_op <> 'DELETE' then to_jsonb(new) end;
  a jsonb := '{}'; d jsonb := '{}'; k text;
begin
  if tg_op = 'UPDATE' then
    -- a importação regrava todas as linhas a cada hora: só registra o que mudou de fato
    for k in select jsonb_object_keys(n) loop
      if k not in ('created_at', 'updated_at') and (o -> k) is distinct from (n -> k) then
        a := a || jsonb_build_object(k, o -> k); d := d || jsonb_build_object(k, n -> k);
      end if;
    end loop;
    if d = '{}' then return null; end if;
  end if;
  insert into alteracoes (tabela, registro_id, acao, antes, depois, origem, alterado_por)
  values (tg_table_name, coalesce(n ->> 'id', o ->> 'id')::uuid,
          case tg_op when 'INSERT' then 'criou' when 'UPDATE' then 'alterou' else 'removeu' end,
          case tg_op when 'UPDATE' then a else o end,
          case tg_op when 'UPDATE' then d else n end,
          case when auth.uid() is null then 'importacao' else 'painel' end, auth.uid());
  return null;
end $$;
revoke execute on function registrar_alteracao() from public, anon, authenticated;

do $$
declare t text;
begin
  foreach t in array array['concursos','lojas','loja_concurso','itens_enxoval','produtos_online',
                           'cupons','cupom_produtos','cupom_itens'] loop
    execute format('drop trigger if exists trg_alteracao on %I', t);
    execute format('create trigger trg_alteracao after insert or update or delete on %I
                    for each row execute function registrar_alteracao()', t);
  end loop;
end $$;
