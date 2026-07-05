#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="olcrtc-panel"
APP_DIR="/opt/olcrtc-panel"
REPO_URL="${OLCRTC_PANEL_REPO:-https://github.com/lebrit/olcrtc-panel.git}"
PANEL_VERSION="0.1.12"
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
  local value
  value="$(read_prompt "$prompt" "")"
  if [ -z "$value" ] && ! { [ -r /dev/tty ] && [ -w /dev/tty ]; } && ! [ -t 0 ]; then
    echo "Нет интерактивного терминала для ввода: $prompt" >&2
    return 1
  fi
  echo "$value"
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
    local stamp backup_dir status_file patch_file
    stamp="$(date +%Y%m%d-%H%M%S)"
    backup_dir="$APP_DIR/data/backups"
    status_file="$backup_dir/git-local-changes-$stamp.status"
    patch_file="$backup_dir/git-local-changes-$stamp.patch"

    git -C "$APP_DIR" fetch --all --tags
    if ! git -C "$APP_DIR" diff --quiet --ignore-submodules -- || ! git -C "$APP_DIR" diff --cached --quiet --ignore-submodules --; then
      mkdir -p "$backup_dir"
      git -C "$APP_DIR" status --short > "$status_file" || true
      {
        git -C "$APP_DIR" diff --binary -- || true
        git -C "$APP_DIR" diff --cached --binary -- || true
      } > "$patch_file"
      echo "Найдены локальные изменения в managed checkout. Patch сохранён: $patch_file"
    fi
    git -C "$APP_DIR" checkout -f -B main origin/main
    git -C "$APP_DIR" reset --hard origin/main
  else
    rm -rf "$APP_DIR"
    git clone "$REPO_URL" "$APP_DIR"
  fi
}

install_cli_wrapper() {
  local wrapper tmp
  wrapper="/usr/local/bin/olcrtc-panel"
  tmp="$(mktemp "${wrapper}.tmp.XXXXXX")"
  cat > "$tmp" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
if [ "\$#" -eq 0 ]; then
  set -- menu
fi
exec bash "$APP_DIR/scripts/install.sh" "\$@"
EOF
  chmod 0755 "$tmp"
  rm -f "$wrapper"
  mv -f "$tmp" "$wrapper"
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
  local public_url bind dns jitsi_ref olcrtc_ref
  if [ -n "$domain" ]; then
    public_url="https://${domain}${path}"
  else
    public_url="http://$(server_ip):8080${path}"
  fi
  bind="127.0.0.1"
  dns="${OLCRTC_DEFAULT_DNS:-8.8.8.8:53}"
  jitsi_ref="${OLCRTC_DEFAULT_JITSI:-https://fairmeeting.net}"
  olcrtc_ref="${OLCRTC_REF:-master}"
  cat > "$ENV_FILE" <<EOF
PANEL_VERSION=$PANEL_VERSION
PANEL_ADMIN_TOKEN=$token
PANEL_DOMAIN=$domain
PANEL_PATH=$path
PANEL_PUBLIC_BASE_URL=$public_url
PANEL_BIND=$bind
PANEL_PORT=18080
OLCRTC_DEFAULT_DNS=$dns
OLCRTC_DEFAULT_JITSI=$jitsi_ref
OLCRTC_REF=$olcrtc_ref
EOF
  chmod 600 "$ENV_FILE"
}

load_env() {
  if [ ! -f "$ENV_FILE" ]; then
    echo "Нет $ENV_FILE. Сначала запусти install."
    exit 1
  fi
  local line key value
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ""|\#*) continue ;;
    esac
    key="${line%%=*}"
    value="${line#*=}"
    case "$key" in
      PANEL_ADMIN_TOKEN|PANEL_DOMAIN|PANEL_PATH|PANEL_PUBLIC_BASE_URL|PANEL_BIND|PANEL_PORT|OLCRTC_DEFAULT_DNS|OLCRTC_DEFAULT_JITSI|OLCRTC_REF)
        printf -v "$key" '%s' "$value"
        export "$key"
        ;;
    esac
  done < "$ENV_FILE"
}

repair_config() {
  load_env
  local token domain path stamp
  token="${PANEL_ADMIN_TOKEN:-$(rand_token)}"
  domain="${PANEL_DOMAIN:-}"
  path="$(normalize_path "${PANEL_PATH:-}")"
  path="$(ensure_secret_path "$path")"
  stamp="$(date +%Y%m%d-%H%M%S)"
  cp "$ENV_FILE" "$ENV_FILE.bak-$stamp" 2>/dev/null || true
  [ -f "$APP_DIR/Caddyfile" ] && cp "$APP_DIR/Caddyfile" "$APP_DIR/Caddyfile.bak-$stamp" 2>/dev/null || true
  write_caddyfile "$domain" "$path"
  write_env "$domain" "$path" "$token"
}

