# Разработка

## Требования

- Python 3.12+;
- Git;
- Docker — опционально;
- отдельный тестовый vault.

## Локальный запуск

```bash
cp .env.example .env
make check
make doctor
make demo
```

`.env.example` указывает на `tests/fixtures/vault` и включает `dry-run`. Не заменяйте путь на реальное хранилище, пока проверяете изменения кода.

## Команды CLI

Диагностика:

```bash
PYTHONPATH=src python3 -m obsidian_assistant --env-file .env doctor
```

Предварительный просмотр capture:

```bash
PYTHONPATH=src python3 -m obsidian_assistant --env-file .env capture \
  --title "Новая идея" \
  --text "Проверить сценарий"
```

Явное применение к тестовому vault:

```bash
PYTHONPATH=src python3 -m obsidian_assistant --env-file .env capture \
  --title "Новая идея" \
  --text "Проверить сценарий" \
  --apply
```

## Контейнер

Контейнер запускается без сети, capabilities и права записи в корневую файловую систему. Vault остаётся единственным записываемым bind mount.

```bash
cp .env.example .env
docker compose run --rm vault-worker doctor
docker compose run --rm vault-worker capture --title "Container test" --text "Safe test"
```

Без `.env` контейнер безопасно подключает `tests/fixtures/vault`. Для записи добавьте `--apply`. Перед настройкой другого пути убедитесь, что `HOST_VAULT_PATH` указывает на тестовую папку; рабочий vault подключайте только по процедуре развёртывания.

## Тесты

```bash
make test
```

Основные обязательные классы тестов:

- конфигурация по умолчанию безопасна;
- путь не выходит за корень vault;
- запись возможна только в allowlist;
- существующий файл не перезаписывается;
- dry-run не изменяет диск;
- frontmatter и Markdown имеют ожидаемый формат.

## Добавление новой операции записи

1. Описать пользовательский сценарий.
2. Проверить, нужен ли ADR.
3. Добавить типизированную модель запроса.
4. Провести путь через общую политику vault.
5. Реализовать dry-run и понятный результат.
6. Добавить негативные тесты безопасности.
7. Обновить документацию, roadmap и changelog.
