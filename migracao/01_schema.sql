-- ============================================================================
-- Enxoval do Aprovado — Migração p/ Supabase (Etapa 1) — Esquema
-- Rode este script no SQL Editor do Supabase. É reexecutável:
-- os DROPs no topo (ordem inversa das FKs) permitem recriar do zero.
-- ============================================================================

drop table if exists precos          cascade;
drop table if exists loja_concurso   cascade;
drop table if exists itens_enxoval   cascade;
drop table if exists lojas           cascade;
drop table if exists concursos       cascade;

-- 1. Concursos (tabela raiz)
create table concursos (
  id uuid primary key default gen_random_uuid(),
  nome text not null unique,          -- ex: "CBMDF - 2025"
  estado text not null,               -- ex: "Distrito Federal"
  ativo boolean not null default true,
  created_at timestamptz not null default now()
);

-- 2. Lojas (tabela raiz)
create table lojas (
  id uuid primary key default gen_random_uuid(),
  nome text not null unique,          -- ex: "Cezar Uniformes"
  tipo text not null,                 -- "fisica" ou "online"
  telefone text,
  instagram text,
  link_maps text,
  ativo boolean not null default true,
  created_at timestamptz not null default now()
);

-- 3. Vínculo loja <-> concurso (resolve Concurso (1) e Concurso (2))
create table loja_concurso (
  id uuid primary key default gen_random_uuid(),
  loja_id uuid not null references lojas(id) on delete cascade,
  concurso_id uuid not null references concursos(id) on delete cascade,
  unique (loja_id, concurso_id)
);

-- 4. Itens do enxoval (lista padronizada por concurso)
create table itens_enxoval (
  id uuid primary key default gen_random_uuid(),
  concurso_id uuid not null references concursos(id) on delete cascade,
  categoria text,
  nome_padronizado text not null,
  qtd_sugerida int,
  cargo text,                         -- nullable; preparado para praça/oficial no futuro
  link_foto text,
  created_at timestamptz not null default now()
);

-- 5. Preços (cruzamento item x loja) — o coração do modelo
create table precos (
  id uuid primary key default gen_random_uuid(),
  item_id uuid not null references itens_enxoval(id) on delete cascade,
  loja_id uuid not null references lojas(id) on delete cascade,
  preco numeric,                      -- numeric, nunca float, para valor monetário
  link_produto text,                  -- para lojas online (a oferta específica)
  updated_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  unique (item_id, loja_id)
);

-- Índices para as foreign keys (consultas por concurso/loja/item)
create index idx_itens_concurso   on itens_enxoval (concurso_id);
create index idx_precos_item      on precos (item_id);
create index idx_precos_loja      on precos (loja_id);
create index idx_lc_loja          on loja_concurso (loja_id);
create index idx_lc_concurso      on loja_concurso (concurso_id);

-- Privilégios: o script de migração usa a service_role key (ignora RLS, mas
-- ainda precisa do privilégio de tabela). Neste projeto não há grant automático.
grant all privileges on all tables    in schema public to service_role;
grant all privileges on all sequences in schema public to service_role;
-- (anon/authenticated e RLS de leitura ficam para a etapa de integração do site.)
