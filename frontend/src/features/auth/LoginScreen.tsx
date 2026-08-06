import { useConfig } from "../../api/queries";

// Login/signup are entirely Django/allauth's job (OIDC redirect, the classic
// /accounts/login/ form, or the invite-gated /accounts/signup/ form reached
// via an /invite/<token>/ link) -- this screen just points the browser at
// the right URL. See ARCHITECTURE.md "Backend apps" / "accounts" for why
// there's no self-service signup form here.
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
          No {config.data?.oidc_provider_name ?? "OIDC"} account? You'll need an
          invite link from an admin — opening it lets you set up a
          password-based account.
        </p>
        <p className="hint">
          <a href="/accounts/login/">Have a password-based account instead?</a>
        </p>
      </div>
    </section>
  );
}
