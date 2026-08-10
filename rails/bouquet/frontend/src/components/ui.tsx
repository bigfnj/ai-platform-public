import React from "react";
import { marked } from "marked";

export function Spinner() { return <span className="bq-spin" />; }

export function Card({ children, style, className }: {
  children: React.ReactNode; style?: React.CSSProperties; className?: string;
}) {
  return <div className={"bq-card" + (className ? " " + className : "")} style={style}>{children}</div>;
}

// Render trusted Markdown (our own backend's report text) to HTML. marked is
// synchronous here; the content is model-authored prose, rendered inside .bq-md.
export function Markdown({ text }: { text: string }) {
  const html = marked.parse(text || "", { async: false }) as string;
  return <div className="bq-md" dangerouslySetInnerHTML={{ __html: html }} />;
}

const CONF_CLASS: Record<string, string> = { high: "hi", medium: "mid", low: "lo" };

export function ConfidenceBadge({ level }: { level: string }) {
  const c = CONF_CLASS[level] || "mid";
  return <span className={"bq-conf bq-conf-" + c}>{level}</span>;
}
