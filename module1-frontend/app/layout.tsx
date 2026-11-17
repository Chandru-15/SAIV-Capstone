import type { Metadata } from 'next'
import ServiceWorkerRegistration from '../components/pwa/ServiceWorkerRegistration'
import './globals.css'

export const metadata: Metadata = {
  title: 'SAIV - Secure Attendance System',
  description: 'Student check-in interface with liveness detection',
  manifest: '/manifest.json',
  icons: { icon: '/icons/icon-192.png', apple: '/icons/icon-192.png' },
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body>
        <ServiceWorkerRegistration />
        {children}
      </body>
    </html>
  )
}