compose_up() {
  cd "$APP_DIR"
  load_env
  if [ -n "${PANEL_DOMAIN:-}" ]; then
    maybe_stop_web_conflicts
  fi
  if [ "${OLCRTC_PANEL_FORCE_RECREATE:-}" = "1" ]; then
    docker compose --profile caddy up -d --build --force-recreate --remove-orphans
  else
    docker compose --profile caddy up -d --build --remove-orphans
  fi
  docker compose --profile caddy restart caddy >/dev/null 2>&1 || true
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
  echo "Обновляю checkout..."
  clone_or_update_repo
  install_cli_wrapper
  update_apply_cmd
}

update_apply_cmd() {
  need_root
  echo "Применяю конфигурацию..."
  repair_config
  echo "Собираю и запускаю контейнеры..."
  compose_up
  echo "Обновлено до версии $PANEL_VERSION."
  info_cmd
}

rescue_up_cmd() {
  need_root
  echo "Аварийное восстановление: обновляю код, wrapper и принудительно пересоздаю контейнеры..."
  ensure_deps
  OLCRTC_PANEL_FORCE_RECREATE=1 update_cmd
}

status_cmd() {
  cd "$APP_DIR"
  compose_ps_safe
}

info_cmd() {
  load_env
  echo "URL: $(grep '^PANEL_PUBLIC_BASE_URL=' "$ENV_FILE" | cut -d= -f2-)"
  echo "Путь: $(grep '^PANEL_PATH=' "$ENV_FILE" | cut -d= -f2-)"
  echo "Admin token: $(grep '^PANEL_ADMIN_TOKEN=' "$ENV_FILE" | cut -d= -f2-)"
  echo
  cd "$APP_DIR"
  compose_ps_safe
}

compose_ps_safe() {
  if has_cmd timeout; then
    timeout 8 docker compose ps || echo "docker compose ps не ответил за 8 секунд. Попробуй: docker compose logs --tail=80"
  else
    docker compose ps || true
  fi
}

doctor_cmd() {
  echo "olcrtc-panel doctor"
  echo "Wrapper: $(command -v olcrtc-panel || true)"
  echo "APP_DIR: $APP_DIR"
  echo "Installer version: $PANEL_VERSION"
  if [ -f "$APP_DIR/VERSION" ]; then
    echo "Checkout VERSION: $(cat "$APP_DIR/VERSION")"
  fi
  if [ -f "$ENV_FILE" ]; then
    echo "ENV version: $(grep '^PANEL_VERSION=' "$ENV_FILE" | cut -d= -f2-)"
    echo "URL: $(grep '^PANEL_PUBLIC_BASE_URL=' "$ENV_FILE" | cut -d= -f2-)"
    echo "Path: $(grep '^PANEL_PATH=' "$ENV_FILE" | cut -d= -f2-)"
  else
    echo "ENV: missing $ENV_FILE"
  fi
  if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" status --short --branch || true
    git -C "$APP_DIR" log -1 --oneline || true
  fi
  if has_cmd docker; then
    docker --version || true
  else
    echo "Docker: missing"
  fi
  if has_cmd timeout; then
    timeout 8 docker compose version || echo "docker compose version timeout/fail"
  else
    docker compose version || true
  fi
  if [ -d "$APP_DIR" ]; then
    cd "$APP_DIR"
    compose_ps_safe
  fi
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
  load_env
  local old_token domain path token
  old_token="${PANEL_ADMIN_TOKEN:-$(rand_token)}"
  domain="$(read_default "Новый домен, пусто для http://IP:8080" "${PANEL_DOMAIN:-}")"
  path="$(normalize_path "$(read_default "Секретный путь панели" "${PANEL_PATH:-$(secret_panel_path)}")")"
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
  choice="$(read_required "Выбор")" || return 0
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
    echo "  2) URL и admin token"
    echo "  3) Логи"
    echo "  4) Обновить"
    echo "  5) Перезапустить"
    echo "  6) Домен, путь, admin token"
    echo "  7) Backup"
    echo "  8) Удаление"
    echo "  9) Doctor"
    echo "  10) Rescue up"
    echo "  0) Выход"
    choice="$(read_required "Выбор")" || {
      echo "Меню требует интерактивный терминал. Для диагностики используй: olcrtc-panel info или olcrtc-panel doctor."
      exit 0
    }
    case "$choice" in
      1) status_cmd ;;
      2) info_cmd ;;
      3) logs_cmd ;;
      4) update_cmd ;;
      5) restart_cmd ;;
      6) config_cmd ;;
      7) backup_cmd ;;
      8) delete_menu ;;
      9) doctor_cmd ;;
      10) rescue_up_cmd ;;
      0) exit 0 ;;
    esac
  done
}

cmd="${1:-menu}"
case "$cmd" in
  install) install_cmd ;;
  update) update_cmd ;;
  update-apply) update_apply_cmd ;;
  rescue-up) rescue_up_cmd ;;
  status) status_cmd ;;
  info) info_cmd ;;
  doctor) doctor_cmd ;;
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
    echo "Использование: $0 install|menu|update|update-apply|rescue-up|status|info|doctor|logs|restart|backup|config|uninstall|delete-runtime|delete-backups|purge"
    exit 1
    ;;
esac
