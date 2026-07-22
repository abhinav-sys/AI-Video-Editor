"use client";

import { useCallback, useRef, useState } from "react";

type Props = {
  label: string;
  hint?: string;
  accept: string;
  multiple?: boolean;
  files: File[];
  onChange: (files: File[]) => void;
  compact?: boolean;
};

function formatMb(size: number) {
  return `${(size / (1024 * 1024)).toFixed(2)} MB`;
}

export function UploadZone({
  label,
  hint = "Drop files or click to browse",
  accept,
  multiple = true,
  files,
  onChange,
  compact = false,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [active, setActive] = useState(false);

  const merge = useCallback(
    (incoming: FileList | null) => {
      if (!incoming?.length) return;
      const list = Array.from(incoming);
      if (!multiple) {
        onChange([list[0]]);
        return;
      }
      const seen = new Set(files.map((f) => `${f.name}:${f.size}`));
      const next = [...files];
      for (const file of list) {
        const key = `${file.name}:${file.size}`;
        if (seen.has(key)) continue;
        seen.add(key);
        next.push(file);
      }
      onChange(next);
    },
    [files, multiple, onChange]
  );

  const removeAt = (index: number) => {
    onChange(files.filter((_, i) => i !== index));
  };

  const total = files.reduce((sum, f) => sum + f.size, 0);

  return (
    <div>
      <div
        className={`drop ${active ? "active" : ""}`}
        style={compact ? { minHeight: 120, padding: "1.25rem" } : undefined}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setActive(true);
        }}
        onDragLeave={() => setActive(false)}
        onDrop={(e) => {
          e.preventDefault();
          setActive(false);
          merge(e.dataTransfer.files);
        }}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
        }}
      >
        <strong>{label}</strong>
        <div className="hint">{hint}</div>
        <input
          ref={inputRef}
          type="file"
          accept={accept}
          multiple={multiple}
          hidden
          onChange={(e) => {
            merge(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      {files.length > 0 && (
        <>
          <div className="file-chips">
            {files.map((f, i) => (
              <div className="file-chip" key={`${f.name}-${f.size}-${i}`}>
                <span title={f.name}>{f.name}</span>
                <span className="meta">{formatMb(f.size)}</span>
                <button type="button" aria-label={`Remove ${f.name}`} onClick={() => removeAt(i)}>
                  ×
                </button>
              </div>
            ))}
          </div>
          <p className="meta" style={{ marginTop: "0.55rem" }}>
            {files.length} file{files.length === 1 ? "" : "s"} · {formatMb(total)}
          </p>
        </>
      )}
    </div>
  );
}
