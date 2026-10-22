#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  GPlanSiS|Project — instalação num VPS Ubuntu (Caddy,
#  PostgreSQL do host). Mesmo método do GPlanSiS|ML (deploy/vps do M.L-SiS).
#
#  IDEMPOTENTE: role, banco e .env só nascem se não existirem; `alembic
#  upgrade head` aplica só o que falta; o admin master é upsert.
#
#  USO (como root, no VPS), depois de os arquivos estarem em /opt/gplansis-project:
#     DOMINIO=project.gplansis.com bash /opt/gplansis-project/deploy/vps/instalar.sh
#
#  Variáveis:
#     DOMINIO       nome público (default project.gplansis.com)
#     PORTA         porta interna do uvicorn (default 8800)
#     ADMIN_EMAIL   e-mail do admin master (obrigatório)
#     ADMIN_SENHA   senha do admin; vazio = gera e imprime
#     DEMO=1        semeia o projeto DEMO v2 via API (≈5 min) depois de subir
#     SEM_JAVA=1    não instala a JRE (importação .mpp responde 501 com instrução)
# ---------------------------------------------------------------------------
set -euo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP=/opt/gplansis-project
DADOS=/var/lib/gplansis-project
WEB=$APP/web
USUARIO=gpsproject
BANCO=gplansis_project
PAPEL_APP=gps_app

DOMINIO="${DOMINIO:-project.gplansis.com}"
PORTA="${PORTA:-8800}"
ADMIN_EMAIL="${ADMIN_EMAIL:?defina ADMIN_EMAIL (e-mail do admin master)}"
ADMIN_SENHA="${ADMIN_SENHA:-}"
SOCKET=/var/run/postgresql
DSN_PSYCOPG="postgresql://$USUARIO@/$BANCO?host=$SOCKET"

verde() { printf '  \033[32m[ok]\033[0m    %s\n' "$1"; }
aviso() { printf '  \033[33m[!]\033[0m     %s\n' "$1"; }
erro()  { printf '  \033[31m[erro]\033[0m  %s\n' "$1" >&2; }

echo; echo "    GPlanSiS|Project em $DOMINIO (porta interna $PORTA)"; echo

[[ $EUID -eq 0 ]] || { erro "rode como root"; exit 1; }
[[ -f $APP/backend/app/main.py ]] || { erro "$APP/backend/app/main.py não existe — envie o código primeiro"; exit 1; }
command -v caddy >/dev/null || { erro "caddy não está instalado"; exit 1; }
command -v psql  >/dev/null || { erro "postgresql não está instalado"; exit 1; }

# ---------------------------------------------------------------- DNS
IP_VPS="$(curl -s --max-time 8 https://api.ipify.org || true)"
IP_DNS="$(getent ahostsv4 "$DOMINIO" 2>/dev/null | awk 'NR==1{print $1}' || true)"
if [[ -z "$IP_DNS" ]]; then aviso "$DOMINIO ainda não resolve; TLS sai quando o A -> $IP_VPS propagar."
elif [[ "$IP_DNS" != "$IP_VPS" ]]; then aviso "$DOMINIO aponta para $IP_DNS, este VPS é $IP_VPS."
else verde "DNS: $DOMINIO -> $IP_VPS"; fi

# ---------------------------------------------------------------- usuário e dados
id "$USUARIO" &>/dev/null || useradd --system --home "$DADOS" --shell /usr/sbin/nologin "$USUARIO"
mkdir -p "$DADOS" "$WEB"
chown -R "$USUARIO:$USUARIO" "$DADOS"
verde "usuário de sistema $USUARIO e $DADOS"

# ---------------------------------------------------------------- JRE (.mpp)
if [[ "${SEM_JAVA:-0}" != 1 ]]; then
  if [[ ! -d /usr/lib/jvm/default-java ]]; then
    echo "  instalando default-jre-headless (leitura de .mpp via MPXJ)..."
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq default-jre-headless >/dev/null
  fi
  verde "JRE: $(/usr/lib/jvm/default-java/bin/java -version 2>&1 | head -1)"
fi

# ---------------------------------------------------------------- venv (Python 3.12 via uv)
# O sistema tem Python 3.14; as versões validadas do backend são 3.12. uv baixa
# um 3.12 isolado em /opt/gplansis-project/.python e cria o venv a partir dele.
export UV_PYTHON_INSTALL_DIR=$APP/.python
if ! command -v uv >/dev/null && [[ ! -x /root/.local/bin/uv ]]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
fi
UV="$(command -v uv || echo /root/.local/bin/uv)"
if [[ ! -x $APP/.venv/bin/python ]] || ! "$APP/.venv/bin/python" -c 'import sys; assert sys.version_info[:2]==(3,12)' 2>/dev/null; then
  rm -rf "$APP/.venv"
  "$UV" venv --python 3.12 --quiet "$APP/.venv"
