"use client";

import { useParams } from "next/navigation";

import { useDirectory } from "@/components/conversations/ConversationDirectory";
import { GroupChatView } from "@/components/conversations/GroupChatView";
import { Spinner } from "@/components/ui/spinner";
import { copy } from "@/i18n";

export default function GroupChatPage() {
  const { groupId } = useParams<{ groupId: string }>();
  const { account, failure } = useDirectory();
  if (account) return <GroupChatView key={account.id + "/" + groupId} id={groupId} userId={account.id} />;
  return (
    <div className="flex h-full items-center justify-center p-6">
      {failure ? <p role="alert">{failure.message}</p> : <Spinner aria-label={copy.groupChat.loading} />}
    </div>
  );
}
