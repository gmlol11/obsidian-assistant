# Чек-лист публикации на GitHub

## Предлагаемые метаданные

**Название:** `obsidian-assistant`

**Описание:**

> Local-first assistant for safely capturing, classifying, and organizing Telegram/OpenClaw inputs in an Obsidian vault.

**Topics:** `obsidian`, `local-first`, `automation`, `telegram-bot`, `openclaw`, `personal-knowledge-management`, `python`, `mac-mini`

## До создания публичного репозитория

- [x] выбрана открытая лицензия MIT;
- [ ] проверить весь Git history, а не только текущие файлы, на секреты;
- [ ] убедиться, что нет настоящего `.env`, vault, логов и резервных копий;
- [ ] проверить README, ссылки и Mermaid;
- [ ] выполнить `make check`;
- [ ] собрать контейнер;
- [ ] открыть репозиторий сначала как private;
- [ ] дождаться зелёного GitHub Actions;
- [ ] включить Dependabot и secret scanning;
- [ ] просмотреть репозиторий как анонимный пользователь;
- [ ] только после этого изменить visibility на public.

## Настройки GitHub

- default branch: `main`;
- запрет force-push и удаления `main`;
- pull request перед merge после появления второго участника;
- required status checks: `test` и `container`;
- squash merge по умолчанию;
- автоматическое удаление merged branches;
- private vulnerability reporting;
- Dependabot security updates;
- secret scanning и push protection, если доступны для репозитория.

## Лицензия

Владелец выбрал MIT: простую разрешительную лицензию, позволяющую использовать, изменять и распространять код при сохранении уведомления об авторстве и текста лицензии.

## Первый release

Первый GitHub release создаётся только после рабочего end-to-end capture. До этого используется статус pre-alpha и секция `[Unreleased]`.

Release должен содержать:

- номер версии;
- пользовательский результат;
- ограничения и известные риски;
- инструкции установки и обновления;
- способ отката;
- commit или image digest;
- changelog без внутренних путей и секретов.
