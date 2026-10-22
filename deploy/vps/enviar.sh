#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  Envia o GPlanSiS|Project para o VPS do hub e atualiza o serviço. Rode da
#  RAIZ do repositório (Git Bash no Windows funciona).
#
#     bash deploy/vps/enviar.sh              # código (+ frontend/dist se existir) e reinicia
#     PRIMEIRA=1 bash deploy/vps/enviar.sh   # código + instalar.sh (1ª vez; DEMO=1 semeia o demo)
#     SO_ENVIAR=1 bash deploy/vps/enviar.sh  # só copia; você roda o resto
#
#  `tar | ssh`, não rsync (o Git Bash não tem rsync).
#  Antes: cd frontend && npm run build   (usa .env.production → API na mesma origem)
# ---------------------------------------------------------------------------
set -euo pipefail

VPS="${VPS:?defina VPS=usuario@seu-servidor}"
CHAVE="${CHAVE:-$HOME/.ssh/id_ed25519}"
APP=/opt/gplansis-project
WEB=$APP/web
PORTA="${PORTA:-8800}"
SSH="ssh -i $CHAVE -o BatchMode=yes -o ConnectTimeout=20"

[[ -f backend/app/main.py ]] || { echo "rode da raiz do repositório" >&2; exit 1; }
[[ -f "$CHAVE" ]] || { echo "chave $CHAVE não encontrada" >&2; exit 1; }

# O que NUNCA sobe: .env (produção tem o seu), .venv (binários Windows),
# node_modules/dist (o bundle vai separado), testes, exportações e HTMLs de demo.
EXCLUIR=(
  --exclude='.git' --exclude='.github' --exclude='.claude'
  --exclude='__pycache__' --exclude='*.pyc' --exclude='.pytest_cache' --exclude='.mypy_cache' --exclude='.ruff_cache'
  --exclude='backend/.venv' --exclude='.env' --exclude='.env.local' --exclude='frontend/.env.local'
  --exclude='frontend/node_modules' --exclude='frontend/dist' --exclude='frontend/dev-dist'
  --exclude='backend/tests' --exclude='demo_v2_exportacoes' --exclude='*.jsonl'
  --exclude='frontend/public/apresentacao'
)

echo "==> código -> $VPS:$APP"
tar czf - "${EXCLUIR[@]}" backend db scripts templates deploy frontend/public/templates INSTALL.md README.md \
  | $SSH "$VPS" "mkdir -p $APP && tar xzf - -C $APP --overwrite --no-same-owner"
echo "    enviado"

if [[ -d frontend/dist && -f frontend/dist/index.html ]]; then
  echo "==> frontend (dist/) -> $VPS:$WEB"
  tar czf - -C frontend/dist . \
    | $SSH "$VPS" "mkdir -p $WEB && rm -rf $WEB/* && tar xzf - -C $WEB --no-same-owner && chown -R gpsproject:gpsproject $WEB 2>/dev/null || true"
  echo "    enviado ($(find frontend/dist -type f | wc -l) arquivos)"
else
  echo "==> frontend: dist/ ausente, não envio.  (cd frontend && npm run build)"
fi

if [[ "${SO_ENVIAR:-0}" == 1 ]]; then
  echo "==> só envio. Para aplicar:  ssh -i $CHAVE $VPS 'bash $APP/deploy/vps/instalar.sh'"; exit 0
fi

if [[ "${PRIMEIRA:-0}" == 1 ]]; then
  echo "==> instalar.sh (primeira instalação)"
  $SSH "$VPS" "DOMINIO=${DOMINIO:-project.gplansis.com} PORTA=$PORTA DEMO=${DEMO:-0} \
               ADMIN_EMAIL=${ADMIN_EMAIL:?defina ADMIN_EMAIL} ADMIN_SENHA='${ADMIN_SENHA:-}' \
               bash $APP/deploy/vps/instalar.sh"
  exit 0
fi

# Atualização: dependências (se mudaram), migrations pendentes e reinício.
echo "==> atualizando serviço"
$SSH "$VPS" "set -e
  UV=\$(command -v uv || echo /root/.local/bin/uv)
  UV_PYTHON_INSTALL_DIR=$APP/.python \$UV pip install --quiet --python $APP/.venv/bin/python -r $APP/backend/requirements-prod.txt
  cd $APP/backend && sudo -u gpsproject env PYTHONUTF8=1 GPS_DSN_SYNC='postgresql+psycopg://gpsproject@/gplansis_project?host=/var/run/postgresql' $APP/.venv/bin/alembic upgrade head | tail -2
  systemctl restart gpsproject
  sleep 4
  systemctl is-active --quiet gpsproject && echo '    gpsproject ativo' || { journalctl -u gpsproject -n 20 --no-pager; exit 1; }
  curl -s --max-time 10 http://127.0.0.1:$PORTA/saude; echo"
