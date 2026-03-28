# Deadline Bot

Бот для напоминания о дедлайнах

## Формат дедлайнов

```text
Описание
DD.MM.YYYY HH:MM
```

Можно время не указывать

```text
Описание
DD.MM.YYYY
```

Все времена бот интерпретирует в часовом поясе `UTC+5`.

- Если до дедлайна меньше или равно 7 дней, бот публикует дедлайн в канал.
- Если до дедлайна больше 7 дней, бот спрашивает, публиковать ли первый пост сразу.
- Всегда напоминает о дедлайне за 7 дней и за 24 часа.
- Можно изменить, отменить и удалить дедлайн.
- При изменении уже опубликованного дедлайна пишет в канал сообщение о переносе.

## Кнопки

- `Добавить дедлайн`
- `Список дедлайнов`
- `Архив`
- `Изменить дедлайн`
- `Отменить дедлайн`
- `Удалить дедлайн`
- `Пропустить`

Слэш-команды тоже оставлены как запасной вариант, но основной интерфейс теперь кнопочный.
При изменении дедлайна кнопку `Пропустить` можно нажать отдельно для описания и отдельно для даты.
Все тексты и шаблоны сообщений бота вынесены в отдельный файл [bot_messages.py](C:/Users/Roma/Desktop/projects/deadline%20bot/bot_messages.py).
Для проверки времени есть тестовая команда `/now`.
Для просмотра архива есть кнопка `Архив` и команда `/archive`.
`Отменить дедлайн` публикует сообщение об отмене и скрывает дедлайн из активных.
`Удалить дедлайн` удаляет все сообщения по дедлайну из канала и переносит запись в архив.
Для разовых Telegram-утилит есть скрипт [tools.py](C:/Users/Roma/Desktop/projects/deadline%20bot/tools.py).
Он запускается просто как:
`py tools.py`
После запуска бот показывает отдельную клавиатуру с кнопкой `Debug Input`.
Нажмите её, отправьте сообщение с кастомным emoji и бот вернёт `custom_emoji_id`, `text_html` и entity-данные прямо в чат.

## Настройка

Нужен Python 3.11+.

1. Установите зависимости:

```bash
pip install -r requirements.txt
```

2. Заполните `.env`:

```env
TOKEN=ваш_токен_бота
CHANNEL_ID=@username_канала_или_-100xxxxxxxxxx
WHITELIST_USER_IDS=123456789,987654321
DEADLINES_STORAGE_PATH=/абсолютный/путь/к/deadlines.json
```

`WHITELIST_USER_IDS` - список Telegram user id через запятую.

Как узнать свой user id:

- написать любому боту вида "user id bot";
- или посмотреть через Telegram API tools.

3. Запустите бота:

```bash
python app.py
```

## Хранение данных

По умолчанию бот хранит дедлайны в локальном файле `deadlines.json` рядом с `app.py`.
Если задана переменная `DEADLINES_STORAGE_PATH`, бот использует указанный абсолютный путь. Для сервера это предпочтительный вариант, чтобы данные жили отдельно от кода.

Для каждого дедлайна сохраняются:

- описание;
- дата и время;
- был ли явно указан `00:00`;
- кто создал дедлайн;
- было ли уже опубликовано первое сообщение;
- были ли отправлены напоминания за 7 дней и 24 часа;
- статус дедлайна: активный, отменённый, завершённый, архивный;
- список всех сообщений в канале, связанных с дедлайном.

## Логика публикации

### Создание

1. Пользователь из whitelist вызывает `/new`.
2. Бот просит описание.
3. Бот просит дату и, при желании, время.
4. Если до дедлайна больше недели, бот спрашивает, публиковать ли сообщение сразу.
5. Если до дедлайна неделя или меньше, бот публикует дедлайн без подтверждения.

### Изменение

1. Пользователь вызывает `/edit`.
2. Выбирает id дедлайна.
3. Отправляет новое описание и новую дату.
4. Если раньше по дедлайну уже было хотя бы одно опубликованное сообщение, бот отправляет в канал сообщение о том, что дедлайн изменился.

### Отмена и удаление

1. `Отменить дедлайн` отправляет отдельное сообщение об отмене и убирает дедлайн из активных.
2. Через 3 дня после отмены бот удаляет все сообщения по этому дедлайну из канала.
3. `Удалить дедлайн` сразу удаляет все сообщения по дедлайну из канала и переносит запись в архив.

## Замечания

- Напоминания проверяются раз в минуту.
- Через 3 дня после завершения или отмены дедлайна бот удаляет все связанные сообщения из канала.
- Бот работает через polling, webhook не нужен.

