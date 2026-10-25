// The Ethereum diamond, marking data that comes from Ethereum mainnet. The standard mark's facets, lightened
// so it reads on the dark panels.

export function EthLogo({ size = 18 }: { size?: number }) {
  return (
    <svg className="eth-logo" width={size * 0.62} height={size} viewBox="0 0 256 417" role="img" aria-label="Ethereum">
      <path fill="#c9cdf5" d="M127.961 0l-2.795 9.5v275.668l2.795 2.79 127.962-75.638z" />
      <path fill="#f3f4ff" d="M127.962 0L0 212.32l127.962 75.639V154.158z" />
      <path fill="#c9cdf5" d="M127.961 312.187l-1.575 1.92v98.199l1.575 4.6L256 236.587z" />
      <path fill="#f3f4ff" d="M127.962 416.905v-104.72L0 236.585z" />
      <path fill="#9aa2ea" d="M127.961 287.958l127.96-75.637-127.96-58.162z" />
      <path fill="#dfe2fb" d="M0 212.32l127.96 75.638v-133.8z" />
    </svg>
  );
}
