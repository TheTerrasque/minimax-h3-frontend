// Matches integrations/llm.py's FINAL_PROMPT_FENCE -- the chat system
// prompt asks the model to wrap a finalized, ready-to-use prompt in a
// fenced code block tagged with this language, so it can be pulled out
// mechanically and offered as a one-click "use this prompt" action instead
// of making the user spot it in a wall of markdown.
const FINAL_PROMPT_FENCE = "final-prompt";

const FINAL_PROMPT_RE = new RegExp("```" + FINAL_PROMPT_FENCE + "\\s*\\n([\\s\\S]*?)```", "i");

export interface ParsedChatMessage {
  /** The message with the final-prompt block (if any) removed, for markdown rendering. */
  text: string;
  /** The extracted prompt text, or null if this message didn't contain one. */
  finalPrompt: string | null;
}

export function parseChatMessage(content: string): ParsedChatMessage {
  const match = content.match(FINAL_PROMPT_RE);
  if (!match || match.index == null) {
    return { text: content, finalPrompt: null };
  }
  const finalPrompt = match[1].trim();
  const text = (content.slice(0, match.index) + content.slice(match.index + match[0].length)).trim();
  return { text, finalPrompt };
}
