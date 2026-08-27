#!/usr/bin/env bash
set -eu

project_dir="${1:-/opt/acl-reference}"
data_dir="${2:-/opt/ACL_Dados_Editorais_GitHub}"

cd "$project_dir"
command -v git-lfs >/dev/null 2>&1 || {
  echo "Git LFS não está instalado no servidor." >&2
  exit 1
}
git -C "$data_dir" lfs install --local
git -C "$data_dir" lfs pull
test -f "$data_dir/current/manifest.json"
test -f "$data_dir/current/runtime.env"

cp "$data_dir/current/runtime.env" .env
set -a
. ./.env
set +a

docker compose down
docker compose build editorial public
docker compose run --rm --no-deps editorial \
  acl-reference --db /app/var/editorial.sqlite restore-github-backup \
  --repository /app/github-data \
  --releases-root /app/releases \
  --usage-db /app/var/usage.sqlite

docker compose up -d meilisearch
active_release=$(sed -n '1p' releases/ACTIVE_RELEASE)
docker compose run --rm --no-deps editorial \
  acl-reference bootstrap-release "/app/releases/$active_release" \
  --meili-url http://meilisearch:7700 \
  --meili-key "$MEILI_MASTER_KEY"
docker compose up -d public editorial
docker compose ps
