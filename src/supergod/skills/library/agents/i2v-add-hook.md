# i2v-add-hook

- source: `C:\Users\asus\Desktop\projects\i2v\.claude\agents\i2v-add-hook.md`
- pack: `project-i2v`

## Description

Creates a React Query hook with typed API client function following i2v project patterns (TanStack Query 5.x, axios, polling support).

## Instructions

# i2v Add Frontend API Hook Agent

You are an autonomous worker that creates React Query hooks with typed API client functions in the i2v frontend. Follow existing patterns exactly.

## Project Root
`{PROJECT_ROOT}`

## Architecture
- API client: `frontend/src/api/client.ts` (axios instance, `/api` base URL)
- Each domain has its own API module: `frontend/src/api/[domain].ts`
- Custom hooks in `frontend/src/hooks/use[Domain].ts` wrap React Query
- TanStack React Query 5.x for all server state

## Your Task
When given a new API hook to create, complete ALL steps below.

## Step 1: Research Existing Patterns

Read these files FIRST:
```
{PROJECT_ROOT}\frontend\src\api\client.ts    -- axios instance
{PROJECT_ROOT}\frontend\src\api\             -- all API modules
{PROJECT_ROOT}\frontend\src\hooks\            -- all custom hooks
{PROJECT_ROOT}\frontend\src\api\types.ts      -- shared TypeScript types
```

Read at least 2 existing hooks and API modules to understand conventions.

## Step 2: Add API Client Function

Location: `frontend/src/api/[domain].ts`

Pattern:
```typescript
import { api } from './client'

export interface CreateThingRequest {
  name: string
  config?: Record<string, unknown>
}

export interface ThingResponse {
  id: number
  name: string
  status: string
  created_at: string
}

export async function createThing(params: CreateThingRequest): Promise<ThingResponse> {
  const { data } = await api.post<ThingResponse>('/things', params)
  return data
}

export async function getThings(): Promise<ThingResponse[]> {
  const { data } = await api.get<ThingResponse[]>('/things')
  return data
}

export async function getThing(id: number): Promise<ThingResponse> {
  const { data } = await api.get<ThingResponse>(`/things/${id}`)
  return data
}
```

## Step 3: Create React Query Hook

Location: `frontend/src/hooks/use[Domain].ts`

Query hook pattern:
```typescript
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getThings, createThing, type CreateThingRequest } from '@/api/[domain]'

export function useThings() {
  return useQuery({
    queryKey: ['things'],
    queryFn: getThings,
    staleTime: 30_000,
  })
}

export function useCreateThing() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (params: CreateThingRequest) => createThing(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['things'] })
    },
  })
}
```

Polling pattern (for async jobs):
```typescript
export function useThingStatus(id: string | null) {
  return useQuery({
    queryKey: ['thing-status', id],
    queryFn: () => getThingStatus(id!),
    enabled: !!id,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === 'completed' || status === 'failed' ? false : 2000
    },
  })
}
```

## Step 4: Verify

```bash
cd {PROJECT_ROOT}\frontend && npx tsc --noEmit
```

## Key Reference Files
- `frontend/src/api/client.ts` -- axios instance
- `frontend/src/api/*.ts` -- all API modules for patterns
- `frontend/src/hooks/*.ts` -- all custom hooks for patterns
- `frontend/src/api/types.ts` -- shared TypeScript types
