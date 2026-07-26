import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import jsxA11y from 'eslint-plugin-jsx-a11y'
import react from 'eslint-plugin-react'

export default [
  { ignores: ['dist/**', 'node_modules/**'] },
  {
    files: ['src/**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: { ...globals.browser, ...globals.es2022 },
    },
    settings: { react: { version: 'detect' } },
    plugins: { 'react-hooks': reactHooks, 'jsx-a11y': jsxA11y, react },
    rules: {
      ...js.configs.recommended.rules,
      // A missing `lucide-react` import shipped `<Bot />` as an undefined JSX
      // identifier and crashed add-on skill output on render. Base `no-undef`
      // does NOT see JSX element names — `react/jsx-no-undef` is the rule that
      // actually catches this, so it must stay an error.
      'react/jsx-no-undef': 'error',
      'no-undef': 'error',
      // Unused identifiers are a warning, not a build gate: the repo currently
      // carries ~890 of them and clearing that backlog is separate work.
      // They stay visible so new ones are noticed in review.
      'no-unused-vars': [
        'warn',
        {
          args: 'none',
          ignoreRestSiblings: true,
          varsIgnorePattern: '^_',
          caughtErrors: 'none',
        },
      ],
      'no-empty': ['error', { allowEmptyCatch: true }],
      'no-useless-escape': 'warn',
      'no-useless-assignment': 'off',
      'no-alert': 'warn',
      'jsx-a11y/label-has-associated-control': [
        'error',
        { assert: 'either', depth: 10 },
      ],
    },
  },
]
