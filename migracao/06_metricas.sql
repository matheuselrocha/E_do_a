-- ============================================================================
-- Enxoval do Aprovado — Fase 2: métricas por loja
-- Rode no SQL Editor DEPOIS do 05_painel.sql. Reexecutável.
--
-- Modelo:
--  * O site (anon) NÃO escreve na tabela: chama registrar_eventos(), que valida
--    tudo (tipo, loja ativa, concurso, item, faixas de valor) e resolve os nomes
--    em IDs. Loja/concurso/item desconhecidos → o evento é descartado.
--  * Ninguém lê eventos_loja diretamente (nem anon, nem lojas). A loja logada só
--    vê os PRÓPRIOS números, já agregados, por metricas_minha_loja().
--  * Registra eventos de TODAS as lojas ativas (não só parceiras): serve de
--    argumento comercial ("sua loja recebeu N contatos no mês").
--  * `sessao` = id aleatório do navegador (sem dado pessoal) → "pessoas" = aparelhos.
-- ============================================================================

create table if not exists eventos_loja (
  id          bigint generated always as identity primary key,
  loja_id     uuid not null,          -- sem FK de propósito: métricas sobrevivem a remoções
  concurso_id uuid,
  item_id     uuid,
  tipo        text not null check (tipo in ('whatsapp','contato','instagram','mapa','carrinho','pdf','loja_unica')),
  via         text,                   -- como o item entrou no carrinho da loja: manual | selecionar_tudo | itens_iniciais | loja_unica | troca_loja | mais_baratas
  valor       numeric,                -- R$ (subtotal do carrinho/PDF ou do item)
  qtd         int,                    -- nº de itens (whatsapp/pdf/loja_unica) ou unidades (carrinho)
  sessao      text,
  criado_em   timestamptz not null default now()
);
create index if not exists idx_eventos_loja_data   on eventos_loja (loja_id, criado_em desc);
create index if not exists idx_eventos_sessao_data on eventos_loja (sessao, criado_em desc);

alter table eventos_loja enable row level security;   -- sem policy = ninguém lê/escreve direto
revoke all on eventos_loja from anon, authenticated;
grant all on eventos_loja to service_role;

-- ----------------------------------------------------------------------------
-- 1) gravação (site, anônimo)
--    p_eventos = [{ "t":"whatsapp", "l":"Nome da Loja", "c":"CBMDF - 2025",
--                   "i":"Nome do item", "v":123.4, "q":3, "via":"manual" }, ...]
-- ----------------------------------------------------------------------------
create or replace function registrar_eventos(p_sessao text, p_eventos jsonb) returns void
language plpgsql security definer set search_path = public as $$
declare
  e jsonb; v_loja uuid; v_conc uuid; v_item uuid; v_valor numeric; v_qtd int; v_via text;
  v_sessao text := nullif(left(coalesce(p_sessao, ''), 40), '');
begin
  if jsonb_typeof(p_eventos) is distinct from 'array' or jsonb_array_length(p_eventos) > 80 then return; end if;
  -- freio contra enxurrada: um aparelho grava no máximo 400 eventos a cada 10 min
  if v_sessao is not null and (select count(*) from eventos_loja
       where sessao = v_sessao and criado_em > now() - interval '10 minutes') > 400 then return; end if;

  for e in select * from jsonb_array_elements(p_eventos) loop
    continue when jsonb_typeof(e) <> 'object'
              or coalesce(e->>'t', '') not in ('whatsapp','contato','instagram','mapa','carrinho','pdf','loja_unica');

    select id into v_loja from lojas where ativo and nome = e->>'l';
    if v_loja is null then
      select id into v_loja from lojas where ativo and lower(trim(nome)) = lower(trim(e->>'l')) limit 1;
    end if;
    continue when v_loja is null;

    v_conc := null; v_item := null;
    select id into v_conc from concursos where nome = e->>'c';
    if v_conc is not null and e ? 'i' then
      select id into v_item from itens_enxoval where concurso_id = v_conc and nome_padronizado = e->>'i';
    end if;

    v_valor := case when jsonb_typeof(e->'v') = 'number' and (e->>'v')::numeric between 0 and 1000000
                    then round((e->>'v')::numeric, 2) end;
    v_qtd   := case when jsonb_typeof(e->'q') = 'number' and (e->>'q')::numeric between 0 and 10000
                    then (e->>'q')::numeric::int end;
    v_via   := case when e->>'via' in ('manual','selecionar_tudo','itens_iniciais','loja_unica','troca_loja','mais_baratas') then e->>'via' end;

    insert into eventos_loja (loja_id, concurso_id, item_id, tipo, via, valor, qtd, sessao)
    values (v_loja, v_conc, v_item, e->>'t', v_via, v_valor, v_qtd, v_sessao);
  end loop;
end $$;
revoke execute on function registrar_eventos(text, jsonb) from public;
grant execute on function registrar_eventos(text, jsonb) to anon, authenticated;

