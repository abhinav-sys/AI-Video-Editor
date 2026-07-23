export type UploadedFileInfo = {
  filename: string;
  kind: string;
  size_bytes: number;
};

export type UploadResponse = {
  upload_id: string;
  videos: UploadedFileInfo[];
  assets: UploadedFileInfo[];
};

export type RenderEngine = "bulkcut" | "creatomate";

export type CreatomateMode = "edit" | "template" | "source";

export type JobStatus =
  | "queued"
  | "parsing"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export type ItemStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export type JobItemResponse = {
  id: string;
  original_filename: string;
  status: ItemStatus;
  progress: number;
  error: string | null;
  occurrences_replaced: number | null;
  preview_before_url: string | null;
  preview_after_url: string | null;
  started_at: string | null;
  finished_at: string | null;
};

export type JobResponse = {
  id: string;
  status: JobStatus;
  prompt: string;
  instructions_json: string | null;
  upload_id: string;
  engine: RenderEngine | string;
  error: string | null;
  progress: number;
  items: JobItemResponse[];
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  download_ready: boolean;
};

export type JobCreateResponse = {
  id: string;
  status: JobStatus;
  engine: RenderEngine | string;
};

export type HealthResponse = {
  status: string;
  ffmpeg: boolean;
  llm_provider: string;
  llm_ok: boolean;
  model: string;
  max_concurrent_renders: number;
  creatomate?: boolean;
  creatomate_ok?: boolean;
  creatomate_template_id?: string | null;
};

export type CreatomateTemplate = {
  id: string;
  name: string;
  tags: string[];
  updated_at?: string;
};

export type CreatomateTemplatesResponse = {
  configured: boolean;
  default_template_id: string | null;
  templates: CreatomateTemplate[];
};