fi
"$UV" pip install --quiet --python "$APP/.venv/bin/python" -r "$APP/backend/requirements-prod.txt"
verde "venv: $("$APP/.venv/bin/python" -V) + requirements-prod"

# ---------------------------------------------------------------- .env
if [[ ! -f $APP/.env ]]; then
  cat > "$APP/.env" <<EOF
# Gerado por instalar.sh em $(date -u +%FT%TZ). Edite à vontade; NÃO versione.
GPS_PORT=$PORTA
GPS_WORKERS=2
# Postgres do host, socket Unix, peer auth (usuário de sistema = role). Sem senha.
GPS_DSN=postgresql+asyncpg://$USUARIO@/$BANCO?host=$SOCKET
GPS_DSN_SYNC=postgresql+psycopg://$USUARIO@/$BANCO?host=$SOCKET
GPS_DB_ROLE=$PAPEL_APP
GPS_CORS_ORIGENS=https://$DOMINIO
GPS_PERMITIR_HEADER_ORG=0
GPS_SESSAO_HORAS=12
JAVA_HOME=/usr/lib/jvm/default-java
EOF
  chmod 640 "$APP/.env"; chown "root:$USUARIO" "$APP/.env"
  verde ".env criado"
else
  verde ".env preservado"
fi

# ---------------------------------------------------------------- banco
PSQL_SU() { su - postgres -c "psql -v ON_ERROR_STOP=1 -tAq $*"; }
PSQL_SU "-c \"SELECT 1 FROM pg_roles WHERE rolname='$USUARIO'\"" | grep -q 1 \
  || su - postgres -c "createuser --no-superuser --no-createdb --no-createrole '$USUARIO'"
PSQL_SU "-c \"SELECT 1 FROM pg_database WHERE datname='$BANCO'\"" | grep -q 1 \
  || su - postgres -c "createdb -O '$USUARIO' '$BANCO'"
# Extensões exigem superusuário; o 001_core.sql as encontra prontas (IF NOT EXISTS).
PSQL_SU "-d '$BANCO' -c 'CREATE EXTENSION IF NOT EXISTS btree_gist WITH SCHEMA public; CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public'"
# gps_app é papel de CLUSTER (003 o cria com IF NOT EXISTS, mas o dono do banco
# não tem CREATEROLE — de propósito). A conexão faz SET LOCAL ROLE gps_app, então
# o dono precisa ser membro.
PSQL_SU "-c \"SELECT 1 FROM pg_roles WHERE rolname='$PAPEL_APP'\"" | grep -q 1 \
  || PSQL_SU "-c 'CREATE ROLE $PAPEL_APP NOLOGIN'"
PSQL_SU "-c 'GRANT $PAPEL_APP TO $USUARIO'"
verde "banco $BANCO (dono $USUARIO) + btree_gist/pgcrypto + $PAPEL_APP"

# ---------------------------------------------------------------- migrations
chown -R root:root "$APP/backend" "$APP/db" "$APP/scripts" 2>/dev/null || true
if saida="$(cd "$APP/backend" && sudo -u "$USUARIO" env PYTHONUTF8=1 \
      "GPS_DSN_SYNC=postgresql+psycopg://$USUARIO@/$BANCO?host=$SOCKET" \
      "$APP/.venv/bin/alembic" upgrade head 2>&1)"; then
  echo "$saida" | tail -3 | sed 's/^/    /'; verde "alembic upgrade head"
else
  erro "alembic falhou:"; echo "$saida" | tail -25 | sed 's/^/    /'; exit 1
fi

# ---------------------------------------------------------------- admin master
GERADA=0
if [[ -z "$ADMIN_SENHA" ]]; then ADMIN_SENHA="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"; GERADA=1; fi
(cd "$APP" && sudo -u "$USUARIO" env PYTHONUTF8=1 "GPS_DSN_SEED=$DSN_PSYCOPG" \
   "$APP/.venv/bin/python" scripts/bootstrap_admin.py "$ADMIN_EMAIL" --senha "$ADMIN_SENHA" | sed 's/^/    /')
verde "admin master $ADMIN_EMAIL"

# ---------------------------------------------------------------- frontend
if [[ ! -f $WEB/index.html ]]; then
  cat > "$WEB/index.html" <<'HTML'
