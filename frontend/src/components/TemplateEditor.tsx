"use client";

import { useEffect, useState } from "react";

import {
  getItemTemplate,
  patchItemTemplate,
  rerenderFromTemplate,
} from "@/lib/api";
import type { EditableTemplate, TemplateEntity } from "@/lib/types";

type Props = {
  jobId: string;
  itemId: string;
  filename: string;
};

export function TemplateEditor({ jobId, itemId, filename }: Props) {
  const [template, setTemplate] = useState<EditableTemplate | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [rerendering, setRerendering] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const data = await getItemTemplate(jobId, itemId);
        if (alive) {
          setTemplate(data);
          setError(null);
        }
      } catch (err) {
        if (alive) setError(err instanceof Error ? err.message : "No template");
      }
    })();
    return () => {
      alive = false;
    };
  }, [jobId, itemId]);

  function updateEntity(id: string, patch: Partial<TemplateEntity>) {
    if (!template) return;
    setTemplate({
      ...template,
      entities: template.entities.map((e) =>
        e.id === id ? { ...e, ...patch, style: { ...e.style, ...(patch.style || {}) } } : e
      ),
    });
  }

  async function onSave() {
    if (!template) return;
    setSaving(true);
    setMessage(null);
    try {
      const updated = await patchItemTemplate(jobId, itemId, {
        entities: template.entities,
      });
      setTemplate(updated);
      setMessage("Template saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function onRerender() {
    setRerendering(true);
    setMessage(null);
    try {
      await onSave();
      const res = await rerenderFromTemplate(jobId, itemId);
      setMessage(`Re-rendered (${res.occurrences} text region(s)). Refresh to see previews.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Re-render failed");
    } finally {
      setRerendering(false);
    }
  }

  if (error && !template) {
    return (
      <div className="meta" style={{ marginTop: "0.75rem" }}>
        Template: {error}
      </div>
    );
  }
  if (!template) {
    return <p className="meta">Loading editable template…</p>;
  }

  return (
    <div className="template-editor" style={{ marginTop: "1rem" }}>
      <h3 style={{ fontSize: "1.1rem", marginBottom: "0.5rem" }}>
        Editable template — {filename}
      </h3>
      <p className="meta" style={{ marginBottom: "0.75rem" }}>
        {template.video.width}×{template.video.height} · {template.video.duration.toFixed(1)}s ·{" "}
        {template.entities.length} entit{template.entities.length === 1 ? "y" : "ies"}
      </p>
      <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: "0.75rem" }}>
        {template.entities.map((ent) => (
          <li
            key={ent.id}
            style={{
              borderTop: "1px solid color-mix(in srgb, currentColor 12%, transparent)",
              paddingTop: "0.75rem",
            }}
          >
            <div className="meta">
              {ent.type} · {ent.role} · {ent.id}
              {ent.track[0]
                ? ` · ${ent.track[0].t_start.toFixed(1)}s–${ent.track[0].t_end.toFixed(1)}s`
                : ""}
            </div>
            {ent.type === "text" && (
              <label style={{ display: "block", marginTop: "0.35rem" }}>
                <span className="meta">Text</span>
                <input
                  type="text"
                  value={ent.text || ""}
                  onChange={(e) => updateEntity(ent.id, { text: e.target.value })}
                  style={{ width: "100%", marginTop: "0.25rem" }}
                />
              </label>
            )}
            {ent.style?.size != null && (
              <label style={{ display: "inline-block", marginTop: "0.35rem", marginRight: "1rem" }}>
                <span className="meta">Size</span>
                <input
                  type="number"
                  value={ent.style.size}
                  onChange={(e) =>
                    updateEntity(ent.id, {
                      style: { ...ent.style, size: Number(e.target.value) },
                    })
                  }
                  style={{ width: "5rem", marginLeft: "0.35rem" }}
                />
              </label>
            )}
            {ent.style?.color && (
              <label style={{ display: "inline-block", marginTop: "0.35rem" }}>
                <span className="meta">Color</span>
                <input
                  type="color"
                  value={ent.style.color.startsWith("#") ? ent.style.color : "#ffffff"}
                  onChange={(e) =>
                    updateEntity(ent.id, {
                      style: { ...ent.style, color: e.target.value },
                    })
                  }
                  style={{ marginLeft: "0.35rem" }}
                />
              </label>
            )}
          </li>
        ))}
      </ul>
      <div className="actions" style={{ marginTop: "1rem" }}>
        <button type="button" onClick={onSave} disabled={saving}>
          {saving ? "Saving…" : "Save template"}
        </button>
        <button type="button" onClick={onRerender} disabled={rerendering}>
          {rerendering ? "Re-rendering…" : "Re-render from template"}
        </button>
      </div>
      {message && <p className="meta" style={{ marginTop: "0.5rem" }}>{message}</p>}
      {error && <p className="error" style={{ marginTop: "0.5rem" }}>{error}</p>}
    </div>
  );
}
