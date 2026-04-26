// OperatorBanner - persistent header announcing the surface.
// Visually unmistakable. Cannot be styled away. Its purpose is to make
// confusion-with-customer-UI structurally impossible at a glance.

function handleSignOut() {
  // Hard navigation: backend clears ALB session cookies, then 302s to
  // Cognito /logout, which redirects back through the ALB and lands
  // the operator on Cognito's sign-in page.
  window.location.href = "/oauth2/sign-out";
}

export function OperatorBanner() {
  return (
    <div className="bg-op-surface border-b border-op-border px-4 py-2 flex items-center gap-3 text-2xs">
      <span className="font-mono font-semibold text-op-accent tracking-wide">
        OPERATOR
      </span>
      <span className="text-op-text-dim font-mono">/</span>
      <span className="font-mono text-op-text">ECHO V2</span>
      <span className="text-op-text-dim font-mono">/</span>
      <span className="text-op-text-muted">
        Internal engineering surface. Not for customer use.
      </span>
      <span className="ml-auto text-op-text-muted font-mono">
        vaultscalerlabs.com
      </span>
      <button
        type="button"
        onClick={handleSignOut}
        aria-label="Sign out"
        className="font-mono border border-op-accent text-op-accent px-2 py-0.5 hover:bg-op-accent hover:text-op-bg transition-colors"
      >
        sign out
      </button>
    </div>
  );
}
