// Backend DriftType / DriftStatus enum wire values
export type DriftType = 'decision_not_promoted' | 'status_contradiction';
export type DriftStatus = 'open' | 'resolved' | 'ignored';

// PATCH action
export type DriftAction = 'ignore' | 'reopen';

export interface DriftSummary {
  id: string;
  type: DriftType;
  status: DriftStatus;
  branch: string;
  external_id: string | null;
  detail: string;
  opened_at: string;          // ISO datetime
  resolved_at: string | null;
}

export interface DriftListResponse {
  items: DriftSummary[];
  total: number;
}

export interface DriftStatusUpdateRequest {
  action: DriftAction;
}
