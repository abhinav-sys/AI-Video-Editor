"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { DownloadButton } from "@/components/DownloadButton";
import { JobProgress } from "@/components/JobProgress";
import { cancelJob, getJob } from "@/lib/api";
import type { JobResponse } from "@/lib/types";

const TERMINAL = new Set(["completed", "failed", "cancelled"]);

export default function JobPage() {
  const params = useParams<{ id: string }>();
  const jobId = params.id;
  const [job, setJob] = useState<JobResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let delay = 1200;

    async function tick() {
      try {
        const data = await getJob(jobId);
        if (!alive) return;
        setJob(data);
        setError(null);
        if (!TERMINAL.has(data.status)) {
          delay = Math.min(2500, delay + 200);
          timer = setTimeout(tick, delay);
        }
      } catch (err) {
        if (!alive) return;
        setError(err instanceof Error ? err.message : "Failed to load job");
        delay = Math.min(4000, delay + 400);
        timer = setTimeout(tick, delay);
      }
    }

    tick();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [jobId]);

  const running = job && !TERMINAL.has(job.status);

  return (
    <main>
      <p className="meta" style={{ marginBottom: "1rem" }}>
        <Link href="/">← New batch</Link>
      </p>
      <header className="rise" style={{ marginBottom: "1.5rem" }}>
        <h1 className="brand" style={{ fontSize: "clamp(2rem, 5vw, 2.8rem)" }}>
          Rendering
        </h1>
        <p className="lede">
          Progress updates live. Cancel anytime while items are still queued or running.
        </p>
      </header>

      <section className="rise delay-1">
        {!job && !error && <p className="meta">Loading job…</p>}
        {error && <p className="error">{error}</p>}
        {job && <JobProgress job={job} />}
        {job && (
          <div className="actions" style={{ marginTop: "1.25rem" }}>
            <DownloadButton jobId={job.id} ready={job.download_ready} />
            {running && (
              <button
                className="btn danger"
                type="button"
                disabled={cancelling}
                onClick={async () => {
                  setCancelling(true);
                  try {
                    const updated = await cancelJob(job.id);
                    setJob(updated);
                  } catch (err) {
                    setError(err instanceof Error ? err.message : "Cancel failed");
                  } finally {
                    setCancelling(false);
                  }
                }}
              >
                {cancelling ? "Cancelling…" : "Cancel"}
              </button>
            )}
            {job.download_ready && (
              <Link className="btn ghost" href={`/jobs/${job.id}/download`} style={{ display: "inline-block" }}>
                Download page
              </Link>
            )}
          </div>
        )}
      </section>
    </main>
  );
}
