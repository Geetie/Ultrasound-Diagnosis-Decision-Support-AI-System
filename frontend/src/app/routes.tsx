import { lazy, Suspense } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import AppLayout from '@/components/layout/AppLayout';
import AuthGuard from '@/components/common/AuthGuard';
import LoadingOverlay from '@/components/common/LoadingOverlay';

const ExaminationPage = lazy(() => import('@/pages/ExaminationPage'));
const SessionCreatePage = lazy(() => import('@/pages/SessionCreatePage'));
const DiagnosisPage = lazy(() => import('@/pages/DiagnosisPage'));
const SettingsPage = lazy(() => import('@/pages/SettingsPage'));
const LoginPage = lazy(() => import('@/pages/LoginPage'));
const QcReportPage = lazy(() => import('@/pages/QcReportPage'));
const KbManagementPage = lazy(() => import('@/pages/KbManagementPage'));
const ConsentPage = lazy(() => import('@/pages/ConsentPage'));
const FeedbackPage = lazy(() => import('@/pages/FeedbackPage'));
const ReportPage = lazy(() => import('@/pages/ReportPage'));

function PageSuspense({ children }: { children: React.ReactNode }) {
  return (
    <Suspense fallback={<LoadingOverlay visible tip="加载中..." />}>
      {children}
    </Suspense>
  );
}

export default function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<PageSuspense><LoginPage /></PageSuspense>} />
      <Route element={<AuthGuard><AppLayout /></AuthGuard>}>
        <Route path="/" element={<PageSuspense><ExaminationPage /></PageSuspense>} />
        <Route path="/session/new" element={<PageSuspense><SessionCreatePage /></PageSuspense>} />
        <Route path="/diagnosis" element={<PageSuspense><DiagnosisPage /></PageSuspense>} />
        <Route path="/settings" element={<PageSuspense><SettingsPage /></PageSuspense>} />
        <Route path="/qc-report" element={<PageSuspense><QcReportPage /></PageSuspense>} />
        <Route path="/kb-management" element={<PageSuspense><KbManagementPage /></PageSuspense>} />
        <Route path="/consent" element={<PageSuspense><ConsentPage /></PageSuspense>} />
        <Route path="/feedback" element={<PageSuspense><FeedbackPage /></PageSuspense>} />
        <Route path="/report" element={<PageSuspense><ReportPage /></PageSuspense>} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
