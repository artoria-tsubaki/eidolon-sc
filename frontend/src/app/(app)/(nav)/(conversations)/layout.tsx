"use client";

import { useParams } from "next/navigation";
import type { ReactNode } from "react";

import { PageShell } from "@/components/app/PageShell";
import { useWide } from "@/components/conversations/ConversationDirectory";
import { ConversationList } from "@/components/conversations/ConversationList";
import { GroupChatDirectory } from "@/components/conversations/GroupChatDirectory";
import { useSavedSize } from "@/components/conversations/useSavedSize";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { useHydrated } from "@/hooks/useHydrated";
import { copy } from "@/i18n";
import { LIST_WIDTH } from "@/lib/panelSizes";
import { cn } from "@/lib/utils";

/**
 * 对话列表和对话共用的双栏外壳（DESIGN.md「Layout」）。
 * - 宽屏：左栏是列表，默认 320px，拖右边的线在 240–480px 之间调宽窄（shadcn Resizable，按像素；
 *   窗口变化时保持像素宽，记在这台设备上）；右栏是这一路由的内容（对话，或者没选角色时的一句提示）；
 *   换角色时列表不动。上限是定值而不是窗口的几成：最宽 480，最窄的宽屏（768px）上对话栏还有将近 300px。
 * - 窄屏：两级页面。对话列表页只显示列表，进了对话只显示对话（对话页首有返回键）。
 * 右栏在宽窄屏之间始终是同一个位置上的同一块，窗口跨过 768px 时对话（和通话）不会重新挂载。
 * 水合前还不知道窗口宽窄（useWide 先当宽屏），先按 CSS 断点排出同样的两栏，窄屏只露这一级页面的那一栏，
 * 免得手机上先闪一下宽屏双栏；水合后才换成可拖动的 Resizable。水合前的列表宽度取 <head> 里的脚本写好的
 * --list-width（上次拖到的宽度），免得先按默认宽度画出来再跳。
 * 角色和摘要由导航外壳（上一层 layout 的 DirectoryProvider）载入一次，列表和对话从那里取。
 */
export default function ConversationsLayout({ children }: { children: ReactNode }) {
  const wide = useWide();
  const hydrated = useHydrated();
  const { characterId, groupId } = useParams<{ characterId?: string; groupId?: string }>();
  const hasDetail = Boolean(characterId || groupId);
  const list = <ConversationList selectedId={characterId ?? null} selectedGroup={groupId ?? null} />;

  return (
    <GroupChatDirectory>
      <PageShell width="full" isScrollable={false} isInShell>
        {hydrated ? (
          <ResizablePanelGroup orientation="horizontal" className="min-h-0 flex-1">
            {wide && (
              <ListPanel>
                <nav aria-label={copy.conversationList.title} className="h-full">
                  {list}
                </nav>
              </ListPanel>
            )}
            {/* 两栏之间的细线（列表栏的 border-r），也是拖动调宽窄的地方。 */}
            {wide && <ResizableHandle aria-label={copy.conversationList.resize} />}
            <ResizablePanel id="content" className="min-w-0">
              <div className="relative h-full">{wide || hasDetail ? children : list}</div>
            </ResizablePanel>
          </ResizablePanelGroup>
        ) : (
          // 和上面宽屏的样子一样：上次拖到的宽度（没拖过是 320px）的列表、1px 的线、右栏占满其余；窄屏只露一栏。
          <div className="flex min-h-0 flex-1 overflow-hidden">
            <nav
              aria-label={copy.conversationList.title}
              className={cn("h-full w-(--list-width,20rem) shrink-0 max-md:w-full", hasDetail && "max-md:hidden")}
            >
              {list}
            </nav>
            <div className="w-px shrink-0 bg-border max-md:hidden" />
            <div className={cn("relative h-full min-w-0 flex-1", !hasDetail && "max-md:hidden")}>{children}</div>
          </div>
        )}
      </PageShell>
    </GroupChatDirectory>
  );
}

/** 宽屏左边的列表栏，和宽屏一起挂上、卸下，每次挂上按上次拖到的宽度排（useSavedSize）。 */
function ListPanel({ children }: { children: ReactNode }) {
  const { defaultSize, onResize } = useSavedSize("list", LIST_WIDTH.initial);
  return (
    <ResizablePanel
      id="list"
      onResize={onResize}
      defaultSize={defaultSize}
      minSize={LIST_WIDTH.min}
      maxSize={LIST_WIDTH.max}
      groupResizeBehavior="preserve-pixel-size"
    >
      {children}
    </ResizablePanel>
  );
}
