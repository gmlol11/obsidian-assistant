import { spawn } from "node:child_process";
import { isAbsolute } from "node:path";

import type { CaptureBridgeEnvelope, CaptureBridgeResponse } from "./types.js";

const MAX_OUTPUT_BYTES = 16_384;
const REQUEST_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const NOTE_PATH_SEGMENT = /^[^/\\.][^/\\]*$/u;
const QUEUE_STATES = new Set(["pending", "processing", "completed", "quarantine"]);
const PROCESSING_STATES = new Set([
  "empty",
  "previewed",
  "completed",
  "retry",
  "quarantined",
]);

export interface CapturePluginConfig {
  bridgeCommand: string;
  workerEnvFile: string;
  ownerIds: readonly string[];
  processImmediately: boolean;
  timeoutMs: number;
}

export function parsePluginConfig(raw: Record<string, unknown> | undefined): CapturePluginConfig {
  if (!raw) {
    throw new Error("Plugin config is required");
  }
  const allowed = new Set([
    "bridgeCommand",
    "workerEnvFile",
    "ownerIds",
    "processImmediately",
    "timeoutMs",
  ]);
  if (Object.keys(raw).some((key) => !allowed.has(key))) {
    throw new Error("Plugin config contains unsupported fields");
  }
  const bridgeCommand = requiredAbsolutePath(raw.bridgeCommand, "bridgeCommand");
  const workerEnvFile = requiredAbsolutePath(raw.workerEnvFile, "workerEnvFile");
  if (!Array.isArray(raw.ownerIds) || raw.ownerIds.length < 1 || raw.ownerIds.length > 4) {
    throw new Error("ownerIds must contain between one and four Telegram IDs");
  }
  const ownerIds = raw.ownerIds.map((value) => {
    if (typeof value !== "string" || !/^[1-9][0-9]{0,19}$/.test(value)) {
      throw new Error("ownerIds must contain numeric Telegram IDs");
    }
    return value;
  });
  if (new Set(ownerIds).size !== ownerIds.length) {
    throw new Error("ownerIds must not contain duplicates");
  }
  const processImmediately = raw.processImmediately ?? true;
  if (typeof processImmediately !== "boolean") {
    throw new Error("processImmediately must be a boolean");
  }
  const timeoutMs = raw.timeoutMs ?? 15_000;
  if (!Number.isInteger(timeoutMs) || Number(timeoutMs) < 1_000 || Number(timeoutMs) > 30_000) {
    throw new Error("timeoutMs must be an integer between 1000 and 30000");
  }
  return {
    bridgeCommand,
    workerEnvFile,
    ownerIds,
    processImmediately,
    timeoutMs: Number(timeoutMs),
  };
}

function requiredAbsolutePath(value: unknown, field: string): string {
  if (typeof value !== "string" || !isAbsolute(value) || value.includes("\0")) {
    throw new Error(`${field} must be an absolute path`);
  }
  return value;
}

export async function invokeBridge(
  config: CapturePluginConfig,
  envelope: CaptureBridgeEnvelope,
): Promise<CaptureBridgeResponse> {
  const encoded = JSON.stringify(envelope);
  return await new Promise<CaptureBridgeResponse>((resolve, reject) => {
    const child = spawn(
      config.bridgeCommand,
      ["--env-file", config.workerEnvFile, "bridge"],
      {
        shell: false,
        stdio: ["pipe", "pipe", "pipe"],
        env: {
          HOME: process.env.HOME,
          LANG: "C.UTF-8",
          LC_ALL: "C.UTF-8",
          PATH: "/usr/bin:/bin:/usr/sbin:/sbin",
        },
      },
    );
    const chunks: Buffer[] = [];
    let outputBytes = 0;
    let settled = false;
    const finish = (callback: () => void) => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      callback();
    };
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      finish(() => reject(new Error("Bridge timed out")));
    }, config.timeoutMs);

    child.stdout.on("data", (chunk: Buffer) => {
      outputBytes += chunk.length;
      if (outputBytes > MAX_OUTPUT_BYTES) {
        child.kill("SIGKILL");
        finish(() => reject(new Error("Bridge output exceeded the safe size limit")));
        return;
      }
      chunks.push(chunk);
    });
    // Drain stderr but never copy it to OpenClaw logs or Telegram replies.
    child.stderr.resume();
    child.on("error", (error) => finish(() => reject(error)));
    child.on("close", (code) => {
      if (code !== 0) {
        finish(() => reject(new Error("Bridge exited unsuccessfully")));
        return;
      }
      finish(() => {
        try {
          const raw: unknown = JSON.parse(Buffer.concat(chunks).toString("utf8"));
          resolve(validateBridgeResponse(raw, envelope.event.request_id));
        } catch (error) {
          reject(error);
        }
      });
    });
    child.stdin.on("error", (error) => finish(() => reject(error)));
    child.stdin.end(encoded);
  });
}

export function validateBridgeResponse(raw: unknown, expectedRequestId: string): CaptureBridgeResponse {
  if (!isRecord(raw)) {
    throw new Error("Bridge response must be an object");
  }
  const keys = new Set([
    "bridge_schema_version",
    "request_id",
    "accepted",
    "queue_state",
    "processing_state",
    "note_path",
  ]);
  if (Object.keys(raw).length !== keys.size || Object.keys(raw).some((key) => !keys.has(key))) {
    throw new Error("Bridge response fields do not match schema version 1");
  }
  if (raw.bridge_schema_version !== 1) {
    throw new Error("Unsupported bridge response version");
  }
  if (
    typeof raw.request_id !== "string" ||
    !REQUEST_ID.test(raw.request_id) ||
    raw.request_id !== expectedRequestId
  ) {
    throw new Error("Bridge response request ID does not match");
  }
  if (raw.accepted !== "created" && raw.accepted !== "duplicate") {
    throw new Error("Bridge response has an invalid acceptance state");
  }
  if (typeof raw.queue_state !== "string" || !QUEUE_STATES.has(raw.queue_state)) {
    throw new Error("Bridge response has an invalid queue state");
  }
  if (
    raw.processing_state !== null &&
    (typeof raw.processing_state !== "string" || !PROCESSING_STATES.has(raw.processing_state))
  ) {
    throw new Error("Bridge response has an invalid processing state");
  }
  if (raw.note_path !== null && !isSafeNotePath(raw.note_path)) {
    throw new Error("Bridge response has an unsafe note path");
  }
  return raw as unknown as CaptureBridgeResponse;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isSafeNotePath(value: unknown): value is string {
  if (typeof value !== "string" || value.length < 1 || value.length > 1024 || value.startsWith("/")) {
    return false;
  }
  const segments = value.split("/");
  return segments.every(
    (segment) => segment !== "." && segment !== ".." && NOTE_PATH_SEGMENT.test(segment),
  );
}
