/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{js,jsx}',
  ],
  theme: {
    extend: {
      colors: {
        'legal-blue': {
          50: '#e8eef5',
          100: '#c5d4e6',
          200: '#9eb8d5',
          300: '#779cc4',
          400: '#5a87b8',
          500: '#3d72ab',
          600: '#2e619e',
          700: '#1e4d8c',
          800: '#1e3a5f',
          900: '#132540',
        }
      }
    },
  },
  plugins: [],
}
