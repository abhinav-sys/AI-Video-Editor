"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { DownloadButton } from "@/components/DownloadButton";
import { getJob } from "@/lib/api";
import type { JobResponse } from "@/lib/types";

export default function DownloadPage() {
  const params = useParams<{ id: string }>();
  const [job, setJob] = useState<JobResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getJob(params.id)
      .then(setJob)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed"));
  }, [params.id]);

  const readyCount = job?.items.filter((i) => i.status === "completed").length ?? 0;

  return (
    <main>
      <p className="meta" style={{ marginBottom: "1rem" }}>
        <Link href={`/jobs/${params.id}`}>← Back to progress</Link>
      </p>
      <header className="rise" style={{ marginBottom: "1.5rem" }}>
        <h1 className="brand" style={{ fontSize: "clamp(2rem, 5vw, 2.8rem)" }}>
          Download
        </h1>
        <p className="lede">Your edited videos are packaged as a single ZIP.</p>
      </header>
      <section className="rise delay-1">
        {error && <p className="error">{error}</p>}
        {job && (
          <>
            <p className="meta" style={{ marginBottom: "1rem" }}>
              Job <code>{job.id}</code> · {readyCount} file{readyCount === 1 ? "" : "s"} ready
            </p>
            <DownloadButton jobId={job.id} ready={job.download_ready} />
          </>
        )}
      </section>
    </main>
  );
}
