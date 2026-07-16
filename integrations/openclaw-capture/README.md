# OpenClaw Capture plugin

Плагин регистрирует `/capture` как нативную команду OpenClaw. Команда не запускает LLM: она проверяет авторизованного Telegram-отправителя, передаёт строгий JSON в локальный `obsidian-assistant bridge` через stdin и возвращает только metadata-статус.

Граница безопасности:

- разрешён только канал Telegram;
- sender ID должен одновременно пройти OpenClaw allowlist и локальный `ownerIds`;
- запускается один абсолютный `bridgeCommand` без shell;
- текст заметки не попадает в argv, Telegram-ответ или журнал плагина;
- дочерний процесс не наследует Telegram/LLM-секреты OpenClaw;
- режим записи определяется отдельным worker env-файлом, а не сообщением пользователя.

Сборка и тесты:

```bash
npm ci
npm run check
```

Для реального Gateway требуется OpenClaw `2026.7.1` и поддерживаемая им версия Node.js. Установка и конфигурация описаны в `docs/openclaw-telegram.md` в корне репозитория.
