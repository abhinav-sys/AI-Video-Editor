import type {

  CreatomateMode,

  CreatomateTemplatesResponse,

  HealthResponse,

  JobCreateResponse,

  JobResponse,

  RenderEngine,

  UploadResponse,

} from "./types";



const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const API_KEY = process.env.NEXT_PUBLIC_API_KEY || "dev-api-key-change-me";



const DEFAULT_TIMEOUT_MS = 120_000;



function headers(json = false): HeadersInit {

  const h: Record<string, string> = {

    "X-API-Key": API_KEY,

  };

  if (json) h["Content-Type"] = "application/json";

  return h;

}



async function request(

  url: string,

  init: RequestInit = {},

  timeoutMs = DEFAULT_TIMEOUT_MS

): Promise<Response> {

  const controller = new AbortController();

  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {

    return await fetch(url, { ...init, signal: controller.signal });

  } catch (err) {

    if (err instanceof DOMException && err.name === "AbortError") {

      throw new Error("Request timed out — is the backend running on :8000?");

    }

    throw new Error(

      err instanceof Error

        ? `Network error: ${err.message}. Is the API at ${API_URL}?`

        : "Network error talking to the API"

    );

  } finally {

    clearTimeout(timer);

  }

}



async function handle<T>(res: Response): Promise<T> {

  if (!res.ok) {

    let detail: unknown = res.statusText;

    try {

      const body = await res.json();

      detail = body.detail ?? body;

    } catch {

      /* ignore */

    }

    const message =

      typeof detail === "string"

        ? detail

        : Array.isArray(detail)

          ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ")

          : JSON.stringify(detail);

    throw new Error(message || `HTTP ${res.status}`);

  }

  return res.json() as Promise<T>;

}



export async function getHealth(): Promise<HealthResponse> {

  const res = await request(`${API_URL}/health`, {}, 8_000);

  return handle(res);

}



export async function listCreatomateTemplates(): Promise<CreatomateTemplatesResponse> {

  const res = await request(`${API_URL}/creatomate/templates`, {

    headers: headers(),

  });

  return handle(res);

}



export async function uploadFiles(

  videos: File[],

  assets: File[]

): Promise<UploadResponse> {

  const form = new FormData();

  videos.forEach((f) => form.append("videos", f));

  assets.forEach((f) => form.append("assets", f));

  const res = await request(`${API_URL}/uploads`, {

    method: "POST",

    headers: { "X-API-Key": API_KEY },

    body: form,

  });

  return handle(res);

}



export type CreateJobOptions = {

  uploadId?: string | null;

  prompt: string;

  engine?: RenderEngine;

  templateId?: string | null;

  creatomateMode?: CreatomateMode;

  videoUrl?: string | null;

};



export async function createJob(options: CreateJobOptions): Promise<JobCreateResponse> {

  const {

    uploadId,

    prompt,

    engine = "bulkcut",

    templateId,

    creatomateMode = "edit",

    videoUrl,

  } = options;

  const body: Record<string, unknown> = {

    prompt,

    engine,

    creatomate_mode: creatomateMode,

  };

  if (uploadId) body.upload_id = uploadId;

  if (templateId) body.template_id = templateId;

  if (videoUrl) body.video_url = videoUrl;



  const res = await request(`${API_URL}/jobs`, {

    method: "POST",

    headers: headers(true),

    body: JSON.stringify(body),

  });

  return handle(res);

}



export async function getJob(jobId: string): Promise<JobResponse> {

  const res = await request(

    `${API_URL}/jobs/${jobId}`,

    {

      headers: headers(),

      cache: "no-store",

    },

    15_000

  );

  return handle(res);

}



export async function cancelJob(jobId: string): Promise<JobResponse> {

  const res = await request(`${API_URL}/jobs/${jobId}/cancel`, {

    method: "POST",

    headers: headers(),

  });

  return handle(res);

}



export function downloadUrl(jobId: string): string {
  return `${API_URL}/jobs/${jobId}/download`;
}

export async function getItemTemplate(
  jobId: string,
  itemId: string
): Promise<import("./types").EditableTemplate> {
  const res = await request(`${API_URL}/jobs/${jobId}/items/${itemId}/template`, {
    headers: headers(),
  });
  return handle(res);
}

export async function patchItemTemplate(
  jobId: string,
  itemId: string,
  body: { entities: import("./types").TemplateEntity[] }
): Promise<import("./types").EditableTemplate> {
  const res = await request(`${API_URL}/jobs/${jobId}/items/${itemId}/template`, {
    method: "PATCH",
    headers: headers(true),
    body: JSON.stringify(body),
  });
  return handle(res);
}

export async function rerenderFromTemplate(
  jobId: string,
  itemId: string
): Promise<{ ok: boolean; output_path: string; occurrences: number }> {
  const res = await request(
    `${API_URL}/jobs/${jobId}/items/${itemId}/rerender`,
    {
      method: "POST",
      headers: headers(),
    },
    600_000
  );
  return handle(res);
}

/** Absolute URL for an item preview path returned by the API (relative `/jobs/...`). */

export function previewImageUrl(relativePath: string | null | undefined): string | null {

  if (!relativePath) return null;

  if (relativePath.startsWith("http")) return relativePath;

  return `${API_URL}${relativePath.startsWith("/") ? "" : "/"}${relativePath}`;

}



export { API_KEY, API_URL };

