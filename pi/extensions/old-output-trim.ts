// old-output-trim.ts — Initial Pi output truncation prototype
// Trims LLM responses that exceed a configurable token budget.

const MAX_TOKENS = 2048;

function estimateTokens(text: string): number {
  return Math.ceil(text.length / 4);
}

export function trimOutput(text: string, maxTokens = MAX_TOKENS): string {
  if (!text || !text.length) return text;
  const estimated = estimateTokens(text);
  if (estimated <= maxTokens) return text;
  const ratio = maxTokens / estimated;
  let trimmed = text.slice(0, Math.floor(text.length * ratio));
  // Avoid cutting mid-word: truncate back to the last space
  const lastSpace = trimmed.lastIndexOf(' ');
  if (lastSpace > 0) trimmed = trimmed.slice(0, lastSpace);
  return trimmed + '\n\n[truncated — output exceeded token budget]';
}
