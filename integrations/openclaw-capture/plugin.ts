import { randomUUID } from "node:crypto";

import {
  invokeBridge,
  parsePluginConfig,
  type CapturePluginConfig,
} from "./bridge-process.js";
import type {
  CaptureBridgeEnvelope,
  CaptureBridgeResponse,
  PluginApi,
  PluginCommandContext,
  PluginCommandResult,
} from "./types.js";

interface PluginDependencies {
  invokeBridge: (
    config: CapturePluginConfig,
    envelope: CaptureBridgeEnvelope,
  ) => Promise<CaptureBridgeResponse>;
  randomUuid: () => string;
  now: () => Date;
}

const DEFAULT_DEPENDENCIES: PluginDependencies = {
  invokeBridge,
  randomUuid: randomUUID,
  now: () => new Date(),
};

export function registerCapturePlugin(
  api: PluginApi,
  dependencies: PluginDependencies = DEFAULT_DEPENDENCIES,
): void {
  const config = parsePluginConfig(api.pluginConfig);
  const ownerIds = new Set(config.ownerIds);

  api.registerCommand({
    name: "capture",
    description: "Сохранить текст в безопасную очередь Obsidian Assistant",
    channels: ["telegram"],
    acceptsArgs: true,
    requireAuth: true,
    async handler(context): Promise<PluginCommandResult> {
      if (!isAllowedOwner(context, ownerIds)) {
        return { text: "Доступ запрещён.", isError: true };
      }
      const capture = parseCaptureArguments(context.args);
      if (!capture) {
        return {
          text: "Использование: /capture текст или /capture Заголовок\\nТекст",
          isError: true,
        };
      }

      const requestId = dependencies.randomUuid();
      const envelope: CaptureBridgeEnvelope = {
        bridge_schema_version: 1,
        process_now: config.processImmediately,
        event: {
          schema_version: 1,
          request_id: requestId,
          event_type: "capture.text",
          source: "telegram",
          actor_id: `telegram:${context.senderId}`,
          created_at: dependencies.now().toISOString(),
          payload: capture,
        },
      };

      try {
        const response = await dependencies.invokeBridge(config, envelope);
        return renderResponse(response);
      } catch (error) {
        const errorType = error instanceof Error ? error.constructor.name : "UnknownError";
        api.logger.error(
          `Obsidian capture bridge failed request_id=${requestId} error_type=${errorType}`,
        );
        return {
          text: `Не удалось подтвердить приём. ID запроса: ${requestId}`,
          isError: true,
        };
      }
    },
  });
}

function isAllowedOwner(context: PluginCommandContext, ownerIds: ReadonlySet<string>): boolean {
  const telegramChannel = context.channel === "telegram" || context.channelId === "telegram";
  return Boolean(
    telegramChannel &&
      context.isAuthorizedSender &&
      context.senderId &&
      ownerIds.has(context.senderId) &&
      context.senderIsOwner !== false,
  );
}

export function parseCaptureArguments(
  args: string | undefined,
): { title: string; text: string } | null {
  const value = args?.trim();
  if (!value) {
    return null;
  }
  const lines = value.split(/\r?\n/u);
  const proposedTitle = lines[0]?.trim() ?? "";
  const remainingText = lines.slice(1).join("\n").trim();
  if (proposedTitle && remainingText) {
    return {
      title: proposedTitle.length <= 200 ? proposedTitle : `${proposedTitle.slice(0, 199)}…`,
      text: remainingText,
    };
  }
  return { title: "Telegram capture", text: value };
}

export function renderResponse(response: CaptureBridgeResponse): PluginCommandResult {
  const suffix = `ID: ${response.request_id}`;
  if (response.queue_state === "completed") {
    const path = response.note_path ? `\nЗаметка: ${response.note_path}` : "";
    return { text: `✅ Сохранено.${path}\n${suffix}` };
  }
  if (response.queue_state === "quarantine") {
    return {
      text: `⚠️ Принято, но обработка остановлена в карантине.\n${suffix}`,
      isError: true,
    };
  }
  if (response.queue_state === "processing") {
    return { text: `⏳ Принято и обрабатывается.\n${suffix}` };
  }
  if (response.processing_state === "previewed") {
    const path = response.note_path ? `\nБудущая заметка: ${response.note_path}` : "";
    return { text: `🧪 Принято. Worker работает в dry-run.${path}\n${suffix}` };
  }
  if (response.processing_state === "retry") {
    return { text: `📥 Принято. Временная ошибка; worker повторит попытку.\n${suffix}` };
  }
  const duplicate = response.accepted === "duplicate" ? " Повтор уже был в очереди." : "";
  return { text: `📥 Принято в очередь.${duplicate}\n${suffix}` };
}
