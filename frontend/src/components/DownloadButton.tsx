"use client";

import { API_KEY, downloadUrl } from "@/lib/api";

type Props = {
  jobId: string;
  ready: boolean;
};

export function DownloadButton({ jobId, ready }: Props) {
  if (!ready) {
    return (
      <button className="btn" disabled>
        Preparing ZIP…
      </button>
    );
  }

  return (
    <button
      className="btn"
      type="button"
      onClick={async () => {
        try {
          const res = await fetch(downloadUrl(jobId), {
            headers: { "X-API-Key": API_KEY },
          });
          if (!res.ok) {
            alert("Download failed");
            return;
          }
          const blob = await res.blob();
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = `job-${jobId}.zip`;
          a.click();
          URL.revokeObjectURL(url);
        } catch {
          alert("Download failed — check that the backend is running.");
        }
      }}
    >
      Download ZIP
    </button>
  );
}
