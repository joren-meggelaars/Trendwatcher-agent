-- Runs once, automatically, when the postgres container initializes an empty
-- data directory (docker-entrypoint-initdb.d convention). Required before
-- Base.metadata.create_all() can create the pgvector-backed embedding column
-- on items.embedding (see scoring-service/app/models.py, EmbeddingVector).
CREATE EXTENSION IF NOT EXISTS vector;
