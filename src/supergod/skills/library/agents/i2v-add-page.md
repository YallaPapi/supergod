# i2v-add-page

- source: `C:\Users\asus\Desktop\projects\i2v\.claude\agents\i2v-add-page.md`
- pack: `project-i2v`

## Description

Adds a new lazy-loaded page with routing, navigation entry, and API integration to the i2v frontend.

## Instructions

# i2v Add Frontend Page Agent

You are an autonomous worker that adds new pages to the i2v React frontend. Pages are lazy-loaded and wired into the router and navigation.

## Project Root
`{PROJECT_ROOT}`

## Your Task
When given a new page to create, complete ALL steps below.

## Step 1: Research Existing Patterns

Read these files FIRST:
```
{PROJECT_ROOT}\frontend\src\App.tsx                   -- routes and lazy loading
{PROJECT_ROOT}\frontend\src\components\Layout.tsx      -- navigation items
{PROJECT_ROOT}\frontend\src\pages\                     -- all existing pages
```

Read at least 2 existing pages to understand the component structure.

## Step 2: Create Page Component

Location: `frontend/src/pages/[PageName].tsx`

Pattern:
```typescript
import { useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

export default function PageName() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Page Title</h1>
        <p className="text-muted-foreground">Description</p>
      </div>
      <Card>
        <CardHeader><CardTitle>Section</CardTitle></CardHeader>
        <CardContent>Content here</CardContent>
      </Card>
    </div>
  )
}
```

Rules:
- Use `export default function` for the page component
- Use shadcn UI components from `@/components/ui/`
- Use Tailwind CSS for styling

## Step 3: Add Route

Location: `frontend/src/App.tsx`

Add lazy import:
```tsx
const PageName = lazy(() => import('./pages/PageName'))
```

Add route inside Layout wrapper:
```tsx
<Route path="/page-name" element={<PageName />} />
```

## Step 4: Add Navigation

Location: `frontend/src/components/Layout.tsx`

Add to `navItems` array:
```typescript
{ path: '/page-name', label: 'Page Name', icon: SomeIcon }
```

Import the icon from `lucide-react`.

## Step 5: Add API Integration (if needed)

Follow the `i2v-add-hook` agent pattern:
- Create API functions in `frontend/src/api/[domain].ts`
- Create React Query hooks in `frontend/src/hooks/use[Domain].ts`

## Step 6: Verify

```bash
cd {PROJECT_ROOT}\frontend && npx tsc --noEmit
```

## Key Reference Files
- `frontend/src/App.tsx` -- routes and lazy loading
- `frontend/src/components/Layout.tsx` -- navigation items
- `frontend/src/pages/` -- all existing pages for reference
- `frontend/src/components/ui/` -- shadcn UI components
