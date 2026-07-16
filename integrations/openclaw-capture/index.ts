import {
  definePluginEntry,
  type OpenClawPluginDefinition,
} from "openclaw/plugin-sdk/plugin-entry";

import { registerCapturePlugin } from "./plugin.js";
import type { PluginApi } from "./types.js";

const plugin: OpenClawPluginDefinition = definePluginEntry({
  id: "obsidian-assistant-capture",
  name: "Obsidian Assistant Capture",
  description: "Owner-only Telegram capture command backed by a local queue.",
  register(api: PluginApi) {
    registerCapturePlugin(api);
  },
});

export default plugin;
