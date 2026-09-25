-- infra/postgres_init.sql : one-time database bootstrap (run via psql).
-- 1) install PostgreSQL 15+ and the pgvector extension binaries first
--    (vector.dll -> <PG>\lib, vector.control + vector--*.sql -> <PG>\share\extension).
-- 2) psql -U postgres -f infra/postgres_init.sql

-- App database + role (dev defaults match .env.example).
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'allmai') THEN
    CREATE ROLE allmai LOGIN PASSWORD 'allmai';
  END IF;
END
$$;

SELECT 'CREATE DATABASE allmai OWNER allmai'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'allmai')\gexec

GRANT ALL PRIVILEGES ON DATABASE allmai TO allmai;

-- Run the rest connected to the app database:
\c allmai

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

GRANT ALL ON SCHEMA public TO allmai;
ALTER SCHEMA public OWNER TO allmai;
