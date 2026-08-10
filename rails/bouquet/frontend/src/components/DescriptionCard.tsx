import { useState } from "react";
import { Markdown } from "./ui";

// The description output: the 720px image beside the copy in a box, with a Copy
// button (clipboard) and an optional Delete. Used for a fresh result and for a
// saved description opened from History.
export function DescriptionCard({ imageUrl, text, guidance, onDelete }: {
  imageUrl: string | null;
  text: string;
  guidance?: string;
  onDelete?: () => void;
}) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch { /* clipboard blocked; no-op */ }
  };

  return (
    <div className="bq-desc">
      {imageUrl && <img className="bq-desc-img" src={imageUrl} alt="the bouquet" />}
      <div className="bq-desc-main">
        {guidance && <div className="bq-desc-guidance"><b>Direction:</b> {guidance}</div>}
        <div className="bq-desc-box"><Markdown text={text} /></div>
        <div className="bq-desc-actions">
          <button className="bq-btn bq-btn-primary" onClick={copy}>
            {copied ? "Copied ✓" : "Copy description"}
          </button>
          {onDelete && <button className="bq-btn" onClick={onDelete}>Delete</button>}
        </div>
      </div>
    </div>
  );
}
