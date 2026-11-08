"use client";
// 帶回饋訊息的表單：送出中停用按鈕，完成後顯示成功或錯誤訊息。
import { useActionState } from "react";
import type { ActionState } from "@/app/actions";

export function ActionForm({
  action,
  children,
  submit,
  className = "inline",
}: {
  action: (prev: ActionState, form: FormData) => Promise<ActionState>;
  children: React.ReactNode;
  submit: string;
  className?: string;
}) {
  const [state, formAction, pending] = useActionState(action, null);
  return (
    <div>
      <form action={formAction} className={className}>
        {children}
        <button type="submit" disabled={pending}>
          {pending ? "處理中" : submit}
        </button>
      </form>
      {state && <p className={`notice ${state.ok ? "ok" : "error"}`}>{state.message}</p>}
    </div>
  );
}
