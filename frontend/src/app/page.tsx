"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { HealthChips } from "@/components/HealthChips";
import { PromptBox } from "@/components/PromptBox";
import { UploadZone } from "@/components/UploadZone";
import { createJob, getHealth, uploadFiles } from "@/lib/api";
import type { HealthResponse } from "@/lib/types";

const EXAMPLES = [
  "Replace July with August, replace logo with logo.png, add watermark bottom-right",
  "Add watermark center and replace logo with logo.png",
  "Replace Draft with Final",
];

type Phase = "idle" | "uploading" | "parsing";

export default function HomePage() {
  const router = useRouter();
  const [videos, setVideos] = useState<File[]>([]);
  const [assets, setAssets] = useState<File[]>([]);
  const [prompt, setPrompt] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [assetsOpen, setAssetsOpen] = useState(false);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    async function ping() {
      try {
        const data = await getHealth();
        if (!alive) return;
        setHealth(data);
        setHealthError(null);
      } catch (err) {
        if (!alive) return;
        setHealth(null);
        setHealthError(err instanceof Error ? err.message : "API offline");
      }
    }
    ping();
    const id = setInterval(ping, 12_000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const disabledReason = useMemo(() => {
    if (phase !== "idle") {
      return phase === "uploading" ? "Uploading videos…" : "Parsing prompt…";
    }
    if (!videos.length) return "Add at least one video to continue";
    if (!prompt.trim()) return "Write an edit prompt to continue";
    if (healthError) return "Backend offline — start the API on :8000";
    if (health && !health.ffmpeg) return "FFmpeg not found on the server PATH";
    return null;
  }, [phase, videos.length, prompt, healthError, health]);

  const canStart = !disabledReason;

  async function onSubmit() {
    setError(null);
    if (!canStart) return;
    setPhase("uploading");
    try {
      const upload = await uploadFiles(videos, assets);
      setPhase("parsing");
      const job = await createJob(upload.upload_id, prompt.trim());
      router.push(`/jobs/${job.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
      setPhase("idle");
    }
  }

  return (
    <>
      <main>
        <header className="topbar rise">
          <div>
            <h1 className="brand">Bulkcut</h1>
            <p className="lede">
              Describe edits in plain language. We parse them into structured instructions and render
              every video in parallel.
            </p>
          </div>
          <HealthChips health={health} error={healthError} />
        </header>

        <section className="stage rise delay-1">
          <UploadZone
            label="Drop your videos"
            hint="MP4, MOV, MKV, or WebM · multiple files welcome"
            accept="video/mp4,video/quicktime,video/x-matroska,video/webm,.mp4,.mov,.mkv,.webm"
            files={videos}
            onChange={setVideos}
          />
        </section>

        <section className="rise delay-2">
          <h2 className="section-label">Edit prompt</h2>
          <PromptBox value={prompt} onChange={setPrompt} />
          <div className="examples">
            {EXAMPLES.map((ex) => (
              <button key={ex} type="button" className="example" onClick={() => setPrompt(ex)}>
                {ex.length > 48 ? `${ex.slice(0, 48)}…` : ex}
              </button>
            ))}
          </div>

          <button
            type="button"
            className="assets-toggle"
            onClick={() => setAssetsOpen((v) => !v)}
            aria-expanded={assetsOpen}
          >
            {assetsOpen ? "▾" : "▸"} Logo & watermark assets
            {assets.length > 0 ? ` (${assets.length})` : ""}
          </button>
          {assetsOpen && (
            <UploadZone
              label="Optional assets"
              hint="PNG / JPG / WebP — name logo files with “logo” for auto-detect"
              accept="image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp"
              files={assets}
              onChange={setAssets}
              compact
            />
          )}

          {error && <p className="error">{error}</p>}
        </section>
      </main>

      <div className="dock">
        <div className="dock-inner">
          <p className="reason">{disabledReason || "Ready to start batch"}</p>
          <div className="actions">
            <button
              className="btn ghost"
              type="button"
              disabled={phase !== "idle"}
              onClick={() => {
                setVideos([]);
                setAssets([]);
                setPrompt("");
                setError(null);
              }}
            >
              Clear
            </button>
            <button className="btn" disabled={!canStart} onClick={onSubmit}>
              {phase === "uploading"
                ? "Uploading…"
                : phase === "parsing"
                  ? "Parsing…"
                  : "Start batch"}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
