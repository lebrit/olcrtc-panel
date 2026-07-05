# olcrtc-panel

Веб-панель для разворачивания и управления `olcrtc` на сервере.

Панель создаёт пользователей, генерирует Jitsi/WBStream профили, запускает серверные `olcrtc srv` инстансы, выдаёт `olcrtc://` URI и subscription-ссылки для клиентов.

## Установка одной командой

```bash
curl -fsSL https://raw.githubusercontent.com/lebrit/olcrtc-panel/main/scripts/install.sh | sudo bash -s -- install
```

После установки меню доступно командой:

```bash
olcrtc-panel menu
```

## Что входит

- FastAPI backend, SQLite state, React/Vite frontend.
- Docker Compose deployment.
- Caddy для HTTPS и скрытого пути панели.
- Интерактивный installer/menu на русском.
- Автогенерация Jitsi room URL.
- Проверка доступных Jitsi серверов из сети сервера.
- WBStream profile wizard с экспериментальным автосозданием room.
- Отдельные пользователи, профили, URI и subscription token.
- Start/stop/rotate key/logs из панели.

## Jitsi

По умолчанию используется `jitsi + datachannel`.

Панель умеет проверять список серверов:

- `https://meet.handyweb.org`
- `https://meet.small-dm.ru`
- `https://meet1.arbitr.ru`
- `https://meet.jit.si`

Для Jitsi обычно не нужна регистрация. Если конкретный сервер отвечает `401/403` или закрывает комнаты политикой инстанса, панель помечает его как требующий ручной проверки.

## WBStream

По умолчанию используется `wbstream + vp8channel`.

Автосоздание WBStream room в этом релизе экспериментальное: панель пробует известные API endpoint-кандидаты и показывает диагностический ответ, если WBStream требует аккаунтный token или изменил API. Если room уже известен, его можно вставить в поле `Room ID`.

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

Текущая версия: `0.1.0`.

Каждое изменение, которое доходит до сборки, должно обновлять:

- `VERSION`
- `frontend/package.json`
- `scripts/install.sh`
- Git tag/release
- этот README при изменении поведения

## Что улучшить дальше

- Довести WBStream room auto-create до стабильного API-контракта после проверки на живом аккаунте.
- Добавить managed `olcrtc` server через `pkg/olcrtc/tunnel` и `AuthHook`, чтобы отключать пользователей без отдельного room/key на профиль.
- Добавить real-provider e2e проверки Jitsi/WBStream прямо из панели.
- Добавить traffic accounting по профилям без парсинга логов.
- Добавить импорт существующих `olcrtc://` и `sub.md` в панель.
