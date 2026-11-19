-- ============================================================================
-- Enxoval do Aprovado — trava da fonte de dados com PRAZO DE VALIDADE
-- Rode no SQL Editor DEPOIS do 11_escrita.sql. Reexecutável.
--
-- "Painel liberado" = fonte_dados = 'painel' E (liberado_ate vazio OU no futuro).
--  * Janela de teste: definir_fonte_dados('painel', 60) → libera por 60 min; passado
--    o prazo o painel volta a ser bloqueado e a importação volta a rodar, sozinhos.
--  * Virada (etapa 6): definir_fonte_dados('painel') → sem prazo (permanente).
--  * definir_fonte_dados('planilha') → bloqueia já.
-- fonte_dados() passa a devolver a situação EFETIVA ('painel' só se liberado agora).
-- ============================================================================

alter table configuracoes add column if not exists liberado_ate timestamptz;

create or replace function _painel_liberado() returns boolean
language sql stable security definer set search_path = public as $$
  select coalesce((select fonte_dados = 'painel' and (liberado_ate is null or liberado_ate > now())
                     from configuracoes where id), false)
$$;
revoke execute on function _painel_liberado() from public, anon, authenticated;

create or replace function _exige_edicao() returns void
language plpgsql stable security definer set search_path = public as $$
begin
  if not pode_editar() then raise exception 'acesso negado' using errcode = '42501'; end if;
  if not _painel_liberado() then
    raise exception 'Os dados ainda vêm da planilha: faça a mudança por lá. O painel passa a editar na virada.'
      using errcode = 'ENX01';
  end if;
end $$;

create or replace function fonte_dados() returns text
language sql stable security definer set search_path = public as $$
  select case when _painel_liberado() then 'painel' else 'planilha' end
$$;
revoke execute on function fonte_dados() from public, anon;
grant execute on function fonte_dados() to authenticated, service_role;   -- o importador consulta

-- situação completa para o painel (faixa "Edição liberada até HH:MM")
create or replace function status_edicao() returns jsonb
language sql stable security definer set search_path = public as $$
  select jsonb_build_object('fonte', case when _painel_liberado() then 'painel' else 'planilha' end,
                            'liberado_ate', case when _painel_liberado() then liberado_ate end)
    from configuracoes where id
$$;
revoke execute on function status_edicao() from public, anon;
grant execute on function status_edicao() to authenticated;

drop function if exists definir_fonte_dados(text);
create or replace function definir_fonte_dados(p text, p_minutos int default null) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  if (select papel from admins where user_id = auth.uid()) is distinct from 'proprietario' then
    raise exception 'acesso negado' using errcode = '42501';
  end if;
  if p not in ('planilha','painel') then raise exception 'Fonte inválida.' using errcode = 'ENX02'; end if;
  if p_minutos is not null and p_minutos not between 1 and 240 then
    raise exception 'Prazo entre 1 e 240 minutos (vazio = sem prazo).' using errcode = 'ENX02';
  end if;
  update configuracoes
     set fonte_dados = p,
         liberado_ate = case when p = 'painel' and p_minutos is not null then now() + make_interval(mins => p_minutos) end,
         alterado_em = now(), alterado_por = auth.uid()
   where id;
  return status_edicao();
end $$;
revoke execute on function definir_fonte_dados(text, int) from public, anon;
grant execute on function definir_fonte_dados(text, int) to authenticated;
