import type { ReactNode } from "react";

interface InlineMarkupProps {
  text: string;
  clinicalCitations?: boolean;
  labNotes?: boolean;
}

type PatternMatch = {
  index: number;
  length: number;
  render: (key: string) => ReactNode;
};

function findEarliest(text: string, patterns: Array<(text: string) => PatternMatch | null>): PatternMatch | null {
  let best: PatternMatch | null = null;
  for (const pattern of patterns) {
    const found = pattern(text);
    if (!found) continue;
    if (best === null || found.index < best.index) {
      best = found;
    }
  }
  return best;
}

function matchRegex(
  text: string,
  regex: RegExp,
  render: (match: RegExpExecArray, key: string) => ReactNode,
): PatternMatch | null {
  const match = regex.exec(text);
  if (!match || match.index < 0) return null;
  return {
    index: match.index,
    length: match[0].length,
    render: (key) => render(match, key),
  };
}

function renderInlineSegments(
  text: string,
  options: Pick<InlineMarkupProps, "clinicalCitations" | "labNotes">,
  keyPrefix = "m",
): ReactNode[] {
  const nodes: ReactNode[] = [];
  let rest = text;
  let key = 0;

  const patterns: Array<(value: string) => PatternMatch | null> = [];

  if (options.clinicalCitations) {
    patterns.push(
      (value) => matchRegex(value, /\*\((WHO|MSF):\s*([^)]+)\)\*/, (match, nodeKey) => (
        <span key={nodeKey} className="ml-1 inline-flex items-center rounded-full border border-blue-200 bg-blue-50 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-blue-700">
          {match[1]}: {match[2]}
        </span>
      )),
      (value) => matchRegex(value, /\*\((WHO|MSF)[^)]*\)\*/, (match, nodeKey) => (
        <span key={nodeKey} className="ml-1 inline-flex items-center rounded-full border border-blue-200 bg-blue-50 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-blue-700">
          {match[1]}
        </span>
      )),
      (value) => matchRegex(value, /\(According to ([^(]+?)\s*\(KB evidence\):\s*([^)]{0,80}[^)]*)\)/, (match, nodeKey) => (
        <span key={nodeKey} className="ml-1 inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[9px] font-medium text-slate-600">
          {match[1].trim()}
        </span>
      )),
      (value) => matchRegex(value, /\*Not in KB\*/, (_match, nodeKey) => (
        <span key={nodeKey} className="ml-1 text-[10px] italic text-[hsl(var(--muted-foreground))]">
          Not in KB
        </span>
      )),
    );
  }

  if (options.labNotes) {
    patterns.push((value) => matchRegex(value, /\*\((.+?)\)\*/, (match, nodeKey) => (
      <span key={nodeKey} className="mb-1 block text-[10px] text-[hsl(var(--muted-foreground))]">
        {match[1]}
      </span>
    )));
  }

  patterns.push(
    (value) => matchRegex(value, /`([^`]+?)`/, (match, nodeKey) => (
      <code key={nodeKey} className="rounded bg-black/10 px-1 py-0.5 font-mono text-[11px]">
        {match[1]}
      </code>
    )),
    (value) => matchRegex(value, /\*\*(.+?)\*\*/, (match, nodeKey) => (
      <strong key={nodeKey}>
        {renderInlineSegments(match[1], options, `${nodeKey}-b`)}
      </strong>
    )),
    (value) => matchRegex(value, /\*([^*]+?)\*/, (match, nodeKey) => (
      <em key={nodeKey}>
        {renderInlineSegments(match[1], options, `${nodeKey}-i`)}
      </em>
    )),
  );

  while (rest) {
    const match = findEarliest(rest, patterns);
    if (!match) {
      nodes.push(rest);
      break;
    }
    if (match.index > 0) {
      nodes.push(rest.slice(0, match.index));
    }
    nodes.push(match.render(`${keyPrefix}-${key++}`));
    rest = rest.slice(match.index + match.length);
  }

  return nodes;
}

export function InlineMarkup({ text, clinicalCitations = false, labNotes = false }: InlineMarkupProps) {
  return <>{renderInlineSegments(text, { clinicalCitations, labNotes })}</>;
}
