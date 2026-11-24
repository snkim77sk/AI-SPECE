import type { Metadata } from 'next';
import OriginalShell from '../components/original-shell';
import {publicEnabled} from '../lib/public-auth-service';
import './globals.css';
import './original-shell.css';

export const metadata: Metadata = {
  title: 'SINSUNG · 신성라이텍 G2B vNext',
  description: '미래예산과 전국 조명·등주 저장자료를 확인하는 승인 회원 전용 G2B 작업 공간.',
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ko"><body><OriginalShell publicMembership={publicEnabled()}>{children}</OriginalShell></body></html>;
}
