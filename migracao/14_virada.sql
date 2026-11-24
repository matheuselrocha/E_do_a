-- ============================================================================
-- Enxoval do Aprovado — preparação da virada (etapa 6, passo A1)
-- Rode no SQL Editor DEPOIS do 13_historico_precos.sql. Reexecutável.
--
-- 1) Origem "robo" no histórico: _origem() decide quem fez a mudança.
--      painel (conta logada) → 'painel' (não dá para forjar: a conta logada sempre vale 'painel');
--      sem login + enxoval.origem = 'robo' (só robo_preco_produto liga) → 'robo';
--      sem login (importação com service_role) → 'importacao'.
-- 2) robo_preco_produto(id, preco): o robô do Mercado Livre grava o preço do produto e,
--    como o painel faz, o preço dos itens ligados a ele na loja do produto. Só service_role.
--    Só grava com o painel liberado (depois da virada); antes disso recusa (ENX01).
-- 3) Renomear um produto obrigatório renomeia os itens ligados (decisão 1 da virada).
--    Se um concurso do produto já tiver OUTRO item com o novo nome, recusa com mensagem.
-- ============================================================================

create or replace function _origem() returns text
language sql stable security definer set search_path = public as $$
  select case
    when auth.uid() is not null then 'painel'
    when coalesce(current_setting('enxoval.origem', true), '') = 'robo' then 'robo'
    else 'importacao' end
$$;
revoke execute on function _origem() from public, anon, authenticated;

-- histórico geral (mesma lógica do 09, só a origem muda)
create or replace function registrar_alteracao() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  o jsonb := case when tg_op <> 'INSERT' then to_jsonb(old) end;
  n jsonb := case when tg_op <> 'DELETE' then to_jsonb(new) end;
  a jsonb := '{}'; d jsonb := '{}'; k text;
begin
  if tg_op = 'UPDATE' then
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
          _origem(), auth.uid());
  return null;
end $$;

-- histórico de preços (mesma lógica do 04, só a origem muda)
create or replace function precos_log() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  quem uuid := auth.uid();
  org  text := _origem();
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

-- ---------------------------------------------------------------- robô de preços
create or replace function robo_preco_produto(p_id uuid, p_preco numeric) returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  v_antes numeric; v_loja uuid; v_link text; v_novo numeric := round(p_preco, 2); n int := 0;
begin
  if not _painel_liberado() then
    raise exception 'Robô: os dados ainda vêm da planilha (grave na planilha até a virada).' using errcode = 'ENX01';
  end if;
  if p_preco is null or p_preco <= 0 or p_preco >= 100000 then perform _erro('Preço deve ser maior que zero e menor que R$ 100.000.'); end if;
  select preco, loja_id, link_produto into v_antes, v_loja, v_link from produtos_online where id = p_id;
  if not found then perform _erro('Produto não encontrado.'); end if;
  perform set_config('enxoval.origem', 'robo', true);       -- vale só nesta transação
  update produtos_online set preco = v_novo where id = p_id;
  if v_loja is not null then                                 -- itens "obrigatórios" acompanham (como no painel)
    insert into precos (item_id, loja_id, preco, link_produto)
    select i.id, v_loja, v_novo, v_link from itens_enxoval i where i.produto_online_id = p_id
    on conflict (item_id, loja_id) do update set preco = excluded.preco;
    get diagnostics n = row_count;
  end if;
  perform set_config('enxoval.origem', '', true);
  return jsonb_build_object('antes', v_antes, 'depois', v_novo, 'itens', n);
end $$;
revoke execute on function robo_preco_produto(uuid, numeric) from public, anon, authenticated;
grant execute on function robo_preco_produto(uuid, numeric) to service_role;

-- ---------------------------------------------------------------- catálogo: renomear leva os itens junto
create or replace function admin_salvar_produto(p jsonb) returns uuid
language plpgsql security definer set search_path = public as $$
declare
  v_id uuid := nullif(p ->> 'id', '')::uuid;
  v_nome text := _txt(p, 'nome');
  v_preco numeric := nullif(p ->> 'preco', '')::numeric;
  v_loja uuid := nullif(p ->> 'loja_id', '')::uuid;
  v_antigo text; v_conf text;
begin
  perform _exige_edicao();
  if v_nome is null then perform _erro('Informe o nome do produto.'); end if;
  if v_preco is not null and (v_preco <= 0 or v_preco >= 100000) then perform _erro('Preço deve ser maior que zero e menor que R$ 100.000.'); end if;
  if v_loja is not null and not exists (select 1 from lojas where id = v_loja) then perform _erro('Loja não encontrada.'); end if;
  if v_id is null then
    insert into produtos_online (categoria, nome, link_produto, link_imagem, preco, loja_id, ativo)
    values (_txt(p, 'categoria'), v_nome, _url(_txt(p, 'link_produto'), 'Link do produto'), _url(_txt(p, 'link_imagem'), 'Imagem'),
            round(v_preco, 2), v_loja, _bool(p, 'ativo', true))
    returning id into v_id;
  else
    select nome into v_antigo from produtos_online where id = v_id;
    if not found then perform _erro('Produto não encontrado.'); end if;
    -- renomear: algum concurso do produto já tem OUTRO item com o novo nome?
    if v_antigo is distinct from v_nome then
      select c.nome into v_conf
        from itens_enxoval i join concursos c on c.id = i.concurso_id
       where i.nome_padronizado = v_nome and i.produto_online_id is distinct from v_id
         and i.concurso_id in (select concurso_id from itens_enxoval where produto_online_id = v_id)
       limit 1;
      if v_conf is not null then
        perform _erro(format('O concurso %s já tem um item chamado "%s": renomeie ou exclua esse item antes.', v_conf, v_nome));
      end if;
    end if;
    update produtos_online set
      nome = v_nome,
      categoria = case when p ? 'categoria' then _txt(p, 'categoria') else categoria end,
      link_produto = case when p ? 'link_produto' then _url(_txt(p, 'link_produto'), 'Link do produto') else link_produto end,
      link_imagem = case when p ? 'link_imagem' then _url(_txt(p, 'link_imagem'), 'Imagem') else link_imagem end,
      preco = case when p ? 'preco' then round(v_preco, 2) else preco end,
      loja_id = case when p ? 'loja_id' then v_loja else loja_id end,
      ativo = _bool(p, 'ativo', ativo)
    where id = v_id;
    -- itens ligados ganham o novo nome (o id do item não muda)
    if v_antigo is distinct from v_nome then
      update itens_enxoval set nome_padronizado = v_nome where produto_online_id = v_id and nome_padronizado is distinct from v_nome;
    end if;
  end if;
  -- itens "obrigatórios" ligados a este produto: preço na loja do produto acompanha
  select preco, loja_id into v_preco, v_loja from produtos_online where id = v_id;
  if v_loja is not null and v_preco is not null then
    insert into precos (item_id, loja_id, preco, link_produto)
    select i.id, v_loja, v_preco, po.link_produto from itens_enxoval i join produtos_online po on po.id = i.produto_online_id
     where i.produto_online_id = v_id
    on conflict (item_id, loja_id) do update set preco = excluded.preco, link_produto = excluded.link_produto;
  end if;
  return v_id;
exception when unique_violation then perform _erro('Já existe um produto com esse nome no catálogo.'); return null;
end $$;
revoke execute on function admin_salvar_produto(jsonb) from public, anon;
grant execute on function admin_salvar_produto(jsonb) to authenticated;
