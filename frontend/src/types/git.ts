export interface GitSettings {
  git_repo_url: string | null;
  git_default_branch: string;
  plan_path: string;
  handoff_dir: string;
  last_synced_commit_sha: string | null;
  has_webhook_secret: boolean;
  has_github_pat: boolean;
  public_webhook_url: string;
  // Phase 6 — Discord 알림 상태
  discord_enabled: boolean;
  discord_disabled_at: string | null;       // ISO datetime
  discord_consecutive_failures: number;
  // handoff 누락 알림 스킵 브랜치 (쉼표/줄바꿈 split, main 은 자동 스킵 — 입력 불필요)
  handoff_skip_branches: string;
}

export interface GitSettingsUpdate {
  git_repo_url?: string | null;
  git_default_branch?: string | null;
  plan_path?: string | null;
  handoff_dir?: string | null;
  handoff_skip_branches?: string | null;
  github_pat?: string | null;  // 평문 입력 — 즉시 backend Fernet encrypt
}

export interface WebhookRegisterResponse {
  webhook_id: number;
  was_existing: boolean;
  public_webhook_url: string;
}

export interface HandoffSummary {
  id: string;
  branch: string;
  author_git_login: string;
  commit_sha: string;
  pushed_at: string;  // ISO datetime
  parsed_tasks_count: number;
}

export interface ReprocessResponse {
  event_id: string;
  status: string;
}

export interface GitEventSummary {
  id: string;
  branch: string;
  head_commit_sha: string;
  pusher: string;
  received_at: string;
  processed_at: string | null;
  error: string | null;
}
