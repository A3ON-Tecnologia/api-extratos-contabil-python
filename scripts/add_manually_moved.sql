-- Migration: add manually_moved flag to logs
-- Run on MySQL (adjust schema/database as needed).

ALTER TABLE extratos_log
  ADD COLUMN manually_moved TINYINT(1) NOT NULL DEFAULT 0;

ALTER TABLE extratos_baixados_log
  ADD COLUMN manually_moved TINYINT(1) NOT NULL DEFAULT 0;

ALTER TABLE extratos_log_teste
  ADD COLUMN manually_moved TINYINT(1) NOT NULL DEFAULT 0;

ALTER TABLE extratos_baixados_log_teste
  ADD COLUMN manually_moved TINYINT(1) NOT NULL DEFAULT 0;

-- If you use Alembic, create a migration with equivalent SQL or use op.add_column(...)
-- Example (Alembic revision upgrade):
-- op.add_column('extratos_log', sa.Column('manually_moved', sa.Boolean(), nullable=False, server_default=sa.text('0')))
-- Repeat for the other three tables.

-- Usage:
-- 1) Backup your database.
-- 2) Run: mysql -u <user> -p <database> < add_manually_moved.sql
-- 3) Restart the application if necessary.
