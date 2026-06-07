/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Wasteland-themed palette: muted/desaturated, with hot accents
        wasteland: {
          50: "#f4f1ea",
          100: "#e6dfd1",
          200: "#c9bfa8",
          300: "#a89e83",
          400: "#857c61",
          500: "#665e47",
          600: "#4b4534",
          700: "#332f22",
          800: "#1f1c14",
          900: "#100e08",
        },
        rust: {
          400: "#cf6a3a",
          500: "#b25024",
          600: "#8c3d18",
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "Menlo", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};