<!doctype html><html lang="pt-BR"><meta charset="utf-8"><title>GPlanSiS|Project</title>
<style>body{background:#1b1f21;color:#e2e8f0;font:14px/1.6 system-ui,sans-serif;max-width:44rem;margin:12vh auto;padding:0 1.5rem}a{color:#5cc8d6}</style>
<h1>GPlanSiS|Project</h1><p>A API está no ar; o bundle do frontend ainda não foi enviado.</p>
<p><a href="/saude">/saude</a></p></html>
HTML
  aviso "frontend não enviado — servindo página de status em /"
else
  verde "frontend em $WEB ($(find "$WEB" -type f | wc -l) arquivos)"
fi
chown -R "$USUARIO:$USUARIO" "$WEB"

# ---------------------------------------------------------------- systemd
sed -e "s#__APP__#$APP#g" -e "s#__DADOS__#$DADOS#g" -e "s#__USUARIO__#$USUARIO#g" \
    "$AQUI/gpsproject.service" > /etc/systemd/system/gpsproject.service
systemctl daemon-reload
systemctl enable gpsproject >/dev/null 2>&1
systemctl restart gpsproject
sleep 4
systemctl is-active --quiet gpsproject \
  && verde "gpsproject.service ativo (uvicorn em 127.0.0.1:$PORTA, usuário $USUARIO)" \
  || { erro "gpsproject.service não subiu:"; journalctl -u gpsproject -n 30 --no-pager | sed 's/^/    /'; exit 1; }

# ---------------------------------------------------------------- caddy
mkdir -p /var/log/caddy && touch /var/log/caddy/gpsproject.log && chown caddy:caddy /var/log/caddy/gpsproject.log
sed -e "s#__DOMINIO__#$DOMINIO#g" -e "s#__PORTA__#$PORTA#g" -e "s#__WEB__#$WEB#g" \
    "$AQUI/gpsproject.caddy" > /etc/caddy/gpsproject.caddy
if ! grep -q "gpsproject.caddy" /etc/caddy/Caddyfile; then
  cp /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.bak-$(date +%Y%m%d-%H%M%S)"
  printf '\n# GPlanSiS|Project (deploy separado — ver /etc/caddy/gpsproject.caddy)\nimport /etc/caddy/gpsproject.caddy\n' >> /etc/caddy/Caddyfile
fi
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
systemctl reload caddy
verde "caddy: bloco $DOMINIO -> 127.0.0.1:$PORTA"

# ---------------------------------------------------------------- verificação
saida="$(curl -s --max-time 10 "http://127.0.0.1:$PORTA/saude" || true)"
echo "$saida" | grep -q '"status":"ok"' && verde "/saude responde: $saida" || { erro "/saude: $saida"; exit 1; }
tok="$(curl -s --max-time 15 -H 'Content-Type: application/json' \
   -d "{\"email\":\"$ADMIN_EMAIL\",\"senha\":\"$ADMIN_SENHA\"}" "http://127.0.0.1:$PORTA/api/v1/auth/login" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("token",""))' 2>/dev/null || true)"
[[ -n "$tok" ]] && verde "login do admin master OK" || aviso "login do admin não respondeu com token"

# ---------------------------------------------------------------- demo (opcional)
if [[ "${DEMO:-0}" == 1 ]]; then
  echo "  semeando projeto DEMO v2 via API (≈5 min)..."
  (cd "$APP" && sudo -u "$USUARIO" env PYTHONUTF8=1 "GPS_API=http://127.0.0.1:$PORTA" \
     "GPS_ADMIN_EMAIL=$ADMIN_EMAIL" "GPS_ADMIN_SENHA=$ADMIN_SENHA" "GPS_DSN_SEED=$DSN_PSYCOPG" \
     HOME="$DADOS" "$APP/.venv/bin/python" scripts/demo_v2.py --somente 2>&1 | tail -5 | sed 's/^/    /') \
    && verde "DEMO v2 semeado" || aviso "demo_v2.py terminou com erro (veja acima)"
fi

echo
echo "  Pronto."
[[ $GERADA == 1 ]] && { echo; echo "   ADMIN: $ADMIN_EMAIL   senha: $ADMIN_SENHA   (guarde; troque no primeiro acesso)"; }
echo
echo "   https://$DOMINIO   ·  systemctl status gpsproject  ·  journalctl -u gpsproject -f"
echo "   tail -f /var/log/caddy/gpsproject.log"
echo "   sudo -u $USUARIO psql -d $BANCO -c 'select count(*) from plan.contrato'"
echo
