// old-output-trim.ts — Initial Pi output truncation prototype
// Trims LLM responses that exceed a configurable token budget.

const MAX_TOKENS = 2048;

function estimateTokens(text: string): number {
  return Math.ceil(text.length / 4);
}

export function trimOutput(text: string, maxTokens = MAX_TOKENS): string {
  const estimated = estimateTokens(text);
  if (estimated <= maxTokens) return text;
  const ratio = maxTokens / estimated;
  const trimmed = text.slice(0, Math.floor(text.length * ratio));
  return trimmed + '\n\n[truncated — output exceeded token budget]';
}
