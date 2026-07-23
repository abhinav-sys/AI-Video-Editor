"use client";

import { useEffect, useState } from "react";

import type { JobResponse } from "@/lib/types";
import { API_KEY, previewImageUrl } from "@/lib/api";

type Props = {
  job: JobResponse;
};

type ParsedInstructions = {
  replace_text?: { from: string; to: string }[];
  replace_logo?: string | null;
  watermark?: string | null;
  watermark_image?: string | null;
  // Creatomate
  mode?: string;
  template_id?: string | null;
  text_primary?: string;
  text_secondary?: string;
  modifications?: Record<string, unknown>;
  edits?: {
    replace_text?: { from: string; to: string }[];
    replace_logo?: string | null;
    watermark?: string | null;
  };
};

function parseInstructions(raw: string | null): ParsedInstructions | null {
  if (!raw) return null;
  try {
    return JSON.parse(raw) as ParsedInstructions;
  } catch {
    return null;
  }
}

function AuthedPreview({
  relativePath,
  alt,
}: {
  relativePath: string | null;
  alt: string;
}) {
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    const url = previewImageUrl(relativePath);
    if (!url) {
      setSrc(null);
      return;
    }
    let objectUrl: string | null = null;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(url, { headers: { "X-API-Key": API_KEY } });
        if (!res.ok) return;
        const blob = await res.blob();
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      } catch {
        /* ignore */
      }
    })();
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [relativePath]);

  if (!relativePath) return <div className="compare-empty">No frame</div>;
  if (!src) return <div className="compare-empty">Loading…</div>;
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={src} alt={alt} />;
}

export function JobProgress({ job }: Props) {
  const pct = Math.round(Math.min(100, job.progress));
  const parsed = parseInstructions(job.instructions_json);
  const isCreatomate = job.engine === "creatomate";

  return (
    <div>
      <div className="progress-hero">
        <div className="ring-wrap">
          <div className="ring" style={{ ["--p" as string]: pct }} />
          <span className="ring-label">{pct}%</span>
        </div>
        <div>
          <div style={{ display: "flex", gap: "0.65rem", alignItems: "center", flexWrap: "wrap" }}>
            <span className={`status-pill ${job.status}`}>{job.status}</span>
            <span className="instr">{isCreatomate ? "Creatomate API" : "Bulkcut local"}</span>
            <span className="meta">{job.items.length} item{job.items.length === 1 ? "" : "s"}</span>
          </div>
          <p className="meta" style={{ margin: "0.35rem 0 0" }}>
            {job.status === "completed"
              ? "Batch finished — download your ZIP."
              : job.status === "failed"
                ? "Batch failed — check item errors below."
                : isCreatomate && job.progress < 25
                  ? "OCR locating text on your video (this can take a few minutes)…"
                  : isCreatomate
                    ? "Creatomate is rendering in the cloud…"
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
          {(parsed.replace_text || parsed.edits?.replace_text || []).map((pair, i) => (
            <span className="instr" key={`t-${i}`}>
              Text: {(pair as { from?: string; from_?: string }).from ||
                (pair as { from_?: string }).from_}{" "}
              → {pair.to}
            </span>
          ))}
          {(parsed.replace_logo || parsed.edits?.replace_logo) && (
            <span className="instr">Logo: {parsed.replace_logo || parsed.edits?.replace_logo}</span>
          )}
          {(parsed.watermark || parsed.edits?.watermark) && (
            <span className="instr">Watermark: {parsed.watermark || parsed.edits?.watermark}</span>
          )}
          {parsed.text_primary && <span className="instr">Text-1: {parsed.text_primary}</span>}
          {parsed.text_secondary && <span className="instr">Text-2: {parsed.text_secondary}</span>}
          {parsed.template_id && (
            <span className="instr">Template: {parsed.template_id.slice(0, 8)}…</span>
          )}
          {parsed.mode && <span className="instr">Mode: {parsed.mode}</span>}
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
            {typeof item.occurrences_replaced === "number" && item.occurrences_replaced > 0 && (
              <p className="meta" style={{ margin: "0.4rem 0 0" }}>
                Replaced {item.occurrences_replaced} occurrence
                {item.occurrences_replaced === 1 ? "" : "s"}
              </p>
            )}
            {item.error && <p className="error">{item.error}</p>}
            {(item.preview_before_url || item.preview_after_url) && (
              <div className="compare-grid" aria-label="Before and after preview">
                <figure>
                  <figcaption>Current</figcaption>
                  <AuthedPreview relativePath={item.preview_before_url} alt="Before edit" />
                </figure>
                <figure>
                  <figcaption>Edited</figcaption>
                  <AuthedPreview relativePath={item.preview_after_url} alt="After edit" />
                </figure>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
