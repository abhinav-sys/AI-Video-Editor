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
  started_at: string | null;
  finished_at: string | null;
};

export type JobResponse = {
  id: string;
  status: JobStatus;
  prompt: string;
  instructions_json: string | null;
  upload_id: string;
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
};

export type HealthResponse = {
  status: string;
  ffmpeg: boolean;
  llm_provider: string;
  llm_ok: boolean;
  model: string;
  max_concurrent_renders: number;
};
