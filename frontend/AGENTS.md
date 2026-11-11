<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

## shadcn 纪律

前端用 shadcn/ui（底层 Base UI，样式 `base-nova`）加 Tailwind v4（ADR-0015）。写代码守下面这些规则；视觉规范以根目录 `DESIGN.md` 为准。

- **布局**：直接写 `<div>` 加 Tailwind 类，不自写 `Stack`/`Row` 原语。同样的结构在两屏以上出现、并且能用 `GLOSSARY.md` 或 `DESIGN.md` 里的词叫出名字，才抽成项目组件。
- **颜色**：只用具名主题变量。`--her-*`、挂断红等固定色在 `src/app/globals.css` 的 `@theme inline` 里注册成 Tailwind 颜色（`bg-her-wall`、`bg-hangup`）。任意值里不准出现颜色（`[var(--…)]`、`bg-(--…)`、hex、oklch、rgb、hsl、color-mix 都不行）。新增固定色写进对比度测试（`src/theme/themeContrast.test.ts`）。酒吧（`/play/bar`）是另立的视觉世界，颜色用自己的 `BAR_INK`，不受这条管（`DESIGN.md`「酒吧」）。
- **尺寸**：`DESIGN.md` frontmatter 里有名字的值（字号层级、气泡圆角、头像等）注册成 `@theme` token（如 `text-header`、`text-meta`、`rounded-bubble`），必须用 token。间距只用 Tailwind 刻度。任意值只用于一次性的布局约束（`max-w-[min(30rem,72vw)]`、`grid-cols-[…]`、`h-[80%]`）。
- **`components/ui/` 直接改**（Own-the-Source），改处加 `// eidolon: <原因>`。`shadcn add` 不带 `--overwrite`，更新组件先看 diff。
- **调用处不改外观**：调用处的 className 只管定位和排布。外观要变就到 `ui/` 组件加 variant/prop 或改那条带条件的类；禁止 `!` important；`group-data-[…]` 盖不住的情况同样这样处理。
- **目录和命名**：项目共用件放 `components/app/`，后台的放 `components/app/admin/`，文件名用 PascalCase。各屏自己的组件放在 `conversations/`、`admin/` 等目录下。`ui/` 用 shadcn 的 kebab-case。
- **`cn`** 一律从 `@/lib/utils` 导入，`shadcn add` 生成的 `from "cn"` 当场改掉。
- **她的颜色**：用 `herVars(官方色)` 注入对话子树（覆盖 `--primary`、`--primary-foreground`、`--ring` 和 `--her-*`），不写进主题或 `:root`。浮层（Dialog、AlertDialog、DropdownMenu、Drawer、Sheet、Popover）渲染到 body，需要她的颜色时把 Content 的 `container` 指到子树里（这样也继承子树的 `.dark`），或在浮层内容上再展开一次 `herVars()`。选中状态一律中性 `bg-muted`。分享图固定浅色，用 `shareVars()` 注入（DESIGN.md「分享图」）。
- **机器检查**：不装 ESLint。`sh scripts/check-discipline.sh` 拦下 `from "cn"`（`lib/utils.ts` 除外）、任意值里的颜色（`-\[(#|oklch|rgb|hsl|color-mix|var\(--)`，以及 v4 的 `bg-(--x)` 这类颜色简写）、important 类（`!mt-2`、`hover:!bg-x`、`mt-2!`）和对 `@astryxdesign` 的引用（旧组件库，已移除），CI 前端 job 在测试之后跑它。字号、圆角任意值的界线交给代码审查。
- **shadcn CLI**：本机 npm 全局配置了 `allow-scripts`，CLI 会报 `EALLOWSCRIPTS`；用 `NPM_CONFIG_USERCONFIG` 指向一份不带它的临时 npmrc 再跑 `npx shadcn@latest add …`。

## 落地页的字体子集

落地页（`src/app/(landing)/`）的字体是按实际用字做的静态子集，自托管，和清单 `subset.json` 一起提交在 `src/app/(landing)/fonts/`（ADR-0016 修订）。前端的 Docker 构建拿不到角色配置，所以子集不在构建时生成，由维护者在本地重新生成：

```sh
node scripts/landing-fonts.mts   # 在 frontend/ 下；要有 uv，第一次运行会下载源字体
```

新增或改名角色、改了组合、招呼或开场白、改了落地页（`copy.landing`）或登录组件（`copy.login`）的文案之后都要跑，然后把 `fonts/` 下的改动一起提交。忘了的话，覆盖契约测试 `fonts/coverage.test.ts` 会失败，并列出缺的字。要收哪些字由 `fonts/fontText.ts` 决定，落地页加了新的文案来源（例如别的文案节）时改它。
