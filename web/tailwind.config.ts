import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        nike: {
          black:     '#111111',
          red:       '#E31837',
          white:     '#FFFFFF',
          gray:      '#F5F5F5',
          'dark-gray': '#757575',
          'mid-gray':  '#CCCCCC',
        },
        bml: {
          beat: '#E31837',
          meet: '#F5A623',
          lose: '#27AE60',
          nd:   '#9B9B9B',
        },
      },
      fontFamily: {
        sans: ['Inter', 'Helvetica Neue', 'Arial', 'sans-serif'],
      },
    },
  },
  plugins: [],
}

export default config
