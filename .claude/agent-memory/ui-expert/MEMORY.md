# UI Expert Agent Memory

## Project Overview
- B2B task management app: **pslog**
- Stack: React 19, TypeScript 5+, Tailwind CSS, shadcn/ui, TanStack Query, Zustand
- Package manager: bun
- Frontend root: `/Users/arden/Documents/dev/pslog/frontend/src/`

## Layout Architecture
- `DashboardPage.tsx`: `h-screen flex flex-col overflow-hidden` root, fixed header + flex body
- Body uses `flex flex-1 overflow-hidden` with sidebar (`w-56`) + main (`flex-1 overflow-auto`)
- Header height context: `h-screen` on root + `flex-shrink-0` header = natural fit, no calc needed
- Main page: `/Users/arden/Documents/dev/pslog/frontend/src/pages/DashboardPage.tsx`

## Neo-Brutalism Design Tokens (canonical — applied across all components)

### Borders & Shadows
- All borders: `border-2 border-black` (never rounded, always sharp)
- Card shadow: `shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]`
- Modal shadow: `shadow-[8px_8px_0px_0px_rgba(0,0,0,1)]`
- Small shadow: `shadow-[2px_2px_0px_0px_rgba(0,0,0,1)]`

### Buttons
- Primary: `bg-black text-white border-2 border-black font-bold hover:bg-yellow-400 hover:text-black transition-colors shadow-[2px_2px_0px_0px_rgba(0,0,0,1)]`
- Full-width primary (auth): `shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]`
- Ghost/cancel: `border-2 border-black font-bold hover:bg-yellow-100 transition-colors`
- Delete: `bg-red-500 text-white border-2 border-black font-bold hover:bg-red-600 transition-colors shadow-[2px_2px_0px_0px_rgba(0,0,0,1)]`
- All: `disabled:opacity-50`
- Use plain `<button>` not shadcn Button in modals and standalone contexts

### Inputs / Textarea / Select
- `border-2 border-black rounded-none w-full px-3 py-2 text-sm focus:outline-none focus:shadow-[2px_2px_0px_0px_rgba(0,0,0,1)]`
- Disabled: `disabled:bg-gray-100 disabled:cursor-not-allowed`
- Textarea: add `min-h-[80px] resize-none`
- Labels: `font-bold text-sm block mb-1`
- Use plain `<input>`, `<textarea>`, `<select>` — not shadcn Input

### Modals
- Backdrop: `fixed inset-0 bg-black/60 flex items-center justify-center z-50`
- Box: `bg-white border-2 border-black shadow-[8px_8px_0px_0px_rgba(0,0,0,1)] p-6 w-full max-w-md`
- Title: `font-black text-lg mb-4`

### Auth Pages
- Page bg: `min-h-screen flex items-center justify-center bg-yellow-50 p-4`
- Card: `border-2 border-black shadow-[8px_8px_0px_0px_rgba(0,0,0,1)] bg-white p-8 w-full max-w-md`
- Brand logo: `font-black text-3xl border-b-4 border-yellow-400 pb-1`
- Error block: `text-sm font-bold text-red-600 border-2 border-red-500 bg-red-50 px-3 py-2`
- Links: `font-bold underline hover:text-yellow-600`

### Typography
- Section/page headings: `font-black`
- Column titles: `font-black text-sm uppercase tracking-wide`
- Count badges: `bg-black text-white text-xs px-2 py-0.5 font-bold`
- Read-only badge: `text-xs bg-yellow-200 text-black border-2 border-black font-bold px-2 py-1`

## Color Palette
- Accent/selected: `yellow-400`
- Hover tint: `yellow-50`, `yellow-100`
- Page bg: `yellow-50`
- Borders: always `black`
- Text: `black` > `foreground` > `muted-foreground`

## Component Patterns

### Sidebar (DashboardPage)
- Selected item: `bg-yellow-400 border-2 border-black shadow-[2px_2px_0px_0px_rgba(0,0,0,1)] font-bold`
- Unselected: `border-2 border-transparent text-muted-foreground hover:bg-yellow-50 hover:border-black`
- Header shadow: `shadow-[0px_2px_0px_0px_rgba(0,0,0,1)] border-b-2 border-black`
- View toggle active: `bg-black text-white`, inactive: `hover:bg-yellow-100`
- Dashed add button: `border-2 border-dashed border-muted-foreground hover:border-black hover:bg-yellow-50`

### Kanban Columns
- todo: `bg-white`, doing: `bg-yellow-50`, done: `bg-yellow-50`, blocked: `bg-red-50`
- All: `border-2 border-black shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]`

### TaskCard
- `bg-white border-2 border-black shadow-[2px_2px_0px_0px_rgba(0,0,0,1)] hover:shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] hover:-translate-x-0.5 hover:-translate-y-0.5 transition-all cursor-pointer p-3`

### WeekView Columns
- Normal: `bg-white border-2 border-black shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]`
- Today: `bg-yellow-400 border-2 border-black shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]`
- Mini task card in today column: `bg-yellow-200 border-2 border-black`

## shadcn/ui Usage Policy
- Replace Card/CardContent with plain `<div>`
- Replace shadcn Button with plain `<button>` in modals/board/week
- Replace shadcn Input with plain `<input>`
- Remove Label, CardHeader, CardFooter, CardTitle etc — use semantic HTML + Tailwind
- Keep shadcn imports only where explicitly in use (ghost logout in sidebar)
- NEVER use native `<select>` or `<input type="date">` — use CustomSelect and DatePicker instead

## Custom Form Components
- `CustomSelect`: `frontend/src/components/ui/CustomSelect.tsx` — replaces all `<select>`
  - Trigger: `border-2 border-black w-full flex items-center justify-between bg-white`
  - Dropdown: `absolute top-full border-2 border-black border-t-0 bg-white shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] max-h-48 overflow-y-auto`
  - Selected option: `bg-yellow-400 font-bold`, hover: `hover:bg-yellow-50`
  - Disabled: `bg-gray-100 cursor-not-allowed`
  - Chevron rotates 180deg when open via `transition-transform`
- `DatePicker`: `frontend/src/components/ui/DatePicker.tsx` — replaces all `<input type="date">`
  - Uses react-day-picker v9 + date-fns v4 (installed)
  - Import: `import 'react-day-picker/style.css'` required in DatePicker.tsx
  - v9 classNames keys (NOT v8): `month_caption`, `button_previous`, `button_next`, `day_button`, `weekdays`, `weekday`, `weeks`, `week`, `month_grid` — NOT the deprecated v8 names
  - v9 modifier keys: `selected`, `today`, `outside`, `disabled` (flat, not prefixed with `day_`)
  - Popup: `border-2 border-black bg-white shadow-[8px_8px_0px_0px_rgba(0,0,0,1)] p-3`
  - Clear button: `border-2 border-black text-xs font-bold py-1 hover:bg-yellow-100`
  - Display format: `yyyy. M. d` (Korean convention)

## Key File Paths
- Pages: `frontend/src/pages/LoginPage.tsx`, `RegisterPage.tsx`
- Board: `frontend/src/components/board/BoardHeader.tsx`, `KanbanColumn.tsx`, `TaskCard.tsx`, `CreateTaskModal.tsx`, `TaskDetailModal.tsx`
- Workspace: `frontend/src/components/workspace/CreateProjectModal.tsx`
- Week: `frontend/src/components/week/WeekView.tsx`, `WeekColumn.tsx`
- UI components: `frontend/src/components/ui/CustomSelect.tsx`, `DatePicker.tsx`
