export function asksForWorkers(text: string, automatic: boolean) {
  if (/\b(?:do not|don't|dont|never|without|avoid|no)\b[^.!?]{0,80}\b(?:subagents?|agents?|workers?|delegat(?:e|ion))\b/i.test(text)) return false;
  if (/(?:^|[.!?]\s*|\b(?:please|can you|could you|would you|i want you to|you should|let's)\s+)\s*(?:use(?!\s+cases?\b)|send(?:\s+off)?|ask|run|spawn|start|create|delegate|launch)\b[^!?]{0,100}\b(?:subagents?|agents?|workers?)\b/i.test(text)) return true;
  return automatic && text.length > 500 && /\b(review|compare|research|audit|analy[sz]e|plan)\b/i.test(text);
}
