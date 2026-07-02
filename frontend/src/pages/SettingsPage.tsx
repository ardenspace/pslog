import { useState } from 'react';
import { Link } from 'react-router-dom';
import axios from 'axios';
import { useAuth } from '@/hooks/useAuth';
import { ROUTES, SITE_NAME } from '@/constants';
import { USERNAME_PATTERN } from '@/types';

type Status =
  | { kind: 'idle' }
  | { kind: 'success' }
  | { kind: 'error'; message: string };

export function SettingsPage() {
  const { user, updateMe, isUpdatingMe } = useAuth();
  // 원본 effect 의 리셋 트리거(서버 username 변경 시 입력값 동기화)를 effect 없이
  // 파생으로 재현: draft 는 생성 시점 서버 값(base)이 유지되는 동안만 유효.
  const [usernameDraft, setUsernameDraft] = useState<{
    value: string;
    base: string;
  } | null>(null);
  const [status, setStatus] = useState<Status>({ kind: 'idle' });

  const serverUsername = user?.username ?? '';
  const username =
    usernameDraft && usernameDraft.base === serverUsername
      ? usernameDraft.value
      : serverUsername;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setStatus({ kind: 'idle' });

    const trimmed = username.trim();
    if (trimmed && !USERNAME_PATTERN.test(trimmed)) {
      setStatus({
        kind: 'error',
        message: '소문자/숫자/_/- 만 가능, 2~32자.',
      });
      return;
    }

    try {
      await updateMe({ username: trimmed === '' ? null : trimmed });
      setStatus({ kind: 'success' });
    } catch (err) {
      let message = '저장에 실패했습니다.';
      if (axios.isAxiosError(err)) {
        if (err.response?.status === 409) {
          message = '이미 사용 중인 username 입니다.';
        } else if (err.response?.status === 422) {
          message = 'username 형식이 올바르지 않습니다.';
        }
      }
      setStatus({ kind: 'error', message });
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-brand-cream p-4 sm:p-6 md:p-8">
      <div className="bg-white/60 backdrop-blur-xl rounded-3xl shadow-xl border border-brand-blue/10 p-6 sm:p-10 w-full max-w-md mx-auto">
        <div className="mb-6 sm:mb-8 text-center">
          <span className="font-bold text-3xl sm:text-4xl text-brand-blue border-b-4 border-brand-orange pb-1">
            {SITE_NAME}
          </span>
        </div>
        <h1 className="font-bold text-xl sm:text-2xl mb-2 text-brand-blue">설정</h1>
        <p className="text-sm text-brand-blue/60 mb-6 sm:mb-8">
          PLAN.md 의 <code className="font-mono text-xs">@username</code> 멘션이
          본인 task 로 매핑됩니다.
        </p>

        <form onSubmit={handleSubmit}>
          <div className="space-y-5">
            <div>
              <label
                htmlFor="email"
                className="font-bold text-sm block mb-1.5 text-brand-blue"
              >
                이메일
              </label>
              <input
                id="email"
                type="email"
                value={user?.email ?? ''}
                readOnly
                className="bg-white/40 border border-brand-blue/20 rounded-xl w-full px-4 py-2.5 text-sm text-brand-blue/70"
              />
            </div>

            <div>
              <label
                htmlFor="username"
                className="font-bold text-sm block mb-1.5 text-brand-blue"
              >
                Username
              </label>
              <input
                id="username"
                type="text"
                value={username}
                onChange={(e) =>
                  setUsernameDraft({
                    value: e.target.value.toLowerCase(),
                    base: serverUsername,
                  })
                }
                placeholder="arden"
                pattern="[a-z0-9_-]{2,32}"
                className="bg-white/80 border border-brand-blue/20 rounded-xl w-full px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-blue/20 transition-all text-brand-blue font-mono"
              />
              <p className="text-xs text-brand-blue/60 mt-1.5">
                소문자/숫자/_/-, 2~32자. 비워두면 매핑 해제.
              </p>
            </div>

            {status.kind === 'error' && (
              <p className="text-sm font-bold text-brand-orange border border-brand-orange/30 bg-brand-orange/10 rounded-xl px-4 py-3">
                {status.message}
              </p>
            )}
            {status.kind === 'success' && (
              <p className="text-sm font-bold text-brand-blue border border-brand-blue/30 bg-brand-blue/10 rounded-xl px-4 py-3">
                저장됨.
              </p>
            )}
          </div>

          <div className="flex flex-col gap-4 mt-8">
            <button
              type="submit"
              disabled={isUpdatingMe}
              className="w-full bg-brand-blue text-white rounded-xl font-bold py-3 hover:bg-brand-neon hover:text-brand-blue transition-colors shadow-sm disabled:opacity-50"
            >
              {isUpdatingMe ? '저장 중...' : '저장'}
            </button>

            <Link
              to={ROUTES.DASHBOARD}
              className="text-center text-sm text-brand-blue/70 font-bold hover:text-brand-orange transition-colors"
            >
              대시보드로 돌아가기
            </Link>
          </div>
        </form>
      </div>
    </div>
  );
}
