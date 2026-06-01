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
        },
        brand: {
          bg: '#F7F3EC',
          'bg-soft': '#EFE8DA',
          surface: '#FFFFFF',
          'surface-2': '#FBF8F2',
          ink: '#14253B',
          'ink-2': '#2D3F55',
          muted: '#6A7587',
          line: '#E1D9C9',
          'line-2': '#CFC4AE',
          accent: '#5A7A5C',
          'accent-2': '#426146',
          gold: '#B8965A',
          rose: '#B5604E',
          green: '#5A7A5C',
          amber: '#C28B2B',
        }
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        serif: ['"Source Serif 4"', 'Georgia', 'serif'],
      }
    },
  },
  plugins: [],
}
