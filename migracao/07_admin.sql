-- ============================================================================
-- Enxoval do Aprovado — painel administrativo (equipe do Enxoval do Aprovado)
-- Rode no SQL Editor DEPOIS do 06_metricas.sql. Reexecutável.
--
-- Modelo:
--  * Admin = conta do Supabase Auth listada em `admins` (inserida só pelo SQL
--    Editor; ninguém se autopromove: a tabela não tem nenhuma policy).
--  * O admin vê as métricas de TODAS as lojas (somadas e por loja), com
--    período livre, por metricas_admin(). Continua sem acesso direto a
--    eventos_loja, leads e precos_historico pelo navegador.
-- ============================================================================

create table if not exists admins (
  user_id    uuid primary key references auth.users(id) on delete cascade,
  created_at timestamptz not null default now()
);
alter table admins enable row level security;          -- sem policy = ninguém lê/escreve pelo site
revoke all on admins from anon, authenticated;
grant all on admins to service_role;

create or replace function eh_admin() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from admins where user_id = auth.uid())
$$;
revoke execute on function eh_admin() from public, anon;
grant execute on function eh_admin() to authenticated;

-- Métricas gerais. p_loja = null → todas as lojas somadas; senão, só aquela loja.
-- Sempre devolve também o ranking `lojas` do período (todas as lojas com evento).
create or replace function metricas_admin(p_inicio date, p_fim date, p_loja uuid default null) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
  v_ini timestamptz := (p_inicio::timestamp) at time zone 'America/Sao_Paulo';
  v_fim timestamptz := ((p_fim + 1)::timestamp) at time zone 'America/Sao_Paulo';
  r jsonb;
begin
  if not eh_admin() then raise exception 'acesso negado' using errcode = '42501'; end if;
  r := _metricas(p_loja, p_inicio, p_fim);
  if r is null then return null; end if;                -- período inválido (vazio ou > 366 dias)

  return r || jsonb_build_object('lojas', coalesce((
    select jsonb_agg(jsonb_build_object(
             'id', l.id, 'nome', l.nome, 'parceira', l.parceira, 'ativo', l.ativo,
             'whatsapp', t.w, 'valor_whatsapp', t.vw, 'contato', t.ct, 'carrinho', t.c,
             'pdf', t.f, 'loja_unica', t.u, 'pessoas', t.p)
           order by t.w desc, t.c desc, l.nome)
      from (select loja_id,
                   count(*) filter (where tipo = 'whatsapp') w,
                   coalesce(sum(valor) filter (where tipo = 'whatsapp'), 0) vw,
                   count(*) filter (where tipo in ('contato','instagram','mapa')) ct,
                   count(*) filter (where tipo = 'carrinho') c,
                   count(*) filter (where tipo = 'pdf') f,
                   count(*) filter (where tipo = 'loja_unica') u,
                   count(distinct sessao) p
              from eventos_loja
             where criado_em >= v_ini and criado_em < v_fim
             group by loja_id) t
      join lojas l on l.id = t.loja_id), '[]'::jsonb));
end $$;
revoke execute on function metricas_admin(date, date, uuid) from public, anon;
grant execute on function metricas_admin(date, date, uuid) to authenticated;

-- ----------------------------------------------------------------------------
-- COMO CRIAR UM ADMIN (depois de criar o usuário em Authentication → Add user):
--   insert into admins (user_id)
--   select id from auth.users where email = 'email@da.equipe'
--   on conflict do nothing;
-- Uma conta pode ser admin E estar ligada a uma loja (loja_usuarios); no painel
-- ela entra direto na administração.
-- ----------------------------------------------------------------------------
