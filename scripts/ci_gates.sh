#!/usr/bin/env bash
# Gates SDD·BE em CI. Binários: qualquer FAIL derruba o build.
set -euo pipefail

DSN_SYNC="${GPS_DSN_SYNC:-postgresql+psycopg://gps:gps@localhost:5433/gplansis}"
# Sem psql no host, aponte para o container:
#   PSQL="docker compose -p gplansis exec -T db psql -U gps"
# Os scripts entram por stdin (-f -) justamente para funcionar dos dois jeitos.
PSQL="${PSQL:-psql}"
DB="${GPS_DB:-gplansis}"
PY="${PY:-}"   # prefixo opcional, ex.: .venv/Scripts/ (Windows) ou .venv/bin/

# psql prefixa cada erro com "psql:arquivo:linha:"; "^ERROR" nunca casaria.
# Erros em cascata ("transaction is aborted") não são recusas de invariante.
contar_recusas() { grep 'ERROR:' | grep -vc 'current transaction is aborted' || true; }

falhar() { echo "FAIL  $1"; exit 1; }
passar() { echo "PASS  $1"; }

echo "== BUILD: migration aplica, reverte e reaplica"
cd backend
GPS_DSN_SYNC="$DSN_SYNC" ${PY}alembic upgrade head    || falhar "BUILD upgrade"
GPS_DSN_SYNC="$DSN_SYNC" ${PY}alembic downgrade base  || falhar "BUILD downgrade"
GPS_DSN_SYNC="$DSN_SYNC" ${PY}alembic upgrade head    || falhar "BUILD reaplicação"
passar "BUILD"

echo "== SELF-CHECK: testes, tipos e lint"
${PY}pytest -q                             || falhar "SELF-CHECK pytest"
${PY}mypy --strict app/dominio app/importacao || falhar "SELF-CHECK mypy"
${PY}ruff check app tests alembic         || falhar "SELF-CHECK ruff"
passar "SELF-CHECK"

echo "== INV-ORG-001: nenhuma tabela sem RLS forçada"
SEM_RLS=$($PSQL -d "$DB" -tA -c "SELECT count(*) FROM plan.v_rls_auditoria" | tr -d '[:space:]')
[ "$SEM_RLS" = "0" ] || falhar "INV-ORG-001: $SEM_RLS tabela(s) sem RLS forçada"
passar "INV-ORG-001"

echo "== RED-TEAM: toda tentativa de violação deve falhar"
cd ..
ERROS_1=$($PSQL -d "$DB" -v ON_ERROR_STOP=0 -f - < db/002_red_team.sql 2>&1 | contar_recusas)
ERROS_2=$($PSQL -d "$DB" -v ON_ERROR_STOP=0 -f - < db/005_red_team_rls.sql 2>&1 | contar_recusas)
ERROS_3=$($PSQL -d "$DB" -v ON_ERROR_STOP=0 -f - < db/007_red_team_cronograma.sql 2>&1 | contar_recusas)
echo "   violações recusadas: $ERROS_1 (núcleo) + $ERROS_2 (RLS/importação) + $ERROS_3 (cronograma)"
[ "$ERROS_1" -ge 18 ] || falhar "RED-TEAM núcleo: esperado >= 18 recusas, obtido $ERROS_1"
[ "$ERROS_2" -ge 6 ]  || falhar "RED-TEAM RLS: esperado >= 6 recusas, obtido $ERROS_2"
[ "$ERROS_3" -ge 8 ]  || falhar "RED-TEAM cronograma: esperado >= 8 recusas, obtido $ERROS_3"
passar "RED-TEAM"

echo "== FRONT: tipos estritos e build"
if [ -d frontend/node_modules ]; then
    (cd frontend && npx tsc --noEmit) || falhar "FRONT tsc"
    (cd frontend && npx vite build --logLevel error) || falhar "FRONT build"
    passar "FRONT"
else
    echo "   (frontend/node_modules ausente — npm install para incluir o gate FRONT)"
fi

echo
echo "Gates SPEC, BUILD, SELF-CHECK, RED-TEAM e FRONT: PASS."
echo "HUMAN-GATE e DONE permanecem manuais."
