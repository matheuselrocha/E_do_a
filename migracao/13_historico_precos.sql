-- ============================================================================
-- Enxoval do Aprovado — leitura do histórico de PREÇOS pela equipe (aba Histórico)
-- Rode no SQL Editor DEPOIS do 12_trava_prazo.sql. Reexecutável. Só leitura.
--
-- precos_historico continua fechada (RLS sem política); a equipe lê por esta função,
-- que confere eh_admin() e já traz nomes de item, concurso, loja e o e-mail do autor.
-- ============================================================================

drop function if exists admin_historico_precos(uuid, uuid, int);
create function admin_historico_precos(p_item uuid default null, p_loja uuid default null, p_limite int default 100)
returns table (alterado_em timestamptz, item_id uuid, item text, concurso text, loja_id uuid, loja text,
               preco_antigo numeric, preco_novo numeric, origem text, autor text)
language plpgsql stable security definer set search_path = public as $$
begin
  if not eh_admin() then raise exception 'acesso negado' using errcode = '42501'; end if;
  return query
    select h.alterado_em, h.item_id, i.nome_padronizado, c.nome, h.loja_id, l.nome,
           h.preco_antigo, h.preco_novo, h.origem, u.email::text
      from precos_historico h
      left join itens_enxoval i on i.id = h.item_id
      left join concursos c on c.id = i.concurso_id
      left join lojas l on l.id = h.loja_id
      left join auth.users u on u.id = h.alterado_por
     where (p_item is null or h.item_id = p_item) and (p_loja is null or h.loja_id = p_loja)
     order by h.alterado_em desc, h.id desc
     limit least(greatest(coalesce(p_limite, 100), 1), 500);
end $$;
revoke execute on function admin_historico_precos(uuid, uuid, int) from public, anon;
grant execute on function admin_historico_precos(uuid, uuid, int) to authenticated;
