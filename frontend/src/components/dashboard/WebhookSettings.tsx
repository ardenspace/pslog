/**
 * Discord webhook URL 편집 + 리포트 전송 (owner 전용, 사이드바 하단).
 * 편집 상태는 페이지 소유 — 데스크톱/모바일 사이드바 두 인스턴스가 공유한다.
 */
export interface WebhookSettingsProps {
  webhookUrl: string;
  isEditing: boolean;
  isSaving: boolean;
  isSendingReport: boolean;
  onChange: (value: string) => void;
  onSave: () => void;
  onSendReport: () => void;
}

export function WebhookSettings({
  webhookUrl,
  isEditing,
  isSaving,
  isSendingReport,
  onChange,
  onSave,
  onSendReport,
}: WebhookSettingsProps) {
  return (
    <div className="space-y-2">
      <div className="space-y-1">
        <label className="text-[10px] sm:text-xs font-medium text-brand-blue/70">
          Discord Webhook URL
        </label>
        <input
          type="url"
          placeholder="https://discord.com/api/webhooks/..."
          value={webhookUrl}
          onChange={(e) => onChange(e.target.value)}
          className="w-full text-xs px-2 py-1.5 border border-brand-blue/20 rounded-lg focus:border-brand-blue focus:ring-2 focus:ring-brand-blue/20 focus:outline-none bg-white/50 backdrop-blur-sm"
        />
        {isEditing && (
          <button
            className="text-xs font-bold px-2 py-1.5 border border-brand-neon bg-brand-neon text-brand-coffee hover:brightness-110 rounded-lg transition-all w-full shadow-sm"
            disabled={isSaving}
            onClick={onSave}
          >
            {isSaving ? '저장 중...' : '저장'}
          </button>
        )}
      </div>
      <button
        className="text-xs sm:text-sm text-brand-blue/70 hover:text-brand-blue flex items-center justify-center gap-1 px-2 py-2 rounded-xl border border-dashed border-brand-blue/30 hover:border-brand-blue hover:bg-brand-sky/20 transition-all font-medium w-full md:w-full"
        disabled={isSendingReport}
        onClick={onSendReport}
      >
        <span>{isSendingReport ? '전송 중...' : 'Discord 리포트'}</span>
      </button>
    </div>
  );
}
