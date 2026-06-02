/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: {
          primary:   '#060a10',
          secondary: '#0d1117',
          tertiary:  '#161b22',
          elevated:  '#1c2230',
        },
        border:   '#21262d',
        txt: {
          primary:   '#e6edf3',
          secondary: '#8b949e',
          muted:     '#3d4451',
        },
        accent:  '#58a6ff',
        green:   '#3fb950',
        red:     '#f85149',
        yellow:  '#d29922',
        purple:  '#a78bfa',
      },
      fontFamily: {
        mono: ["'JetBrains Mono'", 'Fira Code', 'monospace'],
        sans: ["'Inter'", 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