## Деплой на Ubuntu

В репозитории уже есть всё необходимое для деплоя:

- `deploy/install_service.sh` - первичная настройка сервера;
- `deploy/deploy.sh` - раскатка новой версии;
- `deploy/deadline-bot.service` - `systemd`-сервис;
- `.github/workflows/deploy.yml` - auto-deploy через GitHub Actions.

Ниже приведён рекомендуемый сценарий.

### 1. Подготовьте сервер

Подключитесь к серверу под пользователем с `sudo` и убедитесь, что SSH уже настроен.

С локальной машины скопируйте папку `deploy` на сервер:

```bash
scp -r deploy your_user@your_server:/tmp/deadline-bot-deploy
```

На сервере выполните первичную настройку:

```bash
ssh your_user@your_server
sudo bash /tmp/deadline-bot-deploy/install_service.sh
```

Скрипт:

- установит `python3`, `python3-venv` и `rsync`;
- создаст системного пользователя `deadlinebot`;
- подготовит каталоги `/opt/deadline-bot`, `/var/lib/deadline-bot`, `/etc/deadline-bot`;
- установит `systemd` unit;
- добавит ограниченное `sudo`-право на перезапуск сервиса для пользователя `deadlinebot`.

### 2. Заполните env-файл на сервере

Откройте файл `/etc/deadline-bot/deadline-bot.env` и заполните его:

```env
TOKEN=ваш_токен_бота
CHANNEL_ID=@username_канала_или_-100xxxxxxxxxx
WHITELIST_USER_IDS=123456789,987654321
DEADLINES_STORAGE_PATH=/var/lib/deadline-bot/deadlines.json
```

### 3. Подключите SSH-ключ для деплоя

На своей машине создайте отдельную пару ключей:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/deadline-bot-deploy
```

Скопируйте публичный ключ на сервер:

```bash
scp ~/.ssh/deadline-bot-deploy.pub your_user@your_server:/tmp/deadline-bot-deploy.pub
```

На сервере установите его для пользователя `deadlinebot`:

```bash
sudo sh -c 'cat /tmp/deadline-bot-deploy.pub >> /home/deadlinebot/.ssh/authorized_keys'
sudo chown deadlinebot:deadlinebot /home/deadlinebot/.ssh/authorized_keys
sudo chmod 600 /home/deadlinebot/.ssh/authorized_keys
rm /tmp/deadline-bot-deploy.pub
```

### 4. Настройте GitHub Secrets

В репозитории откройте `Settings -> Secrets and variables -> Actions` и добавьте:

- `DEPLOY_HOST` - IP или домен сервера;
- `DEPLOY_PORT` - обычно `22`;
- `DEPLOY_USER` - `deadlinebot`;
- `DEPLOY_SSH_KEY` - содержимое приватного ключа `~/.ssh/deadline-bot-deploy`;
- `DEPLOY_KNOWN_HOSTS` - вывод команды `ssh-keyscan -H your_server`.

Пример получения `known_hosts`:

```bash
ssh-keyscan -H your_server
```

### 5. Запустите первый деплой

После настройки secrets можно:

- либо вручную запустить workflow `Deploy` во вкладке `Actions`;
- либо просто сделать `push` в ветку `main`.

Workflow:

- собирает архив проекта;
- загружает его на сервер по SSH;
- обновляет код в `/opt/deadline-bot/app`;
- устанавливает зависимости в `/opt/deadline-bot/.venv`;
- перезапускает `deadline-bot.service`.

### 6. Проверка на сервере

После первого деплоя проверьте сервис:

```bash
sudo systemctl status deadline-bot
sudo journalctl -u deadline-bot -n 100 --no-pager
```

## Ручной деплой без GitHub Actions

Если нужно раскатить текущую версию вручную, можно использовать тот же `deploy.sh`:

```bash
tar \
  --exclude='.git' \
  --exclude='.github' \
  --exclude='.env' \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='deadlines.json' \
  -czf deadline-bot-release.tar.gz .
scp deadline-bot-release.tar.gz deadlinebot@your_server:/tmp/deadline-bot-release.tar.gz
ssh deadlinebot@your_server "RELEASE_ARCHIVE=/tmp/deadline-bot-release.tar.gz bash -s" < deploy/deploy.sh
```

## Что важно для хранения данных

Продовые дедлайны должны храниться вне репозитория. Рекомендуемый путь уже прописан в примерах:

```env
DEADLINES_STORAGE_PATH=/var/lib/deadline-bot/deadlines.json
```

Тогда auto-deploy обновляет только код, а живые данные остаются на месте.
