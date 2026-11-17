/** @type {import('next').NextConfig} */
const backend = process.env.SAIV_BACKEND_INTERNAL_URL || 'http://127.0.0.1:8000'

const nextConfig = {
  output: 'standalone',
  skipTrailingSlashRedirect: true,
  async rewrites() {
    return [
      { source: '/api/v1/:path*/', destination: `${backend}/api/v1/:path*/` },
      { source: '/api/v1/:path*', destination: `${backend}/api/v1/:path*` },
    ]
  },
}

module.exports = nextConfig