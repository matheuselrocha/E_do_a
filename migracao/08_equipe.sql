-- ============================================================================
-- Enxoval do Aprovado — equipe do painel administrativo (papéis + convites)
-- Rode no SQL Editor DEPOIS do 07_admin.sql. Reexecutável.
--
-- Papéis (coluna admins.papel):
--  * proprietario — definido SÓ aqui no SQL (no máximo um). Convida, troca papel
--                   e remove membros (convite/remoção pela Edge Function `equipe`).
--  * admin        — vai poder editar os dados (concursos, itens, lojas, cupons)
--                   conforme essas funções saírem da planilha (checar pode_editar()).
--  * leitor       — só vê.
-- A tabela continua sem policy: ninguém lê/escreve direto pelo navegador; tudo
-- passa pelas funções abaixo (security definer) ou pela Edge Function.
-- ============================================================================

alter table admins add column if not exists papel text not null default 'leitor';
alter table admins add column if not exists convidado_por uuid references auth.users(id) on delete set null;
do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'admins_papel_chk') then
    alter table admins add constraint admins_papel_chk check (papel in ('proprietario','admin','leitor'));
  end if;
end $$;
create unique index if not exists admins_um_proprietario on admins ((true)) where papel = 'proprietario';

-- papel da conta logada (null = não é da equipe)
create or replace function meu_papel() returns text
language sql stable security definer set search_path = public as $$
  select papel from admins where user_id = auth.uid()
$$;
revoke execute on function meu_papel() from public, anon;
grant execute on function meu_papel() to authenticated;

-- pode alterar dados (proprietário ou administrador) — usar nas futuras funções de edição
create or replace function pode_editar() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from admins where user_id = auth.uid() and papel in ('proprietario','admin'))
$$;
revoke execute on function pode_editar() from public, anon;
grant execute on function pode_editar() to authenticated;

-- lista da equipe (qualquer membro vê; pendente = nunca entrou, convite ainda não aceito)
drop function if exists listar_equipe();
create function listar_equipe()
returns table (user_id uuid, email text, papel text, criado_em timestamptz, ultimo_acesso timestamptz, pendente boolean)
language plpgsql stable security definer set search_path = public as $$
begin
  if not eh_admin() then raise exception 'acesso negado' using errcode = '42501'; end if;
  return query
    select a.user_id, u.email::text, a.papel, a.created_at, u.last_sign_in_at, (u.last_sign_in_at is null)
      from admins a join auth.users u on u.id = a.user_id
     order by case a.papel when 'proprietario' then 0 when 'admin' then 1 else 2 end, u.email;
end $$;
revoke execute on function listar_equipe() from public, anon;
grant execute on function listar_equipe() to authenticated;

-- troca de papel (só o proprietário; o papel de proprietário não sai nem entra por aqui)
create or replace function alterar_papel(p_user uuid, p_papel text) returns void
language plpgsql security definer set search_path = public as $$
begin
  if (select papel from admins where user_id = auth.uid()) is distinct from 'proprietario' then
    raise exception 'acesso negado' using errcode = '42501';
  end if;
  if p_papel not in ('admin','leitor') then raise exception 'papel inválido' using errcode = '22023'; end if;
  update admins set papel = p_papel where user_id = p_user and papel <> 'proprietario';
  if not found then raise exception 'membro não encontrado' using errcode = 'P0002'; end if;
end $$;
revoke execute on function alterar_papel(uuid, text) from public, anon;
grant execute on function alterar_papel(uuid, text) to authenticated;

-- uso interno da Edge Function (service_role): conta existente pelo e-mail
create or replace function _usuario_por_email(p_email text) returns uuid
language sql stable security definer set search_path = public as $$
  select id from auth.users where lower(email) = lower(trim(p_email)) limit 1
$$;
revoke execute on function _usuario_por_email(text) from public, anon, authenticated;
grant execute on function _usuario_por_email(text) to service_role;

-- ----------------------------------------------------------------------------
-- DEFINIR O PROPRIETÁRIO (a conta precisa existir em Authentication → Users):
--   insert into admins (user_id, papel)
--   select id, 'proprietario' from auth.users where email = 'seu@email.com'
--   on conflict (user_id) do update set papel = 'proprietario';
-- Trocar de proprietário: primeiro rebaixe o atual
--   (update admins set papel = 'admin' where papel = 'proprietario'), depois rode o insert acima.
-- ----------------------------------------------------------------------------
