#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="olcrtc-panel"
APP_DIR="/opt/olcrtc-panel"
REPO_URL="${OLCRTC_PANEL_REPO:-https://github.com/lebrit/olcrtc-panel.git}"
PANEL_VERSION="0.1.4"
COMPOSE_FILE="$APP_DIR/docker-compose.yml"
ENV_FILE="$APP_DIR/.env"

need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "Запусти от root или через sudo."
    exit 1
  fi
}

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

detect_pm() {
  if has_cmd apt-get; then echo apt; return; fi
  if has_cmd dnf; then echo dnf; return; fi
  if has_cmd yum; then echo yum; return; fi
  if has_cmd pacman; then echo pacman; return; fi
  echo ""
}

install_packages() {
  local pm
  pm="$(detect_pm)"
  case "$pm" in
    apt)
      apt-get update
      DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl git openssl docker.io
      ;;
    dnf)
      dnf install -y ca-certificates curl git openssl docker
      ;;
    yum)
      yum install -y ca-certificates curl git openssl docker
      ;;
    pacman)
      pacman -Sy --noconfirm ca-certificates curl git openssl docker
      ;;
    *)
      echo "Неизвестный пакетный менеджер. Установи Docker, docker compose, git, curl, openssl вручную."
      exit 1
      ;;
  esac
}

compose_arch() {
  case "$(uname -m)" in
    x86_64|amd64) echo "x86_64" ;;
    aarch64|arm64) echo "aarch64" ;;
    armv7l|armv7) echo "armv7" ;;
    *)
      echo "Неподдерживаемая архитектура для auto-install Docker Compose: $(uname -m)"
      exit 1
      ;;
  esac
}

install_compose_from_github() {
  local os arch plugin_dir plugin_url plugin_path
  os="$(uname -s | tr '[:upper:]' '[:lower:]')"
  arch="$(compose_arch)"
  plugin_dir="/usr/local/lib/docker/cli-plugins"
  plugin_path="$plugin_dir/docker-compose"
  plugin_url="https://github.com/docker/compose/releases/latest/download/docker-compose-${os}-${arch}"

  echo "Docker Compose package не найден в репозиториях ОС. Ставлю Compose v2 CLI plugin из официального GitHub release."
  mkdir -p "$plugin_dir"
  curl -fL "$plugin_url" -o "$plugin_path"
  chmod 0755 "$plugin_path"
}

install_compose_package_if_available() {
  local pm
  pm="$(detect_pm)"
  case "$pm" in
    apt)
      apt-get update
      if apt-cache show docker-compose-plugin >/dev/null 2>&1; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-plugin
      elif apt-cache show docker-compose-v2 >/dev/null 2>&1; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-v2
      elif apt-cache show docker-compose >/dev/null 2>&1; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose || true
      fi
      ;;
    dnf)
      dnf install -y docker-compose-plugin || dnf install -y docker-compose || true
      ;;
    yum)
      yum install -y docker-compose-plugin || yum install -y docker-compose || true
      ;;
    pacman)
      pacman -Sy --noconfirm docker-compose || true
      ;;
  esac
}

ensure_compose() {
  if docker compose version >/dev/null 2>&1; then
    return
  fi

  echo "Docker установлен, но команда 'docker compose' недоступна. Проверяю доступные варианты установки Compose."
  install_compose_package_if_available

  if docker compose version >/dev/null 2>&1; then
    return
  fi

  install_compose_from_github

  if ! docker compose version >/dev/null 2>&1; then
    echo "Не удалось установить Docker Compose v2. Проверь Docker CLI plugin path и доступ к github.com."
    exit 1
  fi
}

ensure_deps() {
  if ! has_cmd docker || ! has_cmd git || ! has_cmd curl || ! has_cmd openssl; then
    install_packages
  fi
  systemctl enable --now docker >/dev/null 2>&1 || true
  ensure_compose
}

rand_token() {
  random_hex 32
}

