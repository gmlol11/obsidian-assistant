/**
 * Narrow structural subset of OpenClaw 2026.7.1 used by this plugin.
 * Keeping the plugin core structural makes its security behavior unit-testable
 * without starting a real Gateway or loading user credentials.
 */
export interface PluginCommandContext {
  senderId?: string;
  channel: string;
  channelId?: string;
  isAuthorizedSender: boolean;
  senderIsOwner?: boolean;
  args?: string;
}

export interface PluginCommandResult {
  text?: string;
  isError?: boolean;
}

export interface PluginCommandDefinition {
  name: string;
  description: string;
  channels?: readonly string[];
  acceptsArgs?: boolean;
  requireAuth?: boolean;
  handler: (context: PluginCommandContext) => PluginCommandResult | Promise<PluginCommandResult>;
}

export interface PluginApi {
  pluginConfig?: Record<string, unknown>;
  logger: {
    error(message: string): void;
  };
  registerCommand(definition: PluginCommandDefinition): void;
}

export interface CaptureBridgeEnvelope {
  bridge_schema_version: 1;
  process_now: boolean;
  event: {
    schema_version: 1;
    request_id: string;
    event_type: "capture.text";
    source: "telegram";
    actor_id: string;
    created_at: string;
    payload: {
      title: string;
      text: string;
    };
  };
}

export type QueueState = "pending" | "processing" | "completed" | "quarantine";
export type ProcessingState = "empty" | "previewed" | "completed" | "retry" | "quarantined";

export interface CaptureBridgeResponse {
  bridge_schema_version: 1;
  request_id: string;
  accepted: "created" | "duplicate";
  queue_state: QueueState;
  processing_state: ProcessingState | null;
  note_path: string | null;
}
