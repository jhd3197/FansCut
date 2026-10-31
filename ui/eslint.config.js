import globals from 'globals';
import reactHooks from 'eslint-plugin-react-hooks';

export default [
  { ignores: ['dist/**'] },
  {
    files: ['src/**/*.{js,jsx}', 'vite.config.js', 'eslint.config.js'],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: { 'react-hooks': reactHooks },
    rules: {
      'no-undef': 'error',
      'no-unused-vars': ['error', { args: 'none', caughtErrors: 'none', varsIgnorePattern: '^[A-Z]' }],
      'no-duplicate-imports': 'error',
      ...reactHooks.configs.recommended.rules,
    },
  },
];
