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
          bg: '#F4F0E8',
          'bg-soft': '#ECE7DE',
          surface: '#FFFFFF',
          'surface-2': '#F8F6F1',
          ink: '#161817',
          'ink-2': '#343A37',
          muted: '#66706B',
          line: '#DCD7CE',
          'line-2': '#C8C1B5',
          accent: '#3157D5',
          'accent-2': '#2445B8',
          gold: '#3157D5',
          rose: '#B5604E',
          green: '#5A7A5C',
          amber: '#C28B2B',
        }
      },
      fontFamily: {
        sans: ['Manrope', 'Inter', 'sans-serif'],
        serif: ['Manrope', 'Inter', 'sans-serif'],
      }
    },
  },
  plugins: [],
}
