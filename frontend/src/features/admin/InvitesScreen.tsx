import { useState, type FormEvent } from "react";
import { useCreateInvite, useDeleteInvite, useInvites } from "../../api/queries";
import type { Invite } from "../../api/types";

function inviteStatus(invite: Invite): { label: string; className: string } {
  if (invite.is_redeemed) return { label: "Redeemed", className: "invite-status-redeemed" };
  if (invite.is_expired) return { label: "Expired", className: "invite-status-expired" };
  return { label: "Active", className: "invite-status-active" };
}

function inviteUrl(token: string): string {
  return `${window.location.origin}/invite/${token}/`;
}

export function InvitesScreen() {
  const invites = useInvites();
  const createInvite = useCreateInvite();
  const deleteInvite = useDeleteInvite();

  const [email, setEmail] = useState("");
  const [note, setNote] = useState("");
  const [expiresInDays, setExpiresInDays] = useState("");
  const [copiedId, setCopiedId] = useState<number | null>(null);

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    await createInvite.mutateAsync({
      email: email.trim() || undefined,
      note: note.trim() || undefined,
      expiresInDays: expiresInDays ? Number(expiresInDays) : undefined,
    });
    setEmail("");
    setNote("");
    setExpiresInDays("");
  }

  async function handleCopy(invite: Invite) {
    await navigator.clipboard.writeText(inviteUrl(invite.token));
    setCopiedId(invite.id);
    setTimeout(() => setCopiedId((id) => (id === invite.id ? null : id)), 2000);
  }

  return (
    <div className="admin-tab-panel">
      <h1>Invites</h1>
      <p className="hint">
        There's no open signup — share an invite link with anyone who should get an account.
      </p>

      <form onSubmit={handleCreate} className="invite-form">
        <label className="toolbar-control">
          <span>Email (optional)</span>
          <input
            type="text"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="Anyone with the link, or lock to one address"
          />
        </label>
        <label className="toolbar-control">
          <span>Note (optional)</span>
          <input
            type="text"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="e.g. for Alex, GPU testing"
            maxLength={200}
          />
        </label>
        <label className="toolbar-control">
          <span>Expires</span>
          <select value={expiresInDays} onChange={(e) => setExpiresInDays(e.target.value)}>
            <option value="">Never</option>
            <option value="7">7 days</option>
            <option value="30">30 days</option>
          </select>
        </label>
        <button type="submit" className="button-primary" disabled={createInvite.isPending}>
          {createInvite.isPending ? "Creating…" : "Create invite"}
        </button>
      </form>
      {createInvite.isError && <p className="error">Couldn't create that invite. Try again.</p>}

      {invites.isLoading && <p className="hint">Loading…</p>}
      {invites.isError && <p className="error">Couldn't load invites.</p>}
      {invites.data?.length === 0 && (
        <p className="empty-state">No invites yet — create one above to get started.</p>
      )}

      <ul className="invite-list">
        {invites.data?.map((invite) => {
          const status = inviteStatus(invite);
          return (
            <li key={invite.id} className="invite-row">
              <div className="invite-row-main">
                <span className={`invite-status ${status.className}`}>{status.label}</span>
                <span className="invite-email">{invite.email || "(any email)"}</span>
                <span className="hint invite-meta">
                  Created {new Date(invite.created_at).toLocaleDateString()}
                  {invite.created_by && ` by ${invite.created_by}`}
                  {invite.expires_at && ` · expires ${new Date(invite.expires_at).toLocaleDateString()}`}
                  {invite.redeemed_by && ` · redeemed by ${invite.redeemed_by}`}
                  {invite.note && ` · note: ${invite.note}`}
                </span>
              </div>
              <div className="invite-row-actions">
                <button type="button" onClick={() => handleCopy(invite)}>
                  {copiedId === invite.id ? "Copied!" : "Copy link"}
                </button>
                <button
                  type="button"
                  onClick={() => deleteInvite.mutate(invite.id)}
                  disabled={deleteInvite.isPending}
                  title={
                    invite.is_redeemed
                      ? "Deleting an already-redeemed invite only removes this record -- it doesn't affect the account it created."
                      : undefined
                  }
                >
                  {invite.is_redeemed ? "Remove" : "Revoke"}
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
