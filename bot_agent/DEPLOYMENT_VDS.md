# Установка Telegram-бота на VDS

Инструкция рассчитана на Ubuntu или Debian и запуск через Docker Compose.
Публичный домен, reverse proxy и открытые входящие порты боту не нужны: он
получает сообщения через Telegram long polling.

## 1. Требования

- VDS с Ubuntu 22.04/24.04 или Debian 12;
- минимум 1 ГБ RAM и 10 ГБ свободного диска;
- SSH-доступ и пользователь с `sudo`;
- исходящие HTTPS-соединения к Telegram, OpenAI, Docker Hub и GitHub;
- токен Telegram-бота, OpenAI API key и числовой Telegram ID администратора.

Одновременно должен работать только один экземпляр бота с данным Telegram
токеном. Перед запуском на VDS остановите локальный контейнер:

```bash
cd ml_diabetic/bot_agent
docker compose down
```

## 2. Установка Docker и Git

Подключитесь к серверу:

```bash
ssh USER@SERVER_IP
```

Установите пакеты из репозитория операционной системы:

```bash
sudo apt update
sudo apt install -y git docker.io docker-compose-v2
sudo systemctl enable --now docker
```

Проверьте установку:

```bash
sudo docker version
sudo docker compose version
```

Если пакет `docker-compose-v2` недоступен в вашей версии ОС, установите Docker
Engine по официальной инструкции: <https://docs.docker.com/engine/install/>.

## 3. Загрузка проекта

```bash
cd /opt
sudo git clone https://github.com/konstantin-gm/ml_diabetic.git
sudo chown -R "$USER":"$USER" /opt/ml_diabetic
cd /opt/ml_diabetic/bot_agent
```

Для закрытого репозитория используйте SSH URL или GitHub access token вместо
публичного HTTPS URL.

## 4. Настройка переменных окружения

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

Минимально заполните:

```env
TELEGRAM_BOT_TOKEN=123456789:telegram_token_from_botfather
OPENAI_API_KEY=sk-your-openai-api-key
OPENAI_MODEL=gpt-5.4-mini
TELEGRAM_ADMIN_IDS=123456789
JOURNAL_TIMEZONE=Europe/Moscow
JOURNAL_XE_CARBS_GRAMS=12
ADMIN_API_TOKEN=replace_with_a_random_token
ADMIN_API_PORT=8000
LOG_LEVEL=INFO
```

Создайте стойкий токен API командой `openssl rand -hex 32` и вставьте результат
в `ADMIN_API_TOKEN`. Этот же токен понадобится в настройках локального клиента.

`TELEGRAM_ADMIN_IDS` содержит числовые Telegram ID администраторов через
запятую. Это не username. `DATABASE_URL` из `.env.example` можно оставить без
изменений: Docker Compose передаёт контейнеру корректный адрес PostgreSQL.

Не публикуйте `.env` и не добавляйте его в Git.

## 5. Первый запуск

```bash
sudo docker compose up -d --build
sudo docker compose ps
sudo docker compose logs --tail=100 bot
sudo docker compose logs --tail=100 api
```

Контейнер бота автоматически применяет миграции Alembic перед запуском. В
логах успешного запуска должны появиться строки `Start polling` и
`Run polling for bot`.

Откройте бота в Telegram и отправьте `/start`. Пользователи вне белого списка
увидят свой Telegram ID. Администратор может добавить их командами:

```text
/add_user 987654321 Иван
/users
```

## 6. Обновление

```bash
cd /opt/ml_diabetic
git pull --ff-only
cd bot_agent
sudo docker compose up -d --build
sudo docker compose logs --tail=100 bot
```

Данные PostgreSQL сохраняются в Docker volume и не удаляются при пересборке
контейнера.

## 7. Управление

Показать состояние:

```bash
sudo docker compose ps
```

Следить за логами:

```bash
sudo docker compose logs -f bot
```

Перезапустить:

```bash
sudo docker compose restart bot
```

Остановить:

```bash
sudo docker compose down
```

Не используйте `docker compose down -v`, если не хотите удалить базу данных.

## 8. Резервное копирование PostgreSQL

Создать дамп:

```bash
cd /opt/ml_diabetic/bot_agent
mkdir -p backups
sudo docker compose exec -T db \
  pg_dump -U diabet -d diabet --format=custom > "backups/diabet-$(date +%F-%H%M).dump"
```

Скопируйте дамп с VDS на другой сервер или локальный компьютер. Файл на том же
VDS не защищает от потери самого сервера.

Восстановление заменяет данные, поэтому перед ним остановите бот и сохраните
актуальный дамп:

```bash
sudo docker compose stop bot
sudo docker compose exec -T db dropdb -U diabet --if-exists diabet
sudo docker compose exec -T db createdb -U diabet diabet
sudo docker compose exec -T db pg_restore -U diabet -d diabet --clean --if-exists \
  < backups/diabet-YYYY-MM-DD-HHMM.dump
sudo docker compose start bot
```

## 9. Диагностика

Если бот не запускается:

```bash
sudo docker compose ps -a
sudo docker compose logs --tail=200 bot
sudo docker compose logs --tail=100 db
```

Частые причины:

- неверный `TELEGRAM_BOT_TOKEN` или `OPENAI_API_KEY`;
- пустой или неверный `TELEGRAM_ADMIN_IDS`;
- тот же Telegram-бот уже запущен на другом компьютере;
- VDS блокирует исходящие HTTPS-соединения;
- закончилось место на диске: проверьте командой `df -h`.

PostgreSQL опубликован только на `127.0.0.1`, поэтому открывать порт 5434 в
firewall не требуется.

## 10. Доступ к базе через API и Qt5-клиент

API запускается контейнером `api` и публикуется только на loopback-интерфейсе
VDS: `127.0.0.1:8000`. Не меняйте привязку на `0.0.0.0`: токен защищает API,
но шифрование соединения обеспечивает SSH-туннель.

Проверьте API на VDS, не выводя токен в историю команд:

```bash
cd /opt/ml_diabetic/bot_agent
sudo docker compose ps
sudo docker compose logs --tail=100 api
```

На локальном компьютере установите клиент из каталога проекта:

```bash
cd ml_diabetic/bot_agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[desktop]'
cp .env.desktop.example .env.desktop
chmod 600 .env.desktop
```

Укажите в `.env.desktop` тот же токен, что и в `.env` на VDS:

```env
API_BASE_URL=http://127.0.0.1:8000
ADMIN_API_TOKEN=the_same_token_as_on_vds
API_TIMEOUT_SECONDS=30
```

Откройте SSH-туннель в отдельном терминале и оставьте его работающим:

```bash
ssh -N -L 8000:127.0.0.1:8000 USER@SERVER_IP
```

Затем запустите клиент:

```bash
cd ml_diabetic/bot_agent
source .venv/bin/activate
diabetes-db-client
```

Кнопки «Добавить» и «Изменить» открывают форму полей выбранной таблицы.
«Удалить» всегда требует подтверждения. Удаление продукта также удаляет его
псевдонимы, а удаление пользователя — весь принадлежащий ему журнал.

Интерактивная документация API доступна через тот же туннель по адресу
`http://127.0.0.1:8000/docs`. Для запросов требуется заголовок
`Authorization: Bearer <ADMIN_API_TOKEN>`.
