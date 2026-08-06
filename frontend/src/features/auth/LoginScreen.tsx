import { useConfig } from "../../api/queries";

// Login itself is entirely Django/allauth's job (OIDC redirect, or the
// classic /accounts/login/ form for admin-created accounts) -- this screen
// just points the browser at the right URL. See ARCHITECTURE.md "Backend
// apps" / "accounts" for why there's no self-signup form here.
export function LoginScreen() {
  const config = useConfig();

  return (
    <section className="login-screen">
      <div className="login-card">
        <h1>Minimax H3 Generator</h1>
        <p>Invite-only video generation for the MiniMax H3 ComfyUI workflows.</p>

        {config.isLoading && <p className="hint">Checking login options…</p>}
        {config.isError && <p className="error">Couldn't reach the server. Try reloading.</p>}

        {config.data?.oidc_enabled && config.data.oidc_login_url && (
          <a className="button button-primary" href={config.data.oidc_login_url}>
            Log in with {config.data.oidc_provider_name}
          </a>
        )}

        <p className="hint">
          No account yet? You'll need an invite link from an admin — opening it
          logs you in and creates one.
        </p>
        <p className="hint">
          <a href="/accounts/login/">Have a password-based account instead?</a>
        </p>
      </div>
    </section>
  );
}
