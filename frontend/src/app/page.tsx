"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { HealthChips } from "@/components/HealthChips";
import { PromptBox } from "@/components/PromptBox";
import { UploadZone } from "@/components/UploadZone";
import { createJob, getHealth, uploadFiles } from "@/lib/api";
import type { HealthResponse, RenderEngine } from "@/lib/types";

const EDIT_EXAMPLES = [
  "Replace 15 & 16 august to 15 & 16 july",
  "Replace Sydney to Melbourne",
  "Replace July with August, replace logo with logo.png",
];

type Phase = "idle" | "uploading" | "parsing";

export default function HomePage() {
  const router = useRouter();
  const [engine, setEngine] = useState<RenderEngine>("creatomate");
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
      return phase === "uploading" ? "Uploading videos…" : "Starting job…";
    }
    if (!videos.length) return "Add at least one video to continue";
    if (!prompt.trim()) return "Write an edit prompt to continue";
    if (healthError) return "Backend offline — start the API on :8000";
    if (engine === "bulkcut") {
      if (health && !health.ffmpeg) return "FFmpeg not found on the server PATH";
    } else {
      if (health && !health.creatomate) return "Set CREATOMATE_API_KEY in backend/.env";
      if (health && health.creatomate && !health.creatomate_ok) {
        return "Creatomate API key rejected — check Project Settings";
      }
      if (health && !health.ffmpeg) {
        return "FFmpeg needed for OCR frame sampling before Creatomate render";
      }
    }
    return null;
  }, [phase, videos.length, prompt, healthError, health, engine]);

  const canStart = !disabledReason;

  async function onSubmit() {
    setError(null);
    if (!canStart) return;
    setPhase("uploading");
    try {
      const upload = await uploadFiles(videos, assets);
      setPhase("parsing");
      const job = await createJob({
        uploadId: upload.upload_id,
        prompt: prompt.trim(),
        engine,
        creatomateMode: engine === "creatomate" ? "edit" : undefined,
      });
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
              Upload a clip, describe the text change, and render with either your local
              pipeline or Creatomate (OCR finds the text → cloud builds the template).
            </p>
          </div>
          <HealthChips health={health} error={healthError} />
        </header>

        <section className="engine-panel rise delay-1" aria-label="Render engine">
          <h2 className="section-label">Engine</h2>
          <div className="engine-toggle" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={engine === "bulkcut"}
              className={`engine-option ${engine === "bulkcut" ? "active" : ""}`}
              onClick={() => setEngine("bulkcut")}
            >
              <strong>My creation</strong>
              <span>Bulkcut · local FFmpeg + OCR</span>
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={engine === "creatomate"}
              className={`engine-option ${engine === "creatomate" ? "active" : ""}`}
              onClick={() => setEngine("creatomate")}
            >
              <strong>Creatomate</strong>
              <span>OCR template from your video → cloud render</span>
            </button>
          </div>
          <p className="engine-hint">
            {engine === "bulkcut"
              ? "Burns replacement text into your uploaded clips using OCR + FFmpeg."
              : "Same OCR locate step, then Creatomate builds a RenderScript template (video + covers + new text) and renders in the cloud."}
          </p>
        </section>

        <section className="stage rise delay-1">
          <UploadZone
            label="Drop your videos"
            hint="MP4, MOV, MKV, or WebM · we OCR frames then build the Creatomate template"
            accept="video/mp4,video/quicktime,video/x-matroska,video/webm,.mp4,.mov,.mkv,.webm"
            files={videos}
            onChange={setVideos}
          />
        </section>

        <section className="rise delay-2">
          <h2 className="section-label">Edit prompt</h2>
          <PromptBox value={prompt} onChange={setPrompt} />
          <div className="examples">
            {EDIT_EXAMPLES.map((ex) => (
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
          <p className="reason">
            {disabledReason ||
              `Ready · ${engine === "bulkcut" ? "Bulkcut local" : "Creatomate OCR+cloud"}`}
          </p>
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
                  ? engine === "creatomate"
                    ? "Building Creatomate job…"
                    : "Parsing…"
                  : engine === "creatomate"
                    ? "Edit with Creatomate"
                    : "Start batch"}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
