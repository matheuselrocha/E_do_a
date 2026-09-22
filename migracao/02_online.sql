-- ============================================================================
-- Enxoval do Aprovado — Migração p/ Supabase (Etapa 1b)
-- Catálogo de produtos online + vínculo item-obrigatório <-> produto online.
-- Rode no SQL Editor DEPOIS do 01_schema.sql. Reexecutável (if not exists).
-- ============================================================================

-- Catálogo "Compras Online": produtos globais (aparecem em todos os concursos).
create table if not exists produtos_online (
  id uuid primary key default gen_random_uuid(),
  categoria text,
  nome text not null,
  link_produto text,
  link_imagem text,
  preco numeric,                                         -- preço exibido na aba "Compras Online" (numeric, nunca float)
  loja_id uuid references lojas(id) on delete set null,  -- loja online que vende (quando identificável)
  ativo boolean not null default true,
  created_at timestamptz not null default now()
);

-- coluna de preço para catálogos já criados antes desta versão (reexecutável)
alter table produtos_online
  add column if not exists preco numeric;

-- Um item obrigatório (por concurso) pode SER também um produto do catálogo online.
-- Quando preenchido, o item da calculadora e o produto do catálogo são o mesmo produto real.
alter table itens_enxoval
  add column if not exists produto_online_id uuid references produtos_online(id) on delete set null;

create index if not exists idx_prod_online_loja  on produtos_online (loja_id);
create index if not exists idx_itens_prod_online on itens_enxoval (produto_online_id);

-- privilégios para o script de migração (service_role)
grant all privileges on all tables    in schema public to service_role;
grant all privileges on all sequences in schema public to service_role;
