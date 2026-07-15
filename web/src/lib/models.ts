/**
 * Static model catalog per provider.
 *
 * The gateway accepts model strings of the form `<provider>/<id>`. This
 * catalog is what the UI shows in pickers — a curated set that's known
 * to work end-to-end through LiteLLM. New models can be typed in by
 * hand (the picker has a "custom..." escape hatch); the catalog is
 * about discoverability, not a hard allow-list.
 *
 * The `value` field is what gets sent to the api. For the playground's
 * legacy bare-name path (e.g. `gpt-4o`, `claude-sonnet-4`) the gateway
 * accepts both forms and `_resolve_provider` derives the provider from
 * the prefix.
 */

export type Provider =
  | "anthropic"
  | "openai"
  | "gemini"
  | "mistral"
  | "deepseek"
  | "groq"
  | "stub";

export interface ModelOption {
  /** What gets dispatched. `<provider>/<model_id>`. */
  value: string;
  /** What the user sees. Short. */
  label: string;
  /** One-line tier hint. ≤32 chars. */
  hint?: string;
}

export const PROVIDERS: { value: Provider; label: string }[] = [
  { value: "anthropic", label: "Anthropic" },
  { value: "openai", label: "OpenAI" },
  { value: "gemini", label: "Gemini" },
  { value: "mistral", label: "Mistral" },
  { value: "deepseek", label: "DeepSeek" },
  { value: "groq", label: "Groq" },
  { value: "stub", label: "Stub (no key)" },
];

/**
 * Curated model catalog. Keep this list short — 4-6 per provider, the
 * ones a researcher actually picks between. New releases get added here
 * by hand (~quarterly). The "custom..." escape hatch in the picker
 * covers anything not on the list.
 */
export const MODEL_CATALOG: Record<Provider, ModelOption[]> = {
  anthropic: [
    { value: "anthropic/claude-opus-4-8", label: "claude-opus-4-8", hint: "frontier reasoning" },
    { value: "anthropic/claude-sonnet-5", label: "claude-sonnet-5", hint: "balanced" },
    { value: "anthropic/claude-haiku-4-5-20251001", label: "claude-haiku-4-5", hint: "fast / cheap" },
  ],
  openai: [
    { value: "openai/gpt-4o", label: "gpt-4o", hint: "flagship multimodal" },
    { value: "openai/gpt-4o-mini", label: "gpt-4o-mini", hint: "cheap" },
    { value: "openai/o3", label: "o3", hint: "deep reasoning" },
    { value: "openai/o4-mini", label: "o4-mini", hint: "reasoning, cheap" },
  ],
  gemini: [
    { value: "gemini/gemini-2.5-pro", label: "gemini-2.5-pro", hint: "long context" },
    { value: "gemini/gemini-2.5-flash", label: "gemini-2.5-flash", hint: "fast" },
    { value: "gemini/gemini-1.5-pro", label: "gemini-1.5-pro", hint: "1M-token window" },
    { value: "gemini/gemini-1.5-flash", label: "gemini-1.5-flash", hint: "cheap" },
  ],
  mistral: [
    { value: "mistral/mistral-large-latest", label: "mistral-large", hint: "flagship" },
    { value: "mistral/mistral-small-latest", label: "mistral-small", hint: "fast" },
    { value: "mistral/codestral-latest", label: "codestral", hint: "code" },
  ],
  deepseek: [
    { value: "deepseek/deepseek-chat", label: "deepseek-chat", hint: "general" },
    { value: "deepseek/deepseek-reasoner", label: "deepseek-reasoner", hint: "reasoning" },
  ],
  groq: [
    { value: "groq/llama-3.3-70b-versatile", label: "llama-3.3-70b", hint: "fast hosted" },
    { value: "groq/llama-3.1-8b-instant", label: "llama-3.1-8b-instant", hint: "cheap" },
    { value: "groq/mixtral-8x7b-32768", label: "mixtral-8x7b", hint: "32k context" },
  ],
  stub: [
    { value: "stub-echo", label: "stub-echo", hint: "keyless smoke test" },
  ],
};

/** Flattened view: [{provider, ...ModelOption}, ...] */
export const ALL_MODELS: (ModelOption & { provider: Provider })[] =
  PROVIDERS.flatMap((p) =>
    MODEL_CATALOG[p.value].map((m) => ({ ...m, provider: p.value })),
  );

/** Return the option matching `value`, or undefined if none. */
export function findModelOption(
  value: string,
): (ModelOption & { provider: Provider }) | undefined {
  return ALL_MODELS.find((m) => m.value === value);
}

/** Extract provider from a `<provider>/<id>` string. Returns null
 * for legacy bare names (`gpt-4o`, `claude-sonnet-4`) since those
 * are routed by the api's _resolve_provider, not by us. */
export function providerFromValue(value: string): Provider | null {
  const slash = value.indexOf("/");
  if (slash <= 0) return null;
  const prefix = value.slice(0, slash);
  return PROVIDERS.some((p) => p.value === prefix) ? (prefix as Provider) : null;
}
