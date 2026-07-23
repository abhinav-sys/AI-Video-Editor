"use client";

import type { HealthResponse } from "@/lib/types";

type Props = {
  health: HealthResponse | null;
  error: string | null;
};

function Chip({
  label,
  state,
}: {
  label: string;
  state: "ok" | "bad" | "warn" | "idle";
}) {
  return (
    <span className={`chip ${state === "idle" ? "" : state}`}>
      <span className="dot" />
      {label}
    </span>
  );
}

export function HealthChips({ health, error }: Props) {
  if (error && !health) {
    return (
      <div className="health">
        <Chip label="API offline" state="bad" />
      </div>
    );
  }
  if (!health) {
    return (
      <div className="health">
        <Chip label="Checking…" state="idle" />
      </div>
    );
  }

  return (
    <div className="health">
      <Chip label="API" state="ok" />
      <Chip label="FFmpeg" state={health.ffmpeg ? "ok" : "bad"} />
      <Chip
        label={`LLM · ${health.llm_provider}`}
        state={health.llm_ok ? "ok" : "warn"}
      />
      <Chip
        label="Creatomate"
        state={
          !health.creatomate ? "idle" : health.creatomate_ok ? "ok" : "bad"
        }
      />
    </div>
  );
}