-- ----------------------------------------------------------------------------
-- 2) cálculo (interno) — p_loja = null soma TODAS as lojas (usado pelo admin, 07_admin.sql)
--    Datas no fuso de Brasília, de p_inicio a p_fim inclusive; "anterior" = o
--    mesmo nº de dias imediatamente antes, para comparação.
-- ----------------------------------------------------------------------------
create or replace function _metricas(p_loja uuid, p_inicio date, p_fim date) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
  v_dias int := p_fim - p_inicio + 1;
  v_ini  timestamptz := (p_inicio::timestamp) at time zone 'America/Sao_Paulo';
  v_fim  timestamptz := ((p_fim + 1)::timestamp) at time zone 'America/Sao_Paulo';
  v_ant  timestamptz := ((p_inicio - v_dias)::timestamp) at time zone 'America/Sao_Paulo';
  r jsonb;
begin
  if p_inicio is null or p_fim is null or v_dias < 1 or v_dias > 366 then return null; end if;

  with ev as (
    select * from eventos_loja
     where (p_loja is null or loja_id = p_loja) and criado_em >= v_ant and criado_em < v_fim
  ), atual as (select * from ev where criado_em >= v_ini),
     prev  as (select * from ev where criado_em <  v_ini)
  select jsonb_build_object(
    'dias', v_dias,
    'inicio', p_inicio,
    'fim', p_fim,
    'totais', coalesce((select jsonb_object_agg(tipo, jsonb_build_object(
                 'eventos', n, 'pessoas', p, 'valor', v))
               from (select tipo, count(*) n, count(distinct sessao) p, coalesce(sum(valor), 0) v
                       from atual group by tipo) t), '{}'::jsonb),
    'anterior', coalesce((select jsonb_object_agg(tipo, jsonb_build_object('eventos', n, 'pessoas', p))
               from (select tipo, count(*) n, count(distinct sessao) p from prev group by tipo) t), '{}'::jsonb),
    'pessoas', (select count(distinct sessao) from atual),
    'por_dia', coalesce((select jsonb_agg(jsonb_build_object('dia', d, 'whatsapp', w, 'carrinho', c, 'pdf', f) order by d)
               from (select (criado_em at time zone 'America/Sao_Paulo')::date d,
                            count(*) filter (where tipo in ('whatsapp','contato')) w,
                            count(*) filter (where tipo = 'carrinho') c,
                            count(*) filter (where tipo = 'pdf') f
                       from atual group by 1) t), '[]'::jsonb),
    'itens', coalesce((select jsonb_agg(jsonb_build_object('nome', nome, 'concurso', conc, 'vezes', n, 'pessoas', p) order by n desc, nome)
               from (select i.nome_padronizado nome, c.nome conc, count(*) n, count(distinct a.sessao) p
                       from atual a
                       join itens_enxoval i on i.id = a.item_id
                       left join concursos c on c.id = a.concurso_id
                      where a.tipo = 'carrinho'
                      group by 1, 2 order by 3 desc, 1 limit 10) t), '[]'::jsonb),
    'concursos', coalesce((select jsonb_agg(jsonb_build_object('nome', nome, 'whatsapp', w, 'carrinho', c, 'pdf', f) order by nome)
               from (select c.nome,
                            count(*) filter (where a.tipo = 'whatsapp') w,
                            count(*) filter (where a.tipo = 'carrinho') c,
                            count(*) filter (where a.tipo = 'pdf') f
                       from atual a join concursos c on c.id = a.concurso_id
                      group by 1) t), '[]'::jsonb)
  ) into r;
  return r;
end $$;
revoke execute on function _metricas(uuid, date, date) from public, anon, authenticated;

-- ----------------------------------------------------------------------------
-- 3) leitura (painel da loja logada) — só os números agregados da PRÓPRIA loja
--    Período = últimos p_dias dias (inclui hoje).
-- ----------------------------------------------------------------------------
create or replace function metricas_minha_loja(p_dias int default 30) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
  v_loja uuid := minha_loja();
  v_dias int  := least(greatest(coalesce(p_dias, 30), 1), 365);
  v_hoje date := (now() at time zone 'America/Sao_Paulo')::date;
begin
  if v_loja is null then return null; end if;
  return _metricas(v_loja, v_hoje - (v_dias - 1), v_hoje);
end $$;
revoke execute on function metricas_minha_loja(int) from public, anon;
grant execute on function metricas_minha_loja(int) to authenticated;

-- ----------------------------------------------------------------------------
-- ADMIN (SQL Editor): resumo de todas as lojas nos últimos 30 dias
--   select l.nome, l.parceira, e.tipo, count(*) eventos, count(distinct e.sessao) pessoas,
--          sum(e.valor) valor
--     from eventos_loja e join lojas l on l.id = e.loja_id
--    where e.criado_em > now() - interval '30 days'
--    group by 1, 2, 3 order by 1, 3;
-- ----------------------------------------------------------------------------