random_hex() {
  local bytes="${1:-8}"
  if has_cmd openssl; then
    openssl rand -hex "$bytes"
    return
  fi
  if has_cmd od; then
    od -An -N"$bytes" -tx1 /dev/urandom | tr -d ' \n'
    return
  fi
  tr -dc 'a-z0-9' </dev/urandom | dd bs="$((bytes * 2))" count=1 2>/dev/null
}

secret_panel_path() {
  echo "/p-$(random_hex 10)"
}

server_ip() {
  curl -fsS --max-time 4 https://icanhazip.com 2>/dev/null | tr -d '[:space:]' || hostname -I | awk '{print $1}'
}

read_default() {
  local prompt="$1"
  local default="$2"
  local value
  value="$(read_prompt "$prompt" "$default")"
  echo "${value:-$default}"
}

read_prompt() {
  local prompt="$1"
  local default="${2:-}"
  local value=""
  local label
  if [ -n "$default" ]; then
    label="$prompt [$default]: "
  else
    label="$prompt: "
  fi
  if [ -r /dev/tty ] && [ -w /dev/tty ]; then
    printf '%s' "$label" >/dev/tty
    IFS= read -r value </dev/tty || value=""
  elif [ -t 0 ]; then
    read -r -p "$label" value || value=""
  else
    echo "Нет интерактивного терминала, используется значение по умолчанию для: $prompt" >&2
  fi
  echo "$value"
}

read_required() {
  local prompt="$1"
  read_prompt "$prompt" ""
}

normalize_path() {
  local value="$1"
  if [ -z "$value" ]; then
    value="$(secret_panel_path)"
  fi
  value="/${value#/}"
  value="${value%/}"
  echo "$value"
}

ensure_secret_path() {
  local value="$1"
  case "$value" in
    "/"|"/panel"|"/admin"|"/dashboard"|"/api"|"/assets"|"/sub")
      echo "Путь '$value' не выглядит секретным. Генерирую новый скрытый путь." >&2
      secret_panel_path
      ;;
    *)
      echo "$value"
      ;;
  esac
}

port_busy() {
  local port="$1"
  ss -ltn "( sport = :$port )" 2>/dev/null | grep -q ":$port" || return 1
}

print_port_diagnostics() {
  echo "Порты 80/443 заняты. Диагностика:"
  ss -ltnp '( sport = :80 or sport = :443 )' 2>/dev/null || true
  for svc in nginx apache2 httpd caddy; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
      echo "Активен сервис: $svc"
    fi
  done
}

maybe_stop_web_conflicts() {
  if ! port_busy 80 && ! port_busy 443; then
    return
  fi
  print_port_diagnostics
  local answer
  answer="$(read_default "Временно остановить nginx/apache2/httpd/caddy перед запуском Caddy контейнера?" "N")"
  case "$answer" in
    y|Y)
      for svc in nginx apache2 httpd caddy; do
        systemctl stop "$svc" >/dev/null 2>&1 || true
      done
      ;;
  esac
}

clone_or_update_repo() {
  if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" fetch --all --tags
    git -C "$APP_DIR" checkout main
    git -C "$APP_DIR" pull --ff-only
  else
    rm -rf "$APP_DIR"
    git clone "$REPO_URL" "$APP_DIR"
  fi
}

install_cli_wrapper() {
  cat > /usr/local/bin/olcrtc-panel <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
if [ "\$#" -eq 0 ]; then
  set -- menu
fi
exec bash "$APP_DIR/scripts/install.sh" "\$@"
EOF
  chmod 0755 /usr/local/bin/olcrtc-panel
}

