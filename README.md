# olcrtc-panel

Веб-панель для разворачивания и управления `olcrtc` на сервере.

Панель создаёт пользователей, генерирует Jitsi/WBStream профили, запускает серверные `olcrtc srv` инстансы, выдаёт `olcrtc://` URI и subscription-ссылки для клиентов.

## Установка одной командой

```bash
curl -fsSL https://raw.githubusercontent.com/lebrit/olcrtc-panel/main/scripts/install.sh | sudo bash -s -- install
```

Установщик сразу спросит домен и секретный путь панели. Если путь оставить пустым, будет создан случайный путь вида `/p-...`; `/panel` по умолчанию больше не используется.

После установки меню доступно командой:

```bash
olcrtc-panel
```

## Что входит

- FastAPI backend, SQLite state, React/Vite frontend.
- Docker Compose deployment.
- Caddy для HTTPS и секретного пути панели.
- Интерактивный installer/menu на русском.
- Автогенерация Jitsi room URL.
- Проверка доступных Jitsi серверов из сети сервера.
- WBStream profile wizard с account token / готовым Room ID.
- Пользователи с вложенными профилями, URI, room copy и subscription token.
- Start/stop/rotate key/logs из панели.

## Jitsi

По умолчанию используется `jitsi + datachannel`, стартовый сервер: `https://fairmeeting.net`.

Панель умеет параллельно проверять расширенный список известных серверов и выбирать первый сервер без явных token/JWT требований. В список входят, например:

- `https://fairmeeting.net`
- `https://meet.ffmuc.net`
- `https://meet.in-berlin.de`
- `https://meet.systemli.org`
- `https://meet.opensuse.org`
- `https://jitsi.debian.social`
- `https://jitsi.hamburg.ccc.de`
- `https://freejitsi01.netcup.net`
- `https://meet.jit.si`

Если сервер отдаёт `401/403` или в `config.js` видны token/JWT настройки, панель помечает его как неподходящий для anonymous `olcrtc` и не выбирает автоматически. Это закрывает кейс `meet.jit.si`, где XMPP может вернуть `token required`.

## WBStream

По умолчанию используется `wbstream + vp8channel`.

Автосоздание WBStream room обновлено под текущий API payload `roomInfo`. Guest-token сейчас получает ответ `Guests are not allowed to create room`, поэтому для автосоздания нужен account token с правом создания room. Если room уже известен, его можно вставить в поле `Room ID` и отключить автосоздание.

`wbstream + datachannel` не включается по умолчанию, потому что guest-flow обычно не имеет `canPublishData=true`.

## Данные

На сервере:

- `/opt/olcrtc-panel/.env` - домен, путь, admin token.
- `/opt/olcrtc-panel/data/panel.db` - пользователи и профили.
- `/opt/olcrtc-panel/data/logs` - логи профилей.
- `/opt/olcrtc-panel/data/backups` - локальные backups.

## Обслуживание

```bash
olcrtc-panel status
olcrtc-panel logs
olcrtc-panel update
olcrtc-panel backup
olcrtc-panel config
```

Удаление разделено по scope:

```bash
olcrtc-panel uninstall        # остановить контейнеры, данные оставить
olcrtc-panel delete-runtime   # удалить runtime/volumes, базу оставить
olcrtc-panel delete-backups   # удалить локальные backups
olcrtc-panel purge            # полное удаление
```

## Разработка

Backend:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements-dev.txt
PYTHONPATH=backend pytest backend/tests
python -m compileall -q backend/olcrtc_panel
```

Frontend:

```bash
cd frontend
npm ci
npm run lint
npm run build
```

Installer:

```bash
bash -n scripts/install.sh
```

## Версии

Текущая версия: `0.1.4`.

Каждое изменение, которое доходит до сборки, должно обновлять:

- `VERSION`
- `frontend/package.json`
- `scripts/install.sh`
- Git tag/release
- этот README при изменении поведения

## Что улучшить дальше

- Проверить WBStream room auto-create на живом account token и зафиксировать права/поля API.
- Добавить managed `olcrtc` server через `pkg/olcrtc/tunnel` и `AuthHook`, чтобы отключать пользователей без отдельного room/key на профиль.
- Добавить real-provider e2e проверки Jitsi/WBStream прямо из панели.
- Добавить traffic accounting по профилям без парсинга логов.
- Добавить импорт существующих `olcrtc://` и `sub.md` в панель.
- Добавить кэширование Docker build слоёв в CI, чтобы релизы собирались быстрее.
- Добавить matrix smoke-test установщика на чистых Debian/Ubuntu/Fedora образах.
- Добавить UI-проверку, что текущая панель действительно открыта только через секретный путь.
- Добавить real XMPP join-check для Jitsi, чтобы отсеивать серверы до старта профиля.
