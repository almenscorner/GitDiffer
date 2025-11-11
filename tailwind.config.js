/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: [
    './_includes/**/*.html',
    './_layouts/**/*.html',
    './**/*.{html,md}',
    './assets/js/**/*.js',
    '!./node_modules/**',
    '!./_site/**'
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter var','Inter','ui-sans-serif','system-ui','-apple-system','Segoe UI','Roboto','Noto Sans','sans-serif']
      }
    }
  }
};
