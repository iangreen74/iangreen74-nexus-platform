// OperatorBanner - persistent header announcing the surface.
// Visually unmistakable. Cannot be styled away. Its purpose is to make
// confusion-with-customer-UI structurally impossible at a glance.

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
        platform.vaultscaler.com/engineering
      </span>
    </div>
  );
}
