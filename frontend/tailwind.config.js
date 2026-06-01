/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{js,jsx}',
  ],
  theme: {
    extend: {
      colors: {
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
        sans: ['Inter', 'sans-serif'],
        serif: ['"Source Serif 4"', 'serif'],
      }
    },
  },
  plugins: [],
}