write_caddyfile() {
  local domain="$1"
  local path="$2"
  local site
  site="$domain"
  if [ -z "$site" ]; then
    site=":8080"
  fi
  cat > "$APP_DIR/Caddyfile" <<EOF
$site {
    encode gzip zstd

    redir ${path} ${path}/ 308

    handle_path ${path}/* {
        reverse_proxy 127.0.0.1:18080
    }

    respond "not found" 404
}
EOF
}

write_env() {
  local domain="$1"
  local path="$2"
  local token="$3"
  local public_url
  local bind
  if [ -n "$domain" ]; then
    public_url="https://${domain}${path}"
  else
    public_url="http://$(server_ip):8080${path}"
  fi
  bind="127.0.0.1"
  cat > "$ENV_FILE" <<EOF
PANEL_VERSION=$PANEL_VERSION
PANEL_ADMIN_TOKEN=$token
PANEL_DOMAIN=$domain
PANEL_PATH=$path
PANEL_PUBLIC_BASE_URL=$public_url
PANEL_BIND=$bind
PANEL_PORT=18080
OLCRTC_DEFAULT_DNS=8.8.8.8:53
OLCRTC_DEFAULT_JITSI=https://fairmeeting.net
OLCRTC_REF=master
EOF
  chmod 600 "$ENV_FILE"
}

compose_up() {
  cd "$APP_DIR"
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
  if [ -n "${PANEL_DOMAIN:-}" ]; then
    maybe_stop_web_conflicts
  fi
  docker compose --profile caddy up -d --build --remove-orphans
}

install_cmd() {
  need_root
  local domain path token
  echo "Настройка панели:"
  domain="$(read_default "Домен панели, пусто для http://IP:8080" "")"
  path="$(normalize_path "$(read_default "Секретный путь панели" "$(secret_panel_path)")")"
  path="$(ensure_secret_path "$path")"
  token="$(rand_token)"

  ensure_deps
  clone_or_update_repo

  mkdir -p "$APP_DIR/data" "$APP_DIR/data/backups"

  write_caddyfile "$domain" "$path"
  write_env "$domain" "$path" "$token"
  install_cli_wrapper

  compose_up

  echo
  echo "Установка завершена."
  echo "URL: $(grep '^PANEL_PUBLIC_BASE_URL=' "$ENV_FILE" | cut -d= -f2-)"
  echo "Admin token: $token"
  echo
  echo "Меню: olcrtc-panel"
}

update_cmd() {
  need_root
  clone_or_update_repo
  install_cli_wrapper
  if [ ! -f "$ENV_FILE" ]; then
    echo "Нет $ENV_FILE. Сначала запусти install."
    exit 1
  fi
  compose_up
  echo "Обновлено до версии $PANEL_VERSION."
}

status_cmd() {
  cd "$APP_DIR"
  docker compose ps
}

logs_cmd() {
  cd "$APP_DIR"
  docker compose logs -f --tail=200
}

restart_cmd() {
  cd "$APP_DIR"
  docker compose restart
}

backup_cmd() {
  need_root
  local stamp target
  stamp="$(date +%Y%m%d-%H%M%S)"
  target="$APP_DIR/data/backups/olcrtc-panel-$stamp.tar.gz"
  tar -czf "$target" -C "$APP_DIR" .env Caddyfile data/panel.db data/runtime data/logs 2>/dev/null || true
  echo "Backup: $target"
}

uninstall_cmd() {
  need_root
  cd "$APP_DIR"
  docker compose --profile caddy down
  echo "Контейнеры остановлены. Файлы и данные сохранены в $APP_DIR."
}

delete_runtime_cmd() {
  need_root
  confirm="$(read_required "Введите DELETE-VOLUMES для удаления runtime/container volumes")"
  [ "$confirm" = "DELETE-VOLUMES" ] || exit 1
  cd "$APP_DIR"
  docker compose --profile caddy down -v
  rm -rf "$APP_DIR/data/runtime" "$APP_DIR/data/logs"
  mkdir -p "$APP_DIR/data/runtime" "$APP_DIR/data/logs"
  echo "Runtime удалён, база и backups сохранены."
}

delete_backups_cmd() {
  need_root
  confirm="$(read_required "Введите DELETE-BACKUPS для удаления локальных backups")"
  [ "$confirm" = "DELETE-BACKUPS" ] || exit 1
  rm -rf "$APP_DIR/data/backups"
  mkdir -p "$APP_DIR/data/backups"
  echo "Backups удалены."
}

purge_cmd() {
  need_root
  confirm="$(read_required "Введите DELETE для полного удаления панели и данных")"
  [ "$confirm" = "DELETE" ] || exit 1
  if [ -d "$APP_DIR" ]; then
    cd "$APP_DIR"
    docker compose --profile caddy down -v || true
  fi
  rm -rf "$APP_DIR"
  rm -f /usr/local/bin/olcrtc-panel
  echo "olcrtc-panel полностью удалён."
}

config_cmd() {
  need_root
  if [ ! -f "$ENV_FILE" ]; then
    echo "Нет $ENV_FILE. Сначала запусти install."
    exit 1
  fi
  local old_token domain path token
  old_token="$(grep '^PANEL_ADMIN_TOKEN=' "$ENV_FILE" | cut -d= -f2-)"
  domain="$(read_default "Новый домен, пусто для http://IP:8080" "$(grep '^PANEL_DOMAIN=' "$ENV_FILE" | cut -d= -f2-)")"
  path="$(normalize_path "$(read_default "Секретный путь панели" "$(grep '^PANEL_PATH=' "$ENV_FILE" | cut -d= -f2-)")")"
  path="$(ensure_secret_path "$path")"
  rotate="$(read_default "Сгенерировать новый admin token?" "N")"
  case "$rotate" in
    y|Y) token="$(rand_token)" ;;
    *) token="$old_token" ;;
  esac
  write_caddyfile "$domain" "$path"
  write_env "$domain" "$path" "$token"
  compose_up
  echo "Конфигурация применена."
  echo "URL: $(grep '^PANEL_PUBLIC_BASE_URL=' "$ENV_FILE" | cut -d= -f2-)"
  echo "Admin token: $token"
}

delete_menu() {
  echo "Удаление:"
  echo "  1) Остановить контейнеры, данные оставить"
  echo "  2) Удалить runtime/volumes, базу оставить"
  echo "  3) Удалить локальные backups"
  echo "  4) Полное удаление"
  echo "  0) Назад"
  choice="$(read_required "Выбор")"
  case "$choice" in
    1) uninstall_cmd ;;
    2) delete_runtime_cmd ;;
    3) delete_backups_cmd ;;
    4) purge_cmd ;;
  esac
}

menu_cmd() {
  while true; do
    echo
    echo "olcrtc-panel $PANEL_VERSION"
    echo "  1) Статус"
    echo "  2) Логи"
    echo "  3) Обновить"
    echo "  4) Перезапустить"
    echo "  5) Домен, путь, admin token"
    echo "  6) Backup"
    echo "  7) Удаление"
    echo "  0) Выход"
    choice="$(read_required "Выбор")"
    case "$choice" in
      1) status_cmd ;;
      2) logs_cmd ;;
      3) update_cmd ;;
      4) restart_cmd ;;
      5) config_cmd ;;
      6) backup_cmd ;;
      7) delete_menu ;;
      0) exit 0 ;;
    esac
  done
}

cmd="${1:-menu}"
case "$cmd" in
  install) install_cmd ;;
  update) update_cmd ;;
  status) status_cmd ;;
  logs) logs_cmd ;;
  restart) restart_cmd ;;
  backup) backup_cmd ;;
  config) config_cmd ;;
  uninstall) uninstall_cmd ;;
  delete-runtime) delete_runtime_cmd ;;
  delete-backups) delete_backups_cmd ;;
  purge) purge_cmd ;;
  menu) menu_cmd ;;
  *)
    echo "Использование: $0 install|menu|update|status|logs|restart|backup|config|uninstall|delete-runtime|delete-backups|purge"
    exit 1
    ;;
esac
