/**
 * PostCSS configuration for Tailwind CSS 4.
 *
 * Tailwind 4 ships a dedicated PostCSS plugin; all theme / content
 * configuration lives in src/app/globals.css via @theme and the
 * `content` array is auto-discovered from import graph.
 */

export default {
  plugins: {
    '@tailwindcss/postcss': {},
  },
};
