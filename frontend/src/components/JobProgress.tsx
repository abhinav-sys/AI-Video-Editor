"use client";

import type { JobResponse } from "@/lib/types";

type Props = {
  job: JobResponse;
};

type ParsedInstructions = {
  replace_text?: { from: string; to: string }[];
  replace_logo?: string | null;
  watermark?: string | null;
  watermark_image?: string | null;
};

function parseInstructions(raw: string | null): ParsedInstructions | null {
  if (!raw) return null;
  try {
    return JSON.parse(raw) as ParsedInstructions;
  } catch {
    return null;
  }
}

export function JobProgress({ job }: Props) {
  const pct = Math.round(Math.min(100, job.progress));
  const parsed = parseInstructions(job.instructions_json);

  return (
    <div>
      <div className="progress-hero">
        <div className="ring-wrap">
          <div className="ring" style={{ ["--p" as string]: pct }} />
          <span className="ring-label">{pct}%</span>
        </div>
        <div>
          <div style={{ display: "flex", gap: "0.65rem", alignItems: "center" }}>
            <span className={`status-pill ${job.status}`}>{job.status}</span>
            <span className="meta">{job.items.length} video{job.items.length === 1 ? "" : "s"}</span>
          </div>
          <p className="meta" style={{ margin: "0.35rem 0 0" }}>
            {job.status === "completed"
              ? "Batch finished — download your ZIP."
              : job.status === "failed"
                ? "Batch failed — check item errors below."
                : "Rendering with bounded parallelism…"}
          </p>
        </div>
      </div>

      <div className="bar" aria-label="Overall progress">
        <span style={{ width: `${pct}%` }} />
      </div>

      {job.error && <p className="error">{job.error}</p>}

      {parsed && (
        <div className="instr-chips" aria-label="Parsed instructions">
          {(parsed.replace_text || []).map((pair, i) => (
            <span className="instr" key={`t-${i}`}>
              Text: {pair.from} → {pair.to}
            </span>
          ))}
          {parsed.replace_logo && <span className="instr">Logo: {parsed.replace_logo}</span>}
          {parsed.watermark && <span className="instr">Watermark: {parsed.watermark}</span>}
        </div>
      )}

      <div style={{ marginTop: "1rem" }}>
        {job.items.map((item) => (
          <div className="item-row" key={item.id}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: "1rem" }}>
              <strong>{item.original_filename}</strong>
              <span className={`status-pill ${item.status}`}>{item.status}</span>
            </div>
            <div className="bar" style={{ marginBottom: 0 }}>
              <span style={{ width: `${Math.min(100, item.progress)}%` }} />
            </div>
            {item.error && <p className="error">{item.error}</p>}
          </div>
        ))}
      </div>
    </div>
  );
}
