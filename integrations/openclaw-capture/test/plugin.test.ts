import assert from "node:assert/strict";
import { chmod, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, it } from "node:test";

import {
  invokeBridge,
  parsePluginConfig,
  validateBridgeResponse,
} from "../bridge-process.js";
import { parseCaptureArguments, registerCapturePlugin, renderResponse } from "../plugin.js";
import type {
  CaptureBridgeResponse,
  PluginApi,
  PluginCommandDefinition,
} from "../types.js";

const REQUEST_ID = "12345678-1234-4678-9234-567812345678";

function config() {
  return {
    bridgeCommand: "/opt/obsidian-assistant/bin/obsidian-assistant",
    workerEnvFile: "/opt/obsidian-assistant/worker.env",
    ownerIds: ["123456789"],
    processImmediately: true,
    timeoutMs: 15000,
  };
}

describe("plugin config", () => {
  it("fails closed on relative commands and unknown fields", () => {
    assert.throws(
      () => parsePluginConfig({ ...config(), bridgeCommand: "obsidian-assistant" }),
      /absolute path/,
    );
    assert.throws(() => parsePluginConfig({ ...config(), extra: true }), /unsupported/);
  });
});

describe("capture parsing", () => {
  it("uses the first line as a title only when a body follows", () => {
    assert.deepEqual(parseCaptureArguments("Заголовок\nТекст заметки"), {
      title: "Заголовок",
      text: "Текст заметки",
    });
    assert.deepEqual(parseCaptureArguments("Одна строка"), {
      title: "Telegram capture",
      text: "Одна строка",
    });
    assert.equal(parseCaptureArguments("   "), null);
  });
});

describe("owner boundary", () => {
  it("invokes the bridge only for the configured authorized Telegram sender", async () => {
    let command: PluginCommandDefinition | undefined;
    let invocations = 0;
    const api: PluginApi = {
      pluginConfig: config(),
      logger: { error() {} },
      registerCommand(definition) {
        command = definition;
      },
    };
    registerCapturePlugin(api, {
      randomUuid: () => REQUEST_ID,
      now: () => new Date("2026-07-16T09:30:00.000Z"),
      async invokeBridge(_config, envelope) {
        invocations += 1;
        assert.equal(envelope.event.actor_id, "telegram:123456789");
        assert.equal(envelope.event.payload.text, "Fixture text");
        return completedResponse();
      },
    });
    assert.ok(command);

    const denied = await command.handler({
      channel: "telegram",
      channelId: "telegram",
      senderId: "999999999",
      isAuthorizedSender: true,
      args: "Fixture text",
    });
    assert.equal(denied.isError, true);
    assert.equal(invocations, 0);

    const accepted = await command.handler({
      channel: "telegram",
      channelId: "telegram",
      senderId: "123456789",
      isAuthorizedSender: true,
      senderIsOwner: true,
      args: "Fixture text",
    });
    assert.match(accepted.text ?? "", /Сохранено/);
    assert.equal(invocations, 1);
  });
});

describe("bridge response validation", () => {
  it("rejects a mismatched request ID and unsafe path", () => {
    assert.throws(
      () => validateBridgeResponse(completedResponse(), "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
      /request ID/,
    );
    assert.throws(
      () => validateBridgeResponse({ ...completedResponse(), note_path: "../escape.md" }, REQUEST_ID),
      /unsafe note path/,
    );
  });

  it("renders metadata without note content", () => {
    const result = renderResponse(completedResponse());
    assert.match(result.text ?? "", /00 Inbox\/fixture\.md/);
    assert.doesNotMatch(result.text ?? "", /private fixture body/);
  });
});

describe("bridge process boundary", () => {
  it("uses fixed arguments and does not inherit the Telegram token", async () => {
    const directory = await mkdtemp(join(tmpdir(), "obsidian-capture-plugin-"));
    const executable = join(directory, "fixture-bridge");
    const workerEnv = join(directory, "worker.env");
    const previousToken = process.env.TELEGRAM_BOT_TOKEN;
    try {
      await writeFile(workerEnv, "OBSIDIAN_DRY_RUN=true\n", { mode: 0o600 });
      await writeFile(
        executable,
        [
          "#!/bin/sh",
          '[ "$1" = "--env-file" ] || exit 2',
          `[ "$2" = "${workerEnv}" ] || exit 3`,
          '[ "$3" = "bridge" ] || exit 4',
          '[ -z "${TELEGRAM_BOT_TOKEN+x}" ] || exit 5',
          "cat >/dev/null",
          `printf '%s\\n' '${JSON.stringify(completedResponse())}'`,
        ].join("\n"),
        { mode: 0o700 },
      );
      await chmod(executable, 0o700);
      process.env.TELEGRAM_BOT_TOKEN = "fixture-parent-token";

      const response = await invokeBridge(
        {
          ...config(),
          bridgeCommand: executable,
          workerEnvFile: workerEnv,
        },
        {
          bridge_schema_version: 1,
          process_now: true,
          event: {
            schema_version: 1,
            request_id: REQUEST_ID,
            event_type: "capture.text",
            source: "telegram",
            actor_id: "telegram:123456789",
            created_at: "2026-07-16T09:30:00.000Z",
            payload: { title: "Fixture", text: "Private fixture body" },
          },
        },
      );

      assert.equal(response.queue_state, "completed");
    } finally {
      if (previousToken === undefined) {
        delete process.env.TELEGRAM_BOT_TOKEN;
      } else {
        process.env.TELEGRAM_BOT_TOKEN = previousToken;
      }
      await rm(directory, { recursive: true, force: true });
    }
  });
});

function completedResponse(): CaptureBridgeResponse {
  return {
    bridge_schema_version: 1,
    request_id: REQUEST_ID,
    accepted: "created",
    queue_state: "completed",
    processing_state: "completed",
    note_path: "00 Inbox/fixture.md",
  };
}
