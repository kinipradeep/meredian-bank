#!/bin/sh
# Runs once, on the first start of an empty Postgres volume: extra databases for LiteLLM and Langfuse.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
  CREATE DATABASE litellm;
  CREATE DATABASE langfuse;
SQL
