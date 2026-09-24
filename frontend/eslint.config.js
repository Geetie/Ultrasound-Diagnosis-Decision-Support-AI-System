import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
  },
  {
    // 历史遗留文件的既有告警（DICOM 适配层的显式 any、挂载时拉取状态的
    // effect）。本次不重写这些文件，但把规则降级为警告以保留可见性；
    // 新增文件仍按 recommended 严格门禁，避免继续累积同类债务。
    files: [
      'src/components/dicom/**/*.{ts,tsx}',
      'src/components/diagnosis/DiagnosisConclusion.tsx',
      'src/hooks/useDicomViewer.ts',
      'src/hooks/useSession.ts',
      'src/stores/dicomStore.ts',
      'src/pages/ConsentPage.tsx',
      'src/pages/ExaminationPage.tsx',
      'src/pages/KbManagementPage.tsx',
      'src/pages/LoginPage.tsx',
      'src/pages/QcReportPage.tsx',
      'src/pages/SessionCreatePage.tsx',
    ],
    rules: {
      '@typescript-eslint/no-explicit-any': 'warn',
      'react-hooks/set-state-in-effect': 'warn',
      'react-hooks/exhaustive-deps': 'warn',
    },
  },
])
