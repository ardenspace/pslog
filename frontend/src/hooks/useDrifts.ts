import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import type { DriftAction, DriftStatus } from '@/types/drift';

interface ListFilters {
  status?: DriftStatus;
}

export function useDrifts(projectId: string | null, filters?: ListFilters) {
  return useQuery({
    queryKey: ['projects', projectId, 'drifts', filters],
    queryFn: () => api.drifts.list(projectId!, filters).then((r) => r.data),
    enabled: !!projectId,
  });
}

export function useTransitionDriftStatus(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ driftId, action }: { driftId: string; action: DriftAction }) =>
      api.drifts.transition(projectId, driftId, { action }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ['projects', projectId, 'drifts'],
      });
    },
  });
}
